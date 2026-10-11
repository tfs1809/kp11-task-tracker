"""Account workflows shared by the FastAPI routes."""

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import hashlib
import os
import secrets
import smtplib
from urllib.parse import urlencode
from uuid import UUID, uuid4

from .db import many, one, transaction
from .security import make_password, password_error, verify_password

INVALID_LINK = "Ссылка недействительна или срок ее действия истек. Запросите новую."


def normalize_email(value):
    email = value.strip().lower()
    if "@" not in email or not email.isascii():
        raise ValueError("Используйте адрес на домене kp11.ru или yandex.ru.")
    domain = email.rsplit("@", 1)[1]
    if domain not in {"kp11.ru", "yandex.ru"}:
        raise ValueError("Допустим только точный домен kp11.ru или yandex.ru.")
    return email


def token_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _send(subject, recipient, body):
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = os.environ["DEFAULT_FROM_EMAIL"]
    message["To"] = recipient
    message.set_content(body)
    try:
        with smtplib.SMTP(os.environ.get("EMAIL_HOST", "localhost"), int(os.environ.get("EMAIL_PORT", "25")), timeout=10) as smtp:
            smtp.send_message(message)
        return True
    except (OSError, smtplib.SMTPException):
        return False


def issue_recovery(email):
    """Issue one short-lived reset grant without revealing account existence."""
    email = normalize_email(email)
    with transaction() as conn:
        user = one(conn, "SELECT id,email,auth_version,is_active,email_verified_at,password FROM accounts_user WHERE email=%s", (email,))
        if not user or not user["is_active"] or not user["email_verified_at"] or not user["password"]:
            return False
        now = datetime.now(timezone.utc)
        conn.execute("UPDATE accounts_passwordresetgrant SET revoked_at=%s WHERE user_id=%s AND used_at IS NULL AND revoked_at IS NULL", (now, user["id"]))
        grant_id = uuid4()
        nonce = secrets.token_urlsafe(32)
        token = f"{grant_id}:{nonce}"
        conn.execute(
            "INSERT INTO accounts_passwordresetgrant(id,token_digest,auth_version,created_at,expires_at,user_id) VALUES (%s,%s,%s,%s,%s,%s)",
            (grant_id, token_digest(token), user["auth_version"], now, now + timedelta(hours=1), user["id"]),
        )
        conn.execute(
            "INSERT INTO audit_auditentry(actor_id,actor_label,action,object_type,object_id,occurred_at,request_id,changes,reason) VALUES (NULL,%s,'accounts.recovery_requested','user',%s,%s,%s,'{}','')",
            ("system", str(user["id"]), now, uuid4()),
        )
    path = "/accounts/reset/?" + urlencode({"token": token})
    url = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/") + path
    return _send("Восстановление доступа к трекеру КП11", email, "Откройте ссылку и задайте новый пароль:\n" + url)


def issue_invitation(actor_id, email, full_name):
    email = normalize_email(email)
    if not full_name.strip():
        raise ValueError("Укажите ФИО сотрудника.")
    with transaction() as conn:
        now = datetime.now(timezone.utc)
        conn.execute("UPDATE accounts_invitation SET revoked_at=%s WHERE user_id IN (SELECT id FROM accounts_user WHERE email=%s) AND used_at IS NULL AND revoked_at IS NULL", (now, email))
        user = one(conn, "SELECT id,is_active,email_verified_at FROM accounts_user WHERE email=%s", (email,))
        if user:
            if user["email_verified_at"] or not user["is_active"]:
                raise ValueError("Для этого адреса приглашение недоступно.")
            user_id = user["id"]
            conn.execute("UPDATE accounts_user SET full_name=%s,invited_at=%s,updated_at=%s WHERE id=%s", (full_name.strip(), now, now, user_id))
        else:
            user_id = uuid4()
            conn.execute("INSERT INTO accounts_user(id,full_name,email,password,is_active,invited_at,auth_version,version,created_at,updated_at) VALUES (%s,%s,%s,'',true,%s,1,1,%s,%s)", (user_id, full_name.strip(), email, now, now, now))
        invitation_id = uuid4()
        nonce = secrets.token_urlsafe(32)
        token = f"{invitation_id}:{nonce}"
        conn.execute("INSERT INTO accounts_invitation(id,email_snapshot,token_digest,created_at,expires_at,version,created_by_id,user_id) VALUES (%s,%s,%s,%s,%s,1,%s,%s)", (invitation_id, email, token_digest(token), now, now + timedelta(hours=48), actor_id, user_id))
        actor = one(conn, "SELECT full_name FROM accounts_user WHERE id=%s", (actor_id,))
        conn.execute("INSERT INTO audit_auditentry(actor_id,actor_label,action,object_type,object_id,occurred_at,request_id,changes,reason) VALUES (%s,%s,'accounts.invitation_sent','user',%s,%s,%s,'{}','')", (actor_id, actor["full_name"], str(user_id), now, uuid4()))
    path = "/accounts/activate/?" + urlencode({"token": token})
    url = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/") + path
    return _send("Приглашение в трекер КП11", email, "Откройте ссылку и задайте собственный пароль:\n" + url), user_id


def find_invitation(token):
    try:
        invitation_id, _ = token.split(":", 1)
        UUID(invitation_id)
    except (ValueError, AttributeError):
        return None
    with transaction() as conn:
        invitation = one(conn, "SELECT i.*,u.email,u.is_active,u.email_verified_at FROM accounts_invitation i JOIN accounts_user u ON u.id=i.user_id WHERE i.id=%s", (invitation_id,))
        if not invitation:
            return None
        now = datetime.now(timezone.utc)
        if invitation["used_at"] or invitation["revoked_at"] or invitation["expires_at"] <= now or not invitation["is_active"] or invitation["email_verified_at"] or not secrets.compare_digest(invitation["token_digest"], token_digest(token)):
            return None
        return invitation


