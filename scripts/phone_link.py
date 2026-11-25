"""scripts/phone_link.py — 1-click phone link for the running Mark-LVII.

Asks the running app to open its Cloudflare Quick Tunnel, checks the public
/pwa page answers 200, then prints the URL, a fresh 5-minute pairing PIN and a
terminal QR code. Run it on the same computer as Jarvis.

    python scripts/phone_link.py          # print link + PIN + QR
    python scripts/phone_link.py --stop   # close the public tunnel
"""
from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOCAL = "https://127.0.0.1:8000"


def _local_ctx() -> ssl.SSLContext:
    """Loopback only: the app's own certificate is self-signed."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def local_api(path: str, body: dict | None = None, timeout: float = 90.0) -> dict:
    req = Request(
        LOCAL + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    with urlopen(req, timeout=timeout, context=_local_ctx()) as resp:
        return json.load(resp)


def probe_public(url: str, attempts: int = 20, delay_s: float = 3.0) -> int:
    """HTTP status of the public page with normal certificate checking.
    A new tunnel name takes a few seconds to resolve."""
    status = 0
    for _ in range(attempts):
        try:
            with urlopen(Request(url, headers={"User-Agent": "phone-link-check"}), timeout=15) as resp:
                return resp.status
        except HTTPError as exc:
            status = exc.code
        except (URLError, OSError):
            status = 0
        time.sleep(delay_s)
    return status


def ascii_qr(data: str) -> str:
    import io

    import qrcode

    qr = qrcode.QRCode(border=2)
    qr.add_data(data)
    qr.make(fit=True)
    out = io.StringIO()
    qr.print_ascii(out=out, invert=True)
    return out.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Open the phone tunnel and show the pairing QR.")
    parser.add_argument("--stop", action="store_true", help="Close the public tunnel")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    try:
        if args.stop:
            local_api("/api/android/tunnel", {"action": "stop"})
            print("Tunnel closed.")
            return 0
        tun = local_api("/api/android/tunnel", {"action": "start"}).get("cloudflare_tunnel", {})
        if not tun.get("running"):
            print(f"Tunnel did not start: {tun.get('error') or 'unknown error'}")
            return 1
        status = probe_public(tun["pwa_url"])
        info = local_api("/api/android/qr?mode=tunnel&fresh=true")
    except (URLError, OSError) as exc:
        print(f"Mark-LVII is not answering on {LOCAL} — start it first. ({exc})")
        return 1

    print(f"Provider : {tun.get('provider')}")
    print(f"Phone URL: {info['pwa_url']}   (HTTP {status})")
    print(f"PIN      : {info['pin']}   (valid 5 minutes, one use)")
    print(f"1-tap    : {info['pairing_url']}")
    print(ascii_qr(info["pairing_url"]))
    return 0 if status == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
