"""
sheet_manager.py
─────────────────────────────────────────────────────────────────────────
Lowest-level layer of the Google-Sheets-as-database stack.

Responsibility: ONLY the raw HTTP plumbing to talk to the Google Apps
Script Web App that sits in front of the Google Sheet. Nothing in this
file knows about users, passwords, or business rules — that lives one
layer up in google_auth.py / apps_script_api.py.

This separation is deliberate so that if the project later migrates the
"cloud database" from Google Sheets to something else (Airtable, a REST
service, etc.), only this file needs to change.
"""

import os
import json
import time
import requests
from typing import Optional


class SheetManagerError(Exception):
    """Raised when the Apps Script Web App is unreachable or returns an
    unexpected/invalid response."""
    pass


class SheetManager:
    def __init__(self, webhook_url: Optional[str] = None, api_secret: Optional[str] = None,
                 timeout: int = 12, max_retries: int = 2):
        self.webhook_url = webhook_url or os.getenv("GAS_WEBHOOK_URL", "")
        self.api_secret = api_secret or os.getenv("GAS_API_SECRET", "")
        self.timeout = timeout
        self.max_retries = max_retries

    def is_configured(self) -> bool:
        return bool(self.webhook_url)

    def _post(self, payload: dict) -> dict:
        """POST JSON to the Apps Script Web App and parse the JSON response.
        Retries on transient network failures (Apps Script cold-starts can
        be slow / occasionally flaky)."""
        if not self.webhook_url:
            raise SheetManagerError(
                "GAS_WEBHOOK_URL is not configured. Set it in Render environment "
                "variables, or via Admin Panel → assign Google Sheet per user."
            )

        body = dict(payload)
        body["api_secret"] = self.api_secret

        last_err = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = requests.post(
                    self.webhook_url,
                    data=json.dumps(body),
                    headers={"Content-Type": "application/json"},
                    timeout=self.timeout,
                )
                if resp.status_code != 200:
                    last_err = SheetManagerError(
                        f"Apps Script returned HTTP {resp.status_code}: {resp.text[:300]}"
                    )
                    time.sleep(0.4 * (attempt + 1))
                    continue
                try:
                    data = resp.json()
                except ValueError:
                    raise SheetManagerError(
                        f"Apps Script returned non-JSON response: {resp.text[:300]}"
                    )
                return data
            except requests.RequestException as e:
                last_err = SheetManagerError(f"Network error reaching Apps Script: {e}")
                time.sleep(0.4 * (attempt + 1))

        raise last_err or SheetManagerError("Unknown error contacting Apps Script")

    def call_action(self, action: str, **kwargs) -> dict:
        """Generic helper: POST {action, ...kwargs} and return the parsed JSON."""
        payload = {"action": action}
        payload.update(kwargs)
        return self._post(payload)


# Module-level singleton — most callers just need `from sheet_manager import sheet_manager`
sheet_manager = SheetManager()
