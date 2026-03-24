"""
Webhook notifications for the IRS pipeline.

After worklist refresh, sends new manual-review items to a configurable
webhook URL (IRS_WEBHOOK_URL environment variable).
"""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from typing import Any


WEBHOOK_URL = os.getenv("IRS_WEBHOOK_URL", "").strip()


def notify_new_exceptions(worklist_items: list[dict[str, Any]], previous_hashes: set[str]) -> int:
    """
    POST new manual-review items to the webhook URL.

    Returns the number of notifications sent (0 if webhook not configured).
    """
    if not WEBHOOK_URL:
        return 0

    new_items = [
        item for item in worklist_items
        if item.get("next_action") == "MANUAL REVIEW"
        and item.get("document_hash") not in previous_hashes
    ]

    if not new_items:
        return 0

    payload = {
        "event": "new_exceptions",
        "count": len(new_items),
        "items": [
            {
                "document_hash": item.get("document_hash"),
                "file_name": item.get("file_name") or item.get("attachment_name"),
                "action_reason": item.get("action_reason"),
                "priority": item.get("priority"),
            }
            for item in new_items
        ],
    }

    try:
        req = urllib.request.Request(
            WEBHOOK_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        return len(new_items)
    except (urllib.error.URLError, OSError) as e:
        print(f"[WEBHOOK] Failed to send notification: {e}")
        return 0
