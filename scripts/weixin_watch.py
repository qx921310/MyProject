#!/usr/bin/env python3
"""Poll iLink QR status until confirmed, then save credentials + write done file.
Reconstructed weixin_watch.py (was referenced by hermes-weixin-gateway skill but missing).
Run with the Hermes venv python after weixin_qr.py."""
import asyncio
import os
import sys
import time

sys.path.insert(0, "/usr/local/lib/hermes-agent")
sys.path.insert(0, "/usr/local/lib/hermes-agent/gateway")
os.chdir("/root")

from gateway.platforms import weixin
from gateway.platforms.weixin import save_weixin_account

STATUS_FILE = "/tmp/weixin_qr_status.txt"
DONE_FILE = "/tmp/weixin_qr_done.txt"
HERMES_HOME = os.path.expanduser("~/.hermes")


async def main() -> int:
    import aiohttp

    if not os.path.exists(STATUS_FILE):
        print("no status file; run weixin_qr.py first")
        return 1
    qrcode = ""
    with open(STATUS_FILE) as f:
        for line in f:
            if line.startswith("qrcode="):
                qrcode = line.strip().split("=", 1)[1]
    if not qrcode:
        print("no qrcode in status file")
        return 1

    async with aiohttp.ClientSession(
        trust_env=True, connector=weixin._make_ssl_connector()
    ) as session:
        deadline = time.monotonic() + 480  # QR expiry window (~8 min)
        current_base_url = weixin.ILINK_BASE_URL
        while time.monotonic() < deadline:
            try:
                resp = await weixin._api_get(
                    session,
                    base_url=current_base_url,
                    endpoint=f"{weixin.EP_GET_QR_STATUS}?qrcode={qrcode}",
                    timeout_ms=weixin.QR_TIMEOUT_MS,
                )
            except Exception:
                await asyncio.sleep(1)
                continue

            status = str(resp.get("status") or "wait")
            if status == "wait":
                pass
            elif status == "scaned":
                print("SCANED: someone scanned, waiting for confirm...", flush=True)
            elif status == "scaned_but_redirect":
                redirect_host = str(resp.get("redirect_host") or "")
                if redirect_host:
                    current_base_url = f"https://{redirect_host}"
                print("REDIRECT: following host", flush=True)
            elif status == "expired":
                print("EXPIRED: QR expired, re-run weixin_qr.py", flush=True)
                return 1
            elif status == "confirmed":
                account_id = str(resp.get("ilink_bot_id") or "")
                token = str(resp.get("bot_token") or "")
                base_url = str(resp.get("baseurl") or weixin.ILINK_BASE_URL)
                user_id = str(resp.get("ilink_user_id") or "")
                if not account_id or not token:
                    print("CONFIRMED but credential payload incomplete", flush=True)
                    return 1
                save_weixin_account(
                    HERMES_HOME,
                    account_id=account_id,
                    token=token,
                    base_url=base_url,
                    user_id=user_id,
                )
                with open(DONE_FILE, "w") as f:
                    f.write(f"account_id={account_id}\nuser_id={user_id}\nbase_url={base_url}\n")
                print(f"CONFIRMED: account_id={account_id} user_id={user_id}", flush=True)
                return 0
            await asyncio.sleep(1)

        print("TIMEOUT: no scan within 8 minutes", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
