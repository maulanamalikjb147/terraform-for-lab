"""
Authentication manager for CloudLab VPS Hypervisor Dashboard
Provides PBKDF2 password hashing and token-based session management
Zero external pip dependencies required.
"""
import os
import json
import time
import hashlib
import secrets
from .config import USERS_FILE, ensure_directories

TOKEN_EXPIRY_SECONDS = 86400 * 7  # 7 days session
_ACTIVE_SESSIONS = {}  # token -> {"username": username, "expires": timestamp}

def _hash_password(password: str, salt: str = None) -> tuple:
    """Hash password using PBKDF2-HMAC-SHA256 with 100,000 iterations"""
    if not salt:
        salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100000
    ).hex()
    return hashed, salt

def _verify_password(password: str, hashed: str, salt: str) -> bool:
    """Verify password against hash and salt"""
    test_hash, _ = _hash_password(password, salt)
    return secrets.compare_digest(test_hash, hashed)

def load_users() -> dict:
    """Load users from file or create default admin"""
    ensure_directories()
    if not os.path.exists(USERS_FILE):
        return init_default_admin()
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return init_default_admin()

def save_users(users: dict):
    """Save users to secure file"""
    ensure_directories()
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2)
    try:
        os.chmod(USERS_FILE, 0o600)
    except Exception:
        pass

def init_default_admin() -> dict:
    """Initialize default admin credentials (admin / admin123)"""
    hashed, salt = _hash_password("admin123")
    users = {
        "admin": {
            "username": "admin",
            "password_hash": hashed,
            "salt": salt,
            "role": "admin",
            "created_at": int(time.time()),
            "must_change_password": True
        }
    }
    save_users(users)
    return users

def login(username: str, password: str) -> dict:
    """Authenticate user and return session token"""
    users = load_users()
    user = users.get(username)
    if not user:
        return {"success": False, "error": "Invalid username or password"}
    
    if not _verify_password(password, user["password_hash"], user["salt"]):
        return {"success": False, "error": "Invalid username or password"}
    
    token = secrets.token_hex(32)
    expires = time.time() + TOKEN_EXPIRY_SECONDS
    _ACTIVE_SESSIONS[token] = {
        "username": username,
        "role": user.get("role", "admin"),
        "expires": expires
    }
    
    return {
        "success": True,
        "token": token,
        "user": {
            "username": username,
            "role": user.get("role", "admin"),
            "must_change_password": user.get("must_change_password", False)
        }
    }

def verify_token(token: str) -> dict:
    """Check if session token is valid and active"""
    if not token:
        return None
    session = _ACTIVE_SESSIONS.get(token)
    if not session:
        return None
    if time.time() > session["expires"]:
        del _ACTIVE_SESSIONS[token]
        return None
    return session

def logout(token: str) -> bool:
    """Invalidate session token"""
    if token in _ACTIVE_SESSIONS:
        del _ACTIVE_SESSIONS[token]
        return True
    return False

def change_password(username: str, old_password: str, new_password: str) -> dict:
    """Change user password after verifying old password"""
    if len(new_password) < 6:
        return {"success": False, "error": "Password must be at least 6 characters"}
    
    users = load_users()
    user = users.get(username)
    if not user:
        return {"success": False, "error": "User not found"}
    
    if not _verify_password(old_password, user["password_hash"], user["salt"]):
        return {"success": False, "error": "Current password is incorrect"}
    
    hashed, salt = _hash_password(new_password)
    user["password_hash"] = hashed
    user["salt"] = salt
    user["must_change_password"] = False
    user["updated_at"] = int(time.time())
    users[username] = user
    save_users(users)
    
    return {"success": True, "message": "Password updated successfully"}

def reset_password_cli(username: str, new_password: str) -> bool:
    """Directly reset password from CLI (e.g. tf-vps-panel reset-password)"""
    users = load_users()
    hashed, salt = _hash_password(new_password)
    if username in users:
        users[username]["password_hash"] = hashed
        users[username]["salt"] = salt
        users[username]["must_change_password"] = False
        users[username]["updated_at"] = int(time.time())
    else:
        users[username] = {
            "username": username,
            "password_hash": hashed,
            "salt": salt,
            "role": "admin",
            "created_at": int(time.time()),
            "must_change_password": False
        }
    save_users(users)
    return True
