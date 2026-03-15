import json
import os
import sys
import urllib.error
import urllib.request


API_URL = "https://pixelvalley.farm/api/market/sell"


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


def main() -> int:
    load_dotenv()
    token = os.getenv("PIXELVALLEY_TOKEN")
    if not token:
        print("Error: PIXELVALLEY_TOKEN tidak ditemukan. Tambahkan ke .env atau environment variable.")
        return 1

    payload = {
        "gold": 0,
        "silver": 1,
        "bronze": 0,
    }

    headers = {
        "sec-ch-ua-full-version-list": '"Not:A-Brand";v="99.0.0.0", "Google Chrome";v="145.0.7632.160", "Chromium";v="145.0.7632.160"',
        "sec-ch-ua-platform": '"Windows"',
        "Authorization": f"Bearer {token}",
        "Referer": "https://pixelvalley.farm/",
        "sec-ch-ua": '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
        "sec-ch-ua-bitness": '"64"',
        "sec-ch-ua-model": '""',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-arch": '"x86"',
        "sec-ch-ua-full-version": '"145.0.7632.160"',
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
        "sec-ch-ua-platform-version": '"19.0.0"',
    }

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(API_URL, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
            print(f"Status: {response.status}")
            print(body)
            return 0
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP Error: {exc.code}")
        print(body)
        return 1
    except urllib.error.URLError as exc:
        print(f"Network Error: {exc.reason}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
