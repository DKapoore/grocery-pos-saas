"""
google_auth.py
─────────────────────────────────────────────────────────────────────────
Top layer: all authentication & account-management BUSINESS LOGIC.

main.py's HTTP routes call into this file. This file calls into
apps_script_api.py (which calls sheet_manager.py, which calls the Apps
Script Web App, which reads/writes the Google Sheet).

    User → FastAPI (main.py) → google_auth.py → apps_script_api.py
         → sheet_manager.py → Google Apps Script → Google Sheet

Why bcrypt: the previous implementation hashed passwords with unsalted
SHA-256, which is not appropriate for password storage (fast to brute
force, no per-user salt). bcrypt is slow-by-design and salts
automatically, which is the standard for this purpose.

Future scalability note: every function here returns / accepts plain
Python dicts shaped like simple ORM rows. If the project later migrates
from Google Sheets to PostgreSQL, only apps_script_api.py needs to be
swapped for a db_api.py with the same function signatures — google_auth.py
and main.py would not need to change.
"""

import os
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

import bcrypt

import apps_script_api as sheets
from sheet_manager import SheetManagerError

send_email_via_gas = sheets.send_email_via_gas


# ======================== PASSWORD HASHING ========================
def hash_password(password: str) -> str:
    """bcrypt hash — includes its own random salt, safe to store as-is."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash or not password:
        return False
    # Strip any whitespace that Google Sheets may add to cell values
    stored = password_hash.strip()
    if not stored:
        return False
    try:
        # bcrypt hashes start with $2b$ or $2a$
        if stored.startswith(("$2b$", "$2a$", "$2y$")):
            return bcrypt.checkpw(password.encode("utf-8"), stored.encode("utf-8"))
        # Legacy fallback: SHA-256 (unsalted) — for accounts approved before the
        # bcrypt migration. This allows existing users to keep logging in without
        # needing admin to re-generate their password.
        import hashlib
        sha_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return sha_hash == stored
    except (ValueError, TypeError, Exception):
        # Malformed hash — try SHA-256 as last resort
        try:
            import hashlib
            return hashlib.sha256(password.encode("utf-8")).hexdigest() == stored
        except Exception:
            return False


def generate_password(length: int = 10) -> str:
    return secrets.token_urlsafe(length)[:length]


# ======================== HELPERS ========================
def _normalize_user(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Apps Script returns Sheet cell values which may come back as floats
    (e.g. user_id, device_limit) or empty strings for blank cells. Normalize
    types so the rest of the backend can rely on consistent shapes."""
    if not raw:
        return raw

    def _int(v, default=0):
        try:
            if v in (None, ""):
                return default
            return int(float(v))
        except (ValueError, TypeError):
            return default

    def _str(v):
        return "" if v is None else str(v)

    return {
        "user_id": _int(raw.get("user_id")),
        "username": _str(raw.get("username")),
        "password_hash": _str(raw.get("password_hash")),
        "full_name": _str(raw.get("full_name")),
        "email": _str(raw.get("email")),
        "whatsapp": _str(raw.get("whatsapp")),
        "city": _str(raw.get("city")),
        "store_type": _str(raw.get("store_type")),
        "subscription_plan": _str(raw.get("subscription_plan")) or "free",
        "plan_amount": _int(raw.get("plan_amount")),
        "expiry_date": _str(raw.get("expiry_date")),
        "account_status": _str(raw.get("account_status")) or "Inactive",
        "device_limit": _int(raw.get("device_limit"), default=1),
        "allowed_ips": _str(raw.get("allowed_ips")),
        "trial_bills_used": _int(raw.get("trial_bills_used")),
        "payment_status": _str(raw.get("payment_status")) or "pending",
        "upi_used": _str(raw.get("upi_used")),
        "receipt_path": _str(raw.get("receipt_path")),
        "settings_password_hash": _str(raw.get("settings_password_hash")),
        "created_date": _str(raw.get("created_date")),
        "last_login": _str(raw.get("last_login")),
        "must_change_password": _str(raw.get("must_change_password")).strip() in ("1", "true", "True", "TRUE"),
        # Convenience booleans mirroring the old SQLite `is_active` / `is_blocked` flags,
        # derived from account_status so callers don't need to know the Sheet's string values.
        "is_active": _str(raw.get("account_status")).strip().lower() == "active",
        "is_blocked": _str(raw.get("account_status")).strip().lower() == "blocked",
    }


