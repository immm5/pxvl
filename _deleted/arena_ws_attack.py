import argparse
import asyncio
import contextlib
import json
import os
import sys
from urllib.parse import urlencode

import websockets
from websockets.exceptions import InvalidStatusCode


WS_BASE_URL = "wss://pixelvalley.farm/api/arena/ws"
ORIGIN = "https://pixelvalley.farm"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Connect Pixelvalley Arena WebSocket and send event.")
    parser.add_argument("--ws-url", type=str, default=None, help="Override URL WSS penuh. Jika diisi, token/runId diabaikan.")
    parser.add_argument("--monster-index", type=int, default=None, help="Helper mode: kirim attack ke monster index ini.")
    parser.add_argument("--payload", type=str, default=None, help='JSON payload sekali kirim, contoh: {"type":"attack","monsterIndex":1}')
    parser.add_argument("--interactive", action="store_true", help="Mode manual: ketik JSON berkali-kali setelah konek.")
    parser.add_argument("--read-count", type=int, default=3, help="Jumlah pesan balasan untuk mode sekali-kirim (default: 3).")
    return parser.parse_args()


def first_env(*keys: str) -> str | None:
    for key in keys:
        value = os.getenv(key)
        if value and value.strip():
            return value.strip()
    return None


def build_cookie_header(raw_cookie: str | None, cf_clearance: str | None) -> str | None:
    if raw_cookie:
        # If user passes full cookie string "a=1; b=2", use it directly.
        if "=" in raw_cookie:
            return raw_cookie
        return f"cf_clearance={raw_cookie}"
    if cf_clearance:
        return f"cf_clearance={cf_clearance}"
    return None


async def receive_loop(ws: websockets.WebSocketClientProtocol) -> None:
    try:
        async for message in ws:
            print(f"Recv: {message}")
    except websockets.ConnectionClosed:
        pass


async def interactive_loop(ws: websockets.WebSocketClientProtocol) -> int:
    print('Terhubung. Ketik JSON lalu Enter untuk kirim. Ketik "exit" untuk keluar.')
    recv_task = asyncio.create_task(receive_loop(ws))
    try:
        while True:
            line = await asyncio.to_thread(input, "json> ")
            line = line.strip()

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
            await ws.send(message)
            print(f"Sent: {message}")
    finally:
        recv_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await recv_task

    return 0


async def run(args: argparse.Namespace) -> int:
    load_dotenv()

    token = first_env("PIXELVALLEY_TOKEN", "TOKEN")
    run_id = first_env("PIXELVALLEY_RUN_ID", "RUN_ID")
    cf_clearance = first_env("PIXELVALLEY_CF_CLEARANCE", "CF_CLEARANCE", "cf_clearance")
    raw_cookie = first_env("PIXELVALLEY_COOKIE", "COOKIE")

    ws_url = args.ws_url or first_env("PIXELVALLEY_WS_URL")
    if not ws_url:
        if not token:
            print("Error: PIXELVALLEY_TOKEN tidak ditemukan di .env / environment.")
            return 1
        if not run_id:
            print("Error: PIXELVALLEY_RUN_ID tidak ditemukan di .env / environment.")
            return 1
        query = urlencode({"token": token, "runId": run_id})
        ws_url = f"{WS_BASE_URL}?{query}"

    headers = [
        ("Cache-Control", "no-cache"),
        ("Pragma", "no-cache"),
        ("Accept-Language", "en-US,en;q=0.9"),
        ("Referer", "https://pixelvalley.farm/"),
    ]
    cookie_header = build_cookie_header(raw_cookie, cf_clearance)
    if cookie_header:
        headers.append(("Cookie", cookie_header))

    interactive_mode = args.interactive or (args.payload is None and args.monster_index is None)

    try:
        async with websockets.connect(
            ws_url,
            origin=ORIGIN,
            extra_headers=headers,
            user_agent_header=USER_AGENT,
            open_timeout=30,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            if interactive_mode:
                return await interactive_loop(ws)

            if args.payload:
                try:
                    payload = json.loads(args.payload)
                except json.JSONDecodeError as exc:
                    print(f"Error: --payload bukan JSON valid: {exc}")
                    return 1
            else:
                payload = {"type": "attack", "monsterIndex": args.monster_index}

            message = json.dumps(payload, separators=(",", ":"))
            await ws.send(message)
            print(f"Sent: {message}")

            for _ in range(args.read_count):
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=5)
                    print(f"Recv: {message}")
                except asyncio.TimeoutError:
                    print("Recv timeout: tidak ada pesan baru.")
                    break

        return 0
    except InvalidStatusCode as exc:
        print(f"WebSocket rejected (HTTP {exc.status_code}).")
        if not cookie_header:
            print("Cookie tidak diset. Tambahkan PIXELVALLEY_CF_CLEARANCE atau PIXELVALLEY_COOKIE di .env.")
        headers_resp = getattr(exc, "headers", None)
        if headers_resp:
            server = headers_resp.get("Server")
            cf_ray = headers_resp.get("CF-RAY")
            if server:
                print(f"Server: {server}")
            if cf_ray:
                print(f"CF-RAY: {cf_ray}")
        print("Cek token, runId/ws-url, cookie, dan pastikan nilainya masih fresh dari browser session aktif.")
        return 1
    except websockets.ConnectionClosedError as exc:
        print(f"Connection closed: code={exc.code}, reason={exc.reason}")
        return 1
    except OSError as exc:
        print(f"Network error: {exc}")
        return 1


def main() -> int:
    args = parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
