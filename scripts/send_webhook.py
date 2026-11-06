#!/usr/bin/env python3
"""Send a signed "new lead" event to a running `salesprep serve`.

export WEBHOOK_SECRET=change-me
python scripts/send_webhook.py L-1001 --url http://127.0.0.1:8787
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

from salesprep.server import sign


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("lead_id")
    parser.add_argument("--url", default="http://127.0.0.1:8787")
    args = parser.parse_args()

    secret = os.environ.get("WEBHOOK_SECRET")
    if not secret:
        print("WEBHOOK_SECRET is not set", file=sys.stderr)
        return 2

    body = json.dumps({"lead_id": args.lead_id}).encode()
    timestamp = str(int(time.time()))
    request = urllib.request.Request(
        args.url.rstrip("/") + "/webhook/new-lead",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Timestamp": timestamp,
            "X-Signature": sign(secret.encode(), timestamp, body),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            print(resp.status, resp.read().decode())
            return 0
    except urllib.error.HTTPError as exc:
        print(exc.code, exc.read().decode())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
