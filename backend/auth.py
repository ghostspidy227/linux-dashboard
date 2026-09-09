import os
import time
import threading
import bcrypt
from datetime import datetime, timedelta, timezone

import jwt

from backend.config import load_config, save_config

ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24

# Generate a secret key on first run
KEY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", ".secret")


def _get_secret():
    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, "r") as f:
            return f.read().strip()
    import secrets
    key = secrets.token_hex(32)
    os.makedirs(os.path.dirname(KEY_PATH), exist_ok=True)
    with open(KEY_PATH, "w") as f:
        f.write(key)
    os.chmod(KEY_PATH, 0o600)
    return key


SECRET_KEY = _get_secret()

# --- Login rate limiting: (ip, username) -> [fail_count, lock_until_epoch] ---
_login_fails = {}
_login_lock = threading.Lock()
LOCK_BASE_SECONDS = 60
LOCK_MAX_SECONDS = 900
LOCK_AFTER_FAILS = 5


def _locked(key):
    entry = _login_fails.get(key)
    return bool(entry and entry[1] > time.time())


def _record_fail(key):
    with _login_lock:
        entry = _login_fails.get(key, [0, 0])
        entry[0] += 1
        if entry[0] >= LOCK_AFTER_FAILS:
            penalty = min(LOCK_BASE_SECONDS * (2 ** (entry[0] - LOCK_AFTER_FAILS)), LOCK_MAX_SECONDS)
            entry[1] = time.time() + penalty
        _login_fails[key] = entry
        return entry[1]


def _clear_fails(key):
    with _login_lock:
        _login_fails.pop(key, None)


def login_lock_remaining(ip, username):
    """Seconds left before this ip+user may try again (0 = allowed)."""
    key = f"{ip}|{username}"
    if not _locked(key):
        return 0
    with _login_lock:
        return max(0, int(_login_fails[key][1] - time.time()))


def hash_password(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password, hashed):
    return bcrypt.checkpw(password.encode(), hashed.encode())


def is_default_password():
    auth = load_config().get("auth", {})
    stored_hash = auth.get("password_hash", "")
    if not stored_hash:
        return True
    try:
        return verify_password("admin", stored_hash)
    except (ValueError, TypeError):
        return False


def ensure_default_auth():
    config = load_config()
    auth = config.get("auth", {})
    if not auth.get("username"):
        auth["username"] = "admin"
    stored = auth.get("password_hash", "")
    if not stored or not stored.startswith(("$2b$", "$2a$", "$2y$")):
        auth["password_hash"] = hash_password("admin")
        config["auth"] = auth
        save_config(config)
        print("[auth] No valid password hash found — set default 'admin', change it after login.")


def _valid_stored_hash(auth):
    stored = auth.get("password_hash", "")
    if not stored.startswith(("$2b$", "$2a$", "$2y$")):
        return None
    try:
        verify_password("probe-not-a-password", stored)
        return stored
    except (ValueError, TypeError):
        return None


def create_token(username):
    expire = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": username,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except jwt.InvalidTokenError:
        return None


def authenticate(ip, username, password):
    """Returns (token, None) on success or (None, lock_seconds_remaining) on failure."""
    key = f"{ip}|{username}"
    remaining = login_lock_remaining(ip, username)
    if remaining:
        return None, remaining

    auth = load_config().get("auth", {})
    stored_user = auth.get("username", "admin")
    stored_hash = _valid_stored_hash(auth)
    if stored_hash is None:
        print("[auth] ERROR: password hash invalid/corrupted — refusing login instead of resetting. "
              "Run install.sh or delete auth.password_hash in data/config.json to reset.")
        return None, _record_fail(key)

    if username != stored_user or not verify_password(password, stored_hash):
        return None, _record_fail(key)

    _clear_fails(key)
    return create_token(username), 0


def change_password(username, new_password):
    if not new_password:
        return False, "Password cannot be empty"
    config = load_config()
    config["auth"] = {
        "username": username,
        "password_hash": hash_password(new_password),
    }
    save_config(config)
    return True, "Password changed"