# ======================== SIGNUP ========================
def signup(username: str, password: Optional[str], full_name: str = "", email: str = "",
           whatsapp: str = "", city: str = "", store_type: str = "",
           subscription_plan: str = "free", plan_amount: int = 0,
           upi_used: str = "", receipt_path: str = "",
           account_status: str = "Inactive", payment_status: str = "pending") -> Dict[str, Any]:
    """Create a new account row in the Google Sheet. Used by both the paid
    registration flow (account_status=Inactive, pending admin approval) and
    the free-trial flow (account_status=Active immediately, no password)."""
    existing = sheets.lookup_user(username)
    if existing:
        return {"success": False, "message": "Username already exists"}

    pw_hash = hash_password(password) if password else ""

    res = sheets.signup_user(
        username=username, password_hash=pw_hash, full_name=full_name, email=email,
        whatsapp=whatsapp, city=city, store_type=store_type,
        subscription_plan=subscription_plan, plan_amount=plan_amount,
        expiry_date="", account_status=account_status, device_limit=1,
        allowed_ips="", trial_bills_used=0, payment_status=payment_status,
        upi_used=upi_used, receipt_path=receipt_path,
    )
    return res


# ======================== LOGIN ========================
def authenticate(username: str, password: str) -> Dict[str, Any]:
    """Validate credentials against the Sheet. Returns:
        {"success": True, "user": {...normalized...}}
      or
        {"success": False, "message": "..."}
    Mirrors the JSON contract requested in the architecture spec."""
    raw = sheets.lookup_user(username)
    if not raw:
        print(f"[AUTH] User not found in Sheet: {username!r}")
        return {"success": False, "message": "Invalid credentials"}

    user = _normalize_user(raw)
    print(f"[AUTH] User found: {username!r} | status={user['account_status']!r} | plan={user['subscription_plan']!r} | hash_prefix={user['password_hash'][:8] if user['password_hash'] else 'EMPTY'!r}")

    if user["account_status"].strip().lower() == "blocked":
        return {"success": False, "message": "Account blocked. Contact support."}

    if user["subscription_plan"] != "free" and not user["is_active"]:
        return {"success": False, "message": "Account not activated yet. Please wait for admin approval."}

    # Free-trial accounts may have no password set — first login just works.
    if user["subscription_plan"] == "free" and not user["password_hash"]:
        pass
    elif not verify_password(password, user["password_hash"]):
        return {"success": False, "message": "Invalid credentials"}

    # Subscription expiry check
    if user["expiry_date"] and user["subscription_plan"] != "free":
        try:
            expiry = datetime.fromisoformat(user["expiry_date"])
            # Some expiry_date values in the Sheet are timezone-aware (e.g. end
            # with 'Z' or '+00:00') while others are naive — comparing a naive
            # datetime.utcnow() against an aware one raises TypeError (not
            # ValueError), which was crashing the whole request unhandled.
            # Treat an aware value as already being UTC wall-clock time, same
            # as utcnow(), by dropping the tzinfo before comparing.
            if expiry.tzinfo is not None:
                expiry = expiry.replace(tzinfo=None)
            if datetime.utcnow() > expiry:
                return {"success": False, "message": "Subscription expired",
                        "plan": user["subscription_plan"], "expiry": user["expiry_date"],
                        "status": user["account_status"]}
        except (ValueError, TypeError):
            pass  # malformed/unexpected date — don't block login over a data issue

    try:
        sheets.update_last_login(username)
    except SheetManagerError:
        pass  # non-critical — don't fail login if this write fails

    return {
        "success": True,
        "message": "Login successful",
        "plan": user["subscription_plan"],
        "expiry": user["expiry_date"],
        "status": user["account_status"],
        "user": user,
    }


