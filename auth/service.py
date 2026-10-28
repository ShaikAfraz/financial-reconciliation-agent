"""
Core Authentication and Session Service.
Implements scrypt password hashing, timing-attack resistance, persistent login throttling,
cryptographic session tokens (SHA-256 stored), CSRF validation, and last-admin protection.
"""

import hmac
import json
import secrets
import hashlib
import sqlite3
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Tuple, List
from pathlib import Path

import data.db as db

# Security parameters
MIN_PASSWORD_LENGTH = 12
DEFAULT_SCRYPT_N = 16384
DEFAULT_SCRYPT_R = 8
DEFAULT_SCRYPT_P = 1
DEFAULT_SCRYPT_MAXMEM = 33554432
SESSION_LIFETIME_HOURS = 24
MAX_LOGIN_FAILURES = 5
THROTTLE_WINDOW_MINUTES = 15
MAX_REGISTRATION_ATTEMPTS = 5
ALLOWED_ROLES = {"admin", "analyst", "auditor"}

# Pre-computed dummy salt and hash for timing attack mitigation on missing user
DUMMY_SALT = bytes([0x42] * 16)
_dummy_params = {"n": DEFAULT_SCRYPT_N, "r": DEFAULT_SCRYPT_R, "p": DEFAULT_SCRYPT_P}

def normalize_username(username: str) -> str:
    """Normalizes username/email to lowercase and stripped of whitespace."""
    if not username:
        return ""
    return username.strip().lower()

def validate_password_strength(password: str) -> Tuple[bool, Optional[str]]:
    """Validates that password meets strict enterprise security policy."""
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        return False, f"Password must be at least {MIN_PASSWORD_LENGTH} characters in length."
    return True, None

def hash_password(password: str) -> Tuple[str, str, str]:
    """
    Hashes password using hashlib.scrypt with a unique cryptographically random salt.
    Returns (hash_hex, salt_hex, params_json).
    """
    is_valid, err = validate_password_strength(password)
    if not is_valid:
        raise ValueError(err)

    salt = secrets.token_bytes(16)
    hash_bytes = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=DEFAULT_SCRYPT_N,
        r=DEFAULT_SCRYPT_R,
        p=DEFAULT_SCRYPT_P,
        maxmem=DEFAULT_SCRYPT_MAXMEM
    )
    params = {
        "n": DEFAULT_SCRYPT_N,
        "r": DEFAULT_SCRYPT_R,
        "p": DEFAULT_SCRYPT_P,
        "maxmem": DEFAULT_SCRYPT_MAXMEM
    }
    return hash_bytes.hex(), salt.hex(), json.dumps(params)

def verify_password(password: str, expected_hash_hex: str, salt_hex: str, params_json: str) -> bool:
    """
    Verifies a password against stored scrypt hash and parameters in constant time.
    """
    try:
        params = json.loads(params_json)
        salt = bytes.fromhex(salt_hex)
        hash_bytes = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=params.get("n", DEFAULT_SCRYPT_N),
            r=params.get("r", DEFAULT_SCRYPT_R),
            p=params.get("p", DEFAULT_SCRYPT_P),
            maxmem=params.get("maxmem", DEFAULT_SCRYPT_MAXMEM)
        )
        return hmac.compare_digest(hash_bytes.hex(), expected_hash_hex)
    except Exception:
        return False

def check_login_throttle(username: str, ip_address: Optional[str] = None) -> bool:
    """
    Checks if login attempts exceed threshold in the recent time window.
    Returns True if throttled (>= 5 failures within 15 minutes).
    """
    conn = db.get_connection()
    cursor = conn.cursor()
    window_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=THROTTLE_WINDOW_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
    SELECT COUNT(*) FROM login_attempts
    WHERE (username = ? OR (ip_address IS NOT NULL AND ip_address = ?))
      AND success = 0
      AND attempt_time >= ?
    """, (username, ip_address, window_cutoff))

    failures = cursor.fetchone()[0]
    conn.close()
    return failures >= MAX_LOGIN_FAILURES

def record_login_attempt(username: str, ip_address: Optional[str], success: bool) -> None:
    """Records a login attempt in the persistent throttling table."""
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO login_attempts (username, ip_address, success)
    VALUES (?, ?, ?)
    """, (username, ip_address, 1 if success else 0))
    conn.commit()
    conn.close()

