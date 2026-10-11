"""Create the one-time system administrator invitation for a new install."""

from datetime import datetime, timedelta, timezone
import argparse
import os
from pathlib import Path
import secrets
import sys
from urllib.parse import urlencode
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi_app.auth import _send, normalize_email, token_digest
from fastapi_app.db import one, transaction


def main():
    parser = argparse.ArgumentParser(description="Create the first KP11 administrator invitation.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--full-name", required=True)
    args = parser.parse_args()
    try:
        email = normalize_email(args.email)
    except ValueError as error:
        parser.error(str(error))
    full_name = args.full_name.strip()
    if not full_name:
        parser.error("Укажите ФИО администратора.")

    now = datetime.now(timezone.utc)
    with transaction() as conn:
        if one(conn, "SELECT 1 FROM access_userrole ur JOIN access_role r ON r.id=ur.role_id JOIN accounts_user u ON u.id=ur.user_id WHERE r.is_system_admin AND r.is_active AND u.is_active LIMIT 1"):
            raise SystemExit("Bootstrap уже выполнен: системный администратор существует.")
        user = one(conn, "SELECT id,is_active,email_verified_at FROM accounts_user WHERE email=%s FOR UPDATE", (email,))
        if user and (not user["is_active"] or user["email_verified_at"]):
            raise SystemExit("Для этого адреса bootstrap недоступен: учетная запись уже активирована или отключена.")
        if user:
            user_id = user["id"]
            conn.execute("UPDATE accounts_user SET full_name=%s,invited_at=%s,updated_at=%s WHERE id=%s", (full_name, now, now, user_id))
        else:
            user_id = uuid4()
            conn.execute("INSERT INTO accounts_user(id,full_name,email,password,is_active,invited_at,auth_version,version,created_at,updated_at) VALUES (%s,%s,%s,'',true,%s,1,1,%s,%s)", (user_id, full_name, email, now, now, now))
        role = one(conn, "SELECT id FROM access_role WHERE is_system_admin AND is_active LIMIT 1")
        if not role:
            raise SystemExit("В базе отсутствует активная системная роль администратора.")
        conn.execute("INSERT INTO access_userrole(user_id,role_id,created_at) VALUES (%s,%s,%s)", (user_id, role["id"], now))
        conn.execute("UPDATE accounts_invitation SET revoked_at=%s WHERE user_id=%s AND used_at IS NULL AND revoked_at IS NULL", (now, user_id))
        invitation_id = uuid4()
        token = f"{invitation_id}:{secrets.token_urlsafe(32)}"
        conn.execute("INSERT INTO accounts_invitation(id,email_snapshot,token_digest,created_at,expires_at,version,created_by_id,user_id) VALUES (%s,%s,%s,%s,%s,1,NULL,%s)", (invitation_id, email, token_digest(token), now, now + timedelta(hours=48), user_id))
        conn.execute("INSERT INTO audit_auditentry(actor_id,actor_label,action,object_type,object_id,occurred_at,request_id,changes,reason) VALUES (NULL,%s,'accounts.bootstrap_started','user',%s,%s,%s,'{}','')", ("bootstrap", str(user_id), now, uuid4()))

    url = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/") + "/accounts/activate/?" + urlencode({"token": token})
    if not _send("Приглашение администратора в трекер КП11", email, "Откройте ссылку и задайте пароль администратора:\n" + url):
        raise SystemExit("Приглашение создано, но SMTP-доставка не подтверждена. Проверьте Mailpit/SMTP.")
    print("Приглашение администратора отправлено в SMTP. Откройте письмо в Mailpit.")


if __name__ == "__main__":
    main()
