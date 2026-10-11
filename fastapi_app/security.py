"""Authentication helpers independent of a web framework."""

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
import secrets
import base64

from .db import connection, one, transaction

SESSION_COOKIE = "kp11_session"
SESSION_TTL = timedelta(hours=12)
PASSWORD_ITERATIONS = 390000


def _secret():
    return os.environ["APP_SECRET_KEY"].encode()


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def verify_password(encoded, password):
    """Verify hashes created by the previous PBKDF2 password implementation."""
    try:
        algorithm, iterations, salt, digest = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        derived = base64.b64encode(hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations))).decode().strip()
        return hmac.compare_digest(derived, digest)
    except (AttributeError, ValueError):
        return False


def make_password(password):
    """Create the password format used by the existing accounts table."""
    salt = secrets.token_urlsafe(16)
    digest = base64.b64encode(hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PASSWORD_ITERATIONS)).decode().strip()
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${digest}"


def password_error(password):
    if len(password) < 12:
        return "Пароль должен содержать не менее 12 символов."
    return None


def create_session(user):
    raw = secrets.token_urlsafe(48)
    with transaction() as conn:
        conn.execute(
            "INSERT INTO app_sessions(token_hash,user_id,auth_version,expires_at) VALUES (%s,%s,%s,%s)",
            (_hash(raw), user["id"], user["auth_version"], datetime.now(timezone.utc) + SESSION_TTL),
        )
    return raw


def delete_session(raw):
    if raw:
        with transaction() as conn:
            conn.execute("DELETE FROM app_sessions WHERE token_hash=%s", (_hash(raw),))


def current_user(raw):
    if not raw:
        return None
    with connection() as conn:
        return one(
            conn,
            """
            SELECT u.* FROM app_sessions s JOIN accounts_user u ON u.id=s.user_id
            WHERE s.token_hash=%s AND s.expires_at>now() AND u.is_active
              AND u.email_verified_at IS NOT NULL AND u.auth_version=s.auth_version
            """,
            (_hash(raw),),
        )


def csrf_token(session):
    return hmac.new(_secret(), session.encode(), hashlib.sha256).hexdigest()


def csrf_valid(session, supplied):
    return bool(session and supplied) and hmac.compare_digest(csrf_token(session), supplied)