def get_user(username: str) -> Optional[Dict[str, Any]]:
    raw = sheets.lookup_user(username)
    if not raw:
        return None
    return _normalize_user(raw)


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    raw = sheets.lookup_user_by_id(user_id)
    if not raw:
        return None
    return _normalize_user(raw)


def list_users() -> list:
    raw_users = sheets.list_all_users()
    return [_normalize_user(u) for u in raw_users]


# ======================== ADMIN ACCOUNT MANAGEMENT ========================
def approve_user(user_id: int, new_username: str, password: Optional[str], plan_days: int,
                  device_limit: Optional[int] = None) -> Dict[str, Any]:
    """Activate a pending account: set/confirm username + password, plan, expiry, status.
    Looked up by user_id (stable) since the admin may be renaming the username
    in this same call."""
    current = get_user_by_id(user_id)
    if not current:
        return {"success": False, "message": "User not found"}

    final_password = password or generate_password()
    start = datetime.utcnow()
    expiry = start + timedelta(days=plan_days)
    plan = "yearly" if plan_days >= 300 else "monthly"

    fields = {
        "username": new_username,
        "password_hash": hash_password(final_password),
        "subscription_plan": plan,
        "expiry_date": expiry.isoformat(),
        "account_status": "Active",
        "payment_status": "approved",
    }
    if device_limit is not None:
        fields["device_limit"] = device_limit

    res = sheets.update_account(username=current["username"], fields=fields, user_id=user_id)
    if not res.get("success"):
        return {"success": False, "message": res.get("message", "Approve failed")}
    return {"success": True, "generated_password": final_password, "expiry": expiry.isoformat(),
            "plan": plan, "username": new_username}


def set_account_status(user_id: int, active: bool = None, blocked: bool = None) -> bool:
    fields = {}
    if blocked is True:
        fields["account_status"] = "Blocked"
    elif blocked is False and active is True:
        fields["account_status"] = "Active"
    elif active is False:
        fields["account_status"] = "Inactive"
    if not fields:
        return True
    res = sheets.update_account(username=None, fields=fields, user_id=user_id)
    return bool(res.get("success"))


def extend_subscription(user_id: int, days: int) -> Dict[str, Any]:
    user = get_user_by_id(user_id)
    if not user:
        return {"success": False, "message": "User not found"}

    if user["expiry_date"]:
        try:
            base = datetime.fromisoformat(user["expiry_date"])
            if base.tzinfo is not None:
                base = base.replace(tzinfo=None)
            if base < datetime.utcnow():
                base = datetime.utcnow()
        except (ValueError, TypeError):
            base = datetime.utcnow()
    else:
        base = datetime.utcnow()

    new_expiry = base + timedelta(days=days)
    res = sheets.update_account(username=user["username"], fields={"expiry_date": new_expiry.isoformat()}, user_id=user_id)
    return {"success": bool(res.get("success")), "new_expiry": new_expiry.isoformat()}


def update_device_limit(user_id: int, max_devices: int, allowed_ips: str = "") -> bool:
    max_devices = max(1, min(50, max_devices))
    res = sheets.update_account(username=None, fields={
        "device_limit": max_devices,
        "allowed_ips": allowed_ips.strip(),
    }, user_id=user_id)
    return bool(res.get("success"))


def update_settings_password(username: str, new_password_hash: str) -> bool:
    res = sheets.update_account(username=username, fields={"settings_password_hash": new_password_hash})
    return bool(res.get("success"))


def reset_password(user_id: int, new_password_hash: str, force_change: bool = True) -> bool:
    """Admin-triggered login password reset. Optionally flags the account so
    the user is forced to set their own new password on next login."""
    res = sheets.update_account(username=None, fields={
        "password_hash": new_password_hash,
        "must_change_password": "1" if force_change else "",
    }, user_id=user_id)
    return bool(res.get("success"))


def clear_must_change_password(username: str) -> bool:
    res = sheets.update_account(username=username, fields={"must_change_password": ""})
    return bool(res.get("success"))


def set_trial_bills_used(username: str, count: int) -> bool:
    res = sheets.update_account(username=username, fields={"trial_bills_used": count})
    return bool(res.get("success"))


def delete_account(user_id: int) -> bool:
    return sheets.delete_user(user_id=user_id)