def activate(token, password):
    error = password_error(password)
    if error:
        return False, error
    try:
        invitation_id, _ = token.split(":", 1)
        UUID(invitation_id)
    except (ValueError, AttributeError):
        return False, INVALID_LINK
    with transaction() as conn:
        invitation = one(conn, "SELECT i.*,u.full_name,u.email,u.is_active,u.email_verified_at,u.auth_version FROM accounts_invitation i JOIN accounts_user u ON u.id=i.user_id WHERE i.id=%s FOR UPDATE", (invitation_id,))
        now = datetime.now(timezone.utc)
        if not invitation or invitation["used_at"] or invitation["revoked_at"] or invitation["expires_at"] <= now or not invitation["is_active"] or invitation["email_verified_at"] or not secrets.compare_digest(invitation["token_digest"], token_digest(token)):
            return False, INVALID_LINK
        conn.execute("UPDATE accounts_user SET password=%s,email_verified_at=%s,auth_version=auth_version+1,version=version+1,updated_at=%s WHERE id=%s", (make_password(password), now, now, invitation["user_id"]))
        conn.execute("UPDATE accounts_invitation SET used_at=%s,version=version+1 WHERE id=%s", (now, invitation["id"]))
        conn.execute("INSERT INTO audit_auditentry(actor_id,actor_label,action,object_type,object_id,occurred_at,request_id,changes,reason) VALUES (%s,%s,'accounts.activated','user',%s,%s,%s,'{}','')", (invitation["user_id"], invitation["full_name"], str(invitation["user_id"]), now, uuid4()))
    return True, ""


def find_reset(token):
    try:
        grant_id, _ = token.split(":", 1)
        UUID(grant_id)
    except (ValueError, AttributeError):
        return None
    with transaction() as conn:
        grant = one(conn, "SELECT g.*,u.email,u.is_active,u.email_verified_at,u.auth_version AS user_auth_version FROM accounts_passwordresetgrant g JOIN accounts_user u ON u.id=g.user_id WHERE g.id=%s", (grant_id,))
        if not grant:
            return None
        now = datetime.now(timezone.utc)
        if grant["used_at"] or grant["revoked_at"] or grant["expires_at"] <= now or not grant["is_active"] or not grant["email_verified_at"] or grant["auth_version"] != grant["user_auth_version"]:
            return None
        if not secrets.compare_digest(grant["token_digest"], token_digest(token)):
            return None
        return grant


def reset_password(token, password):
    error = password_error(password)
    if error:
        return False, error
    try:
        grant_id, _ = token.split(":", 1)
        UUID(grant_id)
    except (ValueError, AttributeError):
        return False, INVALID_LINK
    with transaction() as conn:
        grant = one(conn, "SELECT g.*,u.full_name,u.email,u.password,u.is_active,u.email_verified_at,u.auth_version AS user_auth_version FROM accounts_passwordresetgrant g JOIN accounts_user u ON u.id=g.user_id WHERE g.id=%s FOR UPDATE", (grant_id,))
        now = datetime.now(timezone.utc)
        if not grant or grant["used_at"] or grant["revoked_at"] or grant["expires_at"] <= now or not grant["is_active"] or not grant["email_verified_at"] or grant["auth_version"] != grant["user_auth_version"] or not secrets.compare_digest(grant["token_digest"], token_digest(token)):
            return False, INVALID_LINK
        new_version = grant["auth_version"] + 1
        conn.execute("UPDATE accounts_user SET password=%s,auth_version=%s,version=version+1,updated_at=%s WHERE id=%s", (make_password(password), new_version, now, grant["user_id"]))
        conn.execute("UPDATE accounts_passwordresetgrant SET used_at=%s WHERE id=%s", (now, grant["id"]))
        conn.execute("INSERT INTO audit_auditentry(actor_id,actor_label,action,object_type,object_id,occurred_at,request_id,changes,reason) VALUES (%s,%s,'accounts.password_reset','user',%s,%s,%s,'{}','')", (grant["user_id"], grant["full_name"], str(grant["user_id"]), now, uuid4()))
    return True, ""


def change_password(user_id, old_password, new_password):
    error = password_error(new_password)
    if error:
        return False, error
    with transaction() as conn:
        user = one(conn, "SELECT id,full_name,password,auth_version FROM accounts_user WHERE id=%s FOR UPDATE", (user_id,))
        if not user or not verify_password(user["password"], old_password):
            return False, "Текущий пароль неверен."
        if old_password == new_password:
            return False, "Новый пароль должен отличаться от текущего."
        now = datetime.now(timezone.utc)
        new_version = user["auth_version"] + 1
        conn.execute("UPDATE accounts_user SET password=%s,auth_version=%s,version=version+1,updated_at=%s WHERE id=%s", (make_password(new_password), new_version, now, user_id))
        conn.execute("UPDATE accounts_passwordresetgrant SET revoked_at=%s WHERE user_id=%s AND used_at IS NULL AND revoked_at IS NULL", (now, user_id))
        conn.execute("DELETE FROM app_sessions WHERE user_id=%s", (user_id,))
        conn.execute("INSERT INTO audit_auditentry(actor_id,actor_label,action,object_type,object_id,occurred_at,request_id,changes,reason) VALUES (%s,%s,'accounts.password_changed','user',%s,%s,%s,'{}','')", (user_id, user["full_name"], str(user_id), now, uuid4()))
    return True, ""