def create_user(
    username: str,
    password: str,
    role: str = "analyst",
    is_active: bool = True,
    email: Optional[str] = None
) -> Dict[str, Any]:
    """
    Creates a new user record.
    Passwords must be at least 12 characters.
    """
    norm_user = normalize_username(username)
    if not norm_user:
        raise ValueError("Username cannot be empty")

    if role not in ALLOWED_ROLES:
        raise ValueError(f"Invalid role '{role}'. Allowed roles: {', '.join(sorted(ALLOWED_ROLES))}")

    is_valid, err = validate_password_strength(password)
    if not is_valid:
        raise ValueError(err)

    pwd_hash, salt, params = hash_password(password)
    user_id = f"usr_{secrets.token_hex(8)}"
    norm_email = normalize_username(email) if email else f"{norm_user}@reconciliation.local"

    conn = db.get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
        INSERT INTO users (id, username, email, password_hash, salt, hash_params, role, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, norm_user, norm_email, pwd_hash, salt, params, role, 1 if is_active else 0))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise ValueError(f"User '{norm_user}' already exists")

    cursor.execute("SELECT id, username, email, role, is_active, created_at FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    user_data = dict(row)
    conn.close()
    return user_data

def register_analyst(username: str, email: str, password: str) -> Dict[str, Any]:
    """Creates a self-service account with the least-privileged application role."""
    norm_user = normalize_username(username)
    norm_email = normalize_username(email)
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,31}", norm_user):
        raise ValueError("Username must be 3–32 characters and use letters, numbers, dots, underscores, or hyphens.")
    if len(norm_email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", norm_email):
        raise ValueError("Enter a valid email address.")
    is_valid, err = validate_password_strength(password)
    if not is_valid:
        raise ValueError(err)

    pwd_hash, salt, params = hash_password(password)
    user_id = f"usr_{secrets.token_hex(8)}"
    conn = db.get_connection()
    try:
        # Serialize duplicate checks with inserts so concurrent signup requests cannot
        # claim the same email address.
        conn.execute("BEGIN IMMEDIATE")
        duplicate = conn.execute(
            "SELECT 1 FROM users WHERE username = ? OR lower(email) = ? LIMIT 1",
            (norm_user, norm_email)
        ).fetchone()
        if duplicate:
            conn.rollback()
            raise ValueError("An account with those details already exists.")
        conn.execute("""
            INSERT INTO users (id, username, email, password_hash, salt, hash_params, role, is_active)
            VALUES (?, ?, ?, ?, ?, ?, 'analyst', 1)
        """, (user_id, norm_user, norm_email, pwd_hash, salt, params))
        conn.commit()
        row = conn.execute(
            "SELECT id, username, email, role, is_active, created_at FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
        return dict(row)
    except sqlite3.IntegrityError:
        conn.rollback()
        raise ValueError("An account with those details already exists.")
    finally:
        conn.close()

def check_registration_throttle(ip_address: Optional[str]) -> bool:
    if not ip_address:
        return False
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=THROTTLE_WINDOW_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    conn = db.get_connection()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM registration_attempts WHERE ip_address = ? AND attempt_time >= ?",
            (ip_address, cutoff)
        ).fetchone()[0]
        return count >= MAX_REGISTRATION_ATTEMPTS
    finally:
        conn.close()

def record_registration_attempt(ip_address: Optional[str]) -> None:
    conn = db.get_connection()
    try:
        conn.execute("DELETE FROM registration_attempts WHERE attempt_time < datetime('now', '-1 day')")
        conn.execute("INSERT INTO registration_attempts (ip_address) VALUES (?)", (ip_address,))
        conn.commit()
    finally:
        conn.close()

def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """Retrieves a user by normalized username or email."""
    norm_user = normalize_username(username)
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ? OR email = ?", (norm_user, norm_user))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    """Retrieves a user by ID."""
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, role, is_active, created_at, updated_at FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def list_users() -> List[Dict[str, Any]]:
    """Lists all registered users (excluding secrets)."""
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, role, is_active, created_at, updated_at FROM users ORDER BY created_at ASC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def can_modify_admin(target_user_id: str, new_role: Optional[str] = None, new_active: Optional[bool] = None) -> Tuple[bool, Optional[str]]:
    """
    Enforces last active administrator protection.
    Prevents deactivating, demoting, or deleting the sole active admin.
    """
    conn = db.get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT role, is_active FROM users WHERE id = ?", (target_user_id,))
    target = cursor.fetchone()
    if not target:
        conn.close()
        return False, "Target user not found"

    # If the user is currently an active administrator:
    if target["role"] == "admin" and target["is_active"] == 1:
        # Check if change would revoke admin privilege or deactivate
        is_revoking = (new_role is not None and new_role != "admin") or (new_active is not None and not new_active)
        if is_revoking:
            cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1")
            active_admin_count = cursor.fetchone()[0]
            if active_admin_count <= 1:
                conn.close()
                return False, "Action denied: cannot demote or deactivate the last active administrator."

    conn.close()
    return True, None

def update_user(
    target_user_id: str,
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
    new_password: Optional[str] = None
) -> Dict[str, Any]:
    """Updates user role, active status, or password with admin safety checks."""
    can_mod, err = can_modify_admin(target_user_id, new_role=role, new_active=is_active)
    if not can_mod:
        raise ValueError(err)

    conn = db.get_connection()
    cursor = conn.cursor()

    updates = []
    params = []

    if role is not None:
        if role not in ALLOWED_ROLES:
            conn.close()
            raise ValueError(f"Invalid role '{role}'")
        updates.append("role = ?")
        params.append(role)

    if is_active is not None:
        updates.append("is_active = ?")
        params.append(1 if is_active else 0)

    if new_password is not None:
        is_valid, p_err = validate_password_strength(new_password)
        if not is_valid:
            conn.close()
            raise ValueError(p_err)
        pwd_hash, salt, h_params = hash_password(new_password)
        updates.extend(["password_hash = ?", "salt = ?", "hash_params = ?"])
        params.extend([pwd_hash, salt, h_params])

    if not updates:
        conn.close()
        return get_user_by_id(target_user_id) or {}

    updates.append("updated_at = CURRENT_TIMESTAMP")
    params.append(target_user_id)

    cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    return get_user_by_id(target_user_id) or {}

def update_user_status(target_user_id: str, is_active: bool) -> Dict[str, Any]:
    """Convenience helper to update active status of a user."""
    return update_user(target_user_id, is_active=is_active)

def authenticate_user(
    username: str,
    password: str,
    ip_address: Optional[str] = None
) -> Tuple[Optional[Dict[str, Any]], Optional[str], int]:
    """
    Authenticates user credentials.
    Returns (user_dict, error_message, status_code).
    Ensures identical generic error message on unknown user or bad password.
    Executes dummy scrypt on unknown user to prevent timing attacks.
    Enforces persistent login throttling (429).
    """
    norm_user = normalize_username(username)

    # 1. Check throttling
    if check_login_throttle(norm_user, ip_address):
        return None, "Too many failed login attempts. Please try again in 15 minutes.", 429

    # 2. Look up user
    user = get_user_by_username(norm_user)

    if user is None:
        # Timing attack resistance: execute dummy scrypt computation
        try:
            hashlib.scrypt(
                password.encode("utf-8"),
                salt=DUMMY_SALT,
                n=DEFAULT_SCRYPT_N,
                r=DEFAULT_SCRYPT_R,
                p=DEFAULT_SCRYPT_P,
                maxmem=DEFAULT_SCRYPT_MAXMEM
            )
        except Exception:
            pass
        record_login_attempt(norm_user, ip_address, success=False)
        return None, "Invalid username or password", 401

    # 3. Check active status
    if not user.get("is_active"):
        record_login_attempt(norm_user, ip_address, success=False)
        return None, "Account is inactive. Please contact an administrator.", 403

    # 4. Verify password
    if not verify_password(password, user["password_hash"], user["salt"], user["hash_params"]):
        record_login_attempt(norm_user, ip_address, success=False)
        return None, "Invalid username or password", 401

    # 5. Success
    record_login_attempt(norm_user, ip_address, success=True)
    safe_user = {
        "id": user["id"],
        "username": user["username"],
        "email": user.get("email") or f"{user['username']}@reconciliation.local",
        "role": user["role"],
        "is_active": bool(user["is_active"]),
        "created_at": user["created_at"]
    }
    return safe_user, None, 200

def create_session(user_id: str, lifetime_hours: int = SESSION_LIFETIME_HOURS) -> Tuple[str, str, str]:
    """
    Generates a cryptographically random session token and stores only its SHA-256 hash.
    Returns (raw_session_token, csrf_token, expires_at_iso).
    """
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    csrf_token = secrets.token_urlsafe(32)

    expires_dt = datetime.now(timezone.utc) + timedelta(hours=lifetime_hours)
    expires_at = expires_dt.strftime("%Y-%m-%d %H:%M:%S")

    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO sessions (token_hash, user_id, expires_at, csrf_token)
    VALUES (?, ?, ?, ?)
    """, (token_hash, user_id, expires_at, csrf_token))
    conn.commit()
    conn.close()

    return raw_token, csrf_token, expires_dt.isoformat()

def validate_session(raw_token: str) -> Optional[Dict[str, Any]]:
    """
    Validates session token hash, checks expiration, and returns authenticated user and CSRF token.
    Updates last_activity_at on successful validation.
    """
    if not raw_token:
        return None

    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    conn = db.get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT s.token_hash, s.user_id, s.csrf_token, s.expires_at,
           u.username, u.email, u.role, u.is_active
    FROM sessions s
    JOIN users u ON s.user_id = u.id
    WHERE s.token_hash = ?
      AND s.expires_at > ?
      AND u.is_active = 1
    """, (token_hash, now_str))

    row = cursor.fetchone()
    if not row:
        conn.close()
        return None

    # Update last activity
    cursor.execute("""
    UPDATE sessions
    SET last_activity_at = CURRENT_TIMESTAMP
    WHERE token_hash = ?
    """, (token_hash,))
    conn.commit()
    conn.close()

    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "email": row["email"],
        "role": row["role"],
        "csrf_token": row["csrf_token"],
        "expires_at": row["expires_at"]
    }

def invalidate_session(raw_token: str) -> bool:
    """Invalidates/deletes a session by its raw token."""
    if not raw_token:
        return False
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted
