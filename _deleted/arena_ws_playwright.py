import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import Error, TimeoutError, sync_playwright


WS_BASE_URL = "wss://pixelvalley.farm/api/arena/ws"
SITE_URL = "https://pixelvalley.farm/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)

WS_BRIDGE_JS = r"""
(() => {
  if (window.__pvWsInstalled) return;
  window.__pvWsInstalled = true;
  window.__pvWs = null;

  window.__pvConnect = (url) => {
    return new Promise((resolve, reject) => {
      let done = false;
      const ws = new WebSocket(url);
      window.__pvWs = ws;

      const finish = (ok, err) => {
        if (done) return;
        done = true;
        if (ok) resolve(true);
        else reject(err || new Error("unknown ws error"));
      };

      ws.addEventListener("open", () => {
        console.log("[PV_WS_OPEN]");
        finish(true);
      });
      ws.addEventListener("message", (ev) => {
        console.log("[PV_WS_RECV] " + String(ev.data));
      });
      ws.addEventListener("error", () => {
        console.log("[PV_WS_ERROR]");
      });
      ws.addEventListener("close", (ev) => {
        console.log("[PV_WS_CLOSE] code=" + ev.code + " reason=" + (ev.reason || ""));
      });

      setTimeout(() => finish(false, new Error("ws open timeout")), 20000);
    });
  };

  window.__pvSend = (raw) => {
    if (!window.__pvWs || window.__pvWs.readyState !== WebSocket.OPEN) {
      return { ok: false, error: "socket not open" };
    }
    window.__pvWs.send(raw);
    return { ok: true };
  };
})();
"""


def load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()

            if value and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]

            if key and key not in os.environ:
                os.environ[key] = value


def first_env(*keys: str) -> str | None:
    for key in keys:
        value = os.getenv(key)
        if value and value.strip():
            return value.strip()
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manual WebSocket sender via browser context (Playwright).")
    parser.add_argument("--ws-url", type=str, default=None, help="URL WSS penuh. Jika kosong, build dari token+runId.")
    parser.add_argument("--site-url", type=str, default=SITE_URL, help="URL site untuk buka halaman awal.")
    parser.add_argument("--profile-dir", type=str, default=".pw-profile", help="Folder profile persistent browser.")
    parser.add_argument("--headless", action="store_true", help="Jalankan browser headless.")
    parser.add_argument("--channel", type=str, default=None, help='Opsional channel browser, contoh: "chrome".')
    return parser.parse_args()


def build_ws_url(arg_ws_url: str | None) -> str | None:
    ws_url = arg_ws_url or first_env("PIXELVALLEY_WS_URL")
    if ws_url:
        return ws_url

    token = first_env("PIXELVALLEY_TOKEN", "TOKEN")
    run_id = first_env("PIXELVALLEY_RUN_ID", "RUN_ID")
    if not token or not run_id:
        return None
    query = urlencode({"token": token, "runId": run_id})
    return f"{WS_BASE_URL}?{query}"


def main() -> int:
    load_dotenv()
    args = parse_args()
    ws_url = build_ws_url(args.ws_url)
    if not ws_url:
        print("Error: ws-url tidak ada. Isi PIXELVALLEY_WS_URL atau PIXELVALLEY_TOKEN + PIXELVALLEY_RUN_ID.")
        return 1

    profile_path = str(Path(args.profile_dir).resolve())

    with sync_playwright() as p:
        try:
            context = p.chromium.launch_persistent_context(
                user_data_dir=profile_path,
                headless=args.headless,
                channel=args.channel,
                viewport={"width": 1280, "height": 900},
                locale="en-US",
                user_agent=USER_AGENT,
            )
        except Error as exc:
            print(f"Gagal launch browser: {exc}")
            return 1

        page = context.pages[0] if context.pages else context.new_page()

        def on_console(msg) -> None:
            text = msg.text
            if text.startswith("[PV_WS_"):
                print(text)

        page.on("console", on_console)

        try:
            page.goto(args.site_url, wait_until="domcontentloaded", timeout=60000)
        except TimeoutError:
            print("Warning: buka halaman timeout, lanjut tetap mencoba.")

        print("Browser terbuka. Login / selesaikan challenge Cloudflare jika muncul.")
        input("Jika halaman sudah siap, tekan Enter di terminal untuk connect WSS...")

        try:
            page.evaluate(WS_BRIDGE_JS)
            page.evaluate("window.__pvConnect(arguments[0])", ws_url)
            print("WebSocket connected. Ketik JSON dan Enter. Ketik 'exit' untuk keluar.")
        except Error as exc:
            print(f"Gagal connect WSS dari browser context: {exc}")
            context.close()
            return 1

        while True:
            try:
                line = input("json> ").strip()
            except (EOFError, KeyboardInterrupt):
                line = "exit"

            if not line:
                continue
            if line.lower() in {"exit", "quit"}:
                break

            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"JSON invalid: {exc}")
                continue

            message = json.dumps(parsed, separators=(",", ":"))
            try:
                result = page.evaluate("window.__pvSend(arguments[0])", message)
            except Error as exc:
                print(f"Kirim gagal: {exc}")
                continue

            if not result.get("ok"):
                print(f"Kirim gagal: {result.get('error')}")
            else:
                print(f"Sent: {message}")

        context.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
