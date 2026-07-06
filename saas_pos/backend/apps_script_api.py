"""
apps_script_api.py
─────────────────────────────────────────────────────────────────────────
Middle layer: a typed, action-specific API on top of sheet_manager.py.

This is where each "action" the Google Apps Script Web App understands
gets its own clearly-named Python function with a defined input/output
shape. google_auth.py (business logic) calls into THIS file; it never
talks to sheet_manager.py directly.

Architecture:
    User → FastAPI Backend (Render) → apps_script_api.py → sheet_manager.py
           → Google Apps Script Web API → Google Sheet Database
"""

from typing import Optional, List, Dict, Any
from sheet_manager import sheet_manager, SheetManagerError


def signup_user(username: str, password_hash: str, full_name: str = "", email: str = "",
                 whatsapp: str = "", city: str = "", store_type: str = "",
                 subscription_plan: str = "free", plan_amount: int = 0,
                 expiry_date: str = "", account_status: str = "Inactive",
                 device_limit: int = 1, allowed_ips: str = "", trial_bills_used: int = 0,
                 payment_status: str = "pending", upi_used: str = "",
                 receipt_path: str = "") -> Dict[str, Any]:
    """Append a new row to the Users sheet. Returns {success, user_id} or
    {success: False, message}."""
    return sheet_manager.call_action(
        "signup",
        username=username, password_hash=password_hash, full_name=full_name,
        email=email, whatsapp=whatsapp, city=city, store_type=store_type,
        subscription_plan=subscription_plan, plan_amount=plan_amount,
        expiry_date=expiry_date, account_status=account_status,
        device_limit=device_limit, allowed_ips=allowed_ips,
        trial_bills_used=trial_bills_used, payment_status=payment_status,
        upi_used=upi_used, receipt_path=receipt_path,
    )


def lookup_user(username: str) -> Optional[Dict[str, Any]]:
    """Fetch a single user row by username. Returns the user dict, or None
    if not found / on error (callers should treat None as 'auth failed')."""
    try:
        res = sheet_manager.call_action("login_lookup", username=username)
    except SheetManagerError:
        raise
    if not res.get("success"):
        # Log the REAL reason Apps Script rejected this (e.g. "Unauthorized —
        # invalid API secret" vs "User not found") instead of silently
        # collapsing everything into None, which made every failure mode
        # look identical to a genuine "user not found" in the logs.
        print(f"[AUTH] Sheet lookup failed for {username!r}: {res.get('message') or res.get('error') or res}")
        return None
    return res.get("user")


def lookup_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    """Fetch a single user row by the Sheet's stable numeric user_id."""
    res = sheet_manager.call_action("get_user_by_id", user_id=user_id)
    if not res.get("success"):
        return None
    return res.get("user")


def list_all_users() -> List[Dict[str, Any]]:
    """Fetch every row from the Users sheet — used by the admin panel."""
    res = sheet_manager.call_action("list_users")
    if not res.get("success"):
        raise SheetManagerError(res.get("message", "Failed to list users"))
    return res.get("users", [])


def update_account(username: Optional[str], fields: Dict[str, Any],
                    user_id: Optional[int] = None) -> Dict[str, Any]:
    """Update one or more columns for a user. Prefer passing user_id (stable
    identity) — required when `fields` includes a username rename, since
    looking the row up by its old username after the fact would fail.
    `fields` keys must match the Users sheet header names exactly (see
    GoogleAppsScript.js USERS_HEADER)."""
    return sheet_manager.call_action("update_account", username=username, user_id=user_id, fields=fields)


def update_last_login(username: str) -> bool:
    res = sheet_manager.call_action("update_last_login", username=username)
    return bool(res.get("success"))


def delete_user(username: Optional[str] = None, user_id: Optional[int] = None) -> bool:
    res = sheet_manager.call_action("delete_user", username=username, user_id=user_id)
    return bool(res.get("success"))
