"""Functional authentication check; creates and removes only temporary users."""
import json
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from uuid import uuid4

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = "http://127.0.0.1:8000"
MAILPIT = "http://mailpit:8025"
suffix = secrets.token_hex(4)
email = f"fastapi-auth-{suffix}@kp11.ru"
reset_email = f"fastapi-reset-{suffix}@yandex.ru"
old_password = "OldPassword-123"
new_password = "NewPassword-456"


def db():
    import os
    return psycopg.connect(dbname=os.environ["POSTGRES_DB"], user=os.environ["DB_APP_USER"], password=os.environ["DB_APP_PASSWORD"], host=os.environ["DB_HOST"], port=os.environ["DB_PORT"], row_factory=psycopg.rows.dict_row)


def create_user(address, password):
    from fastapi_app.security import make_password
    uid = uuid4()
    now = datetime.now(timezone.utc)
    with db() as conn:
        with conn.transaction():
            conn.execute("INSERT INTO accounts_user(id,full_name,email,password,is_active,email_verified_at,auth_version,version,created_at,updated_at) VALUES (%s,%s,%s,%s,true,%s,1,1,%s,%s)", (uid, "FastAPI smoke", address, make_password(password), now, now, now))
    return uid


def csrf(body):
    return re.search(r'name="csrf_token" value="([^"]+)"', body).group(1)


def page(jar, path):
    with urllib.request.urlopen(urllib.request.Request(BASE + path, headers={"User-Agent": "fastapi-auth-smoke"}), timeout=5) as response:
        return response.read().decode(), response


def post(jar, path, fields):
    data = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(BASE + path, data=data, headers={"User-Agent": "fastapi-auth-smoke"})
    return urllib.request.urlopen(request, timeout=5)


def login(address, password):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler(), urllib.request.HTTPCookieProcessor(jar))
    request = urllib.request.Request(BASE + "/accounts/login/", headers={"User-Agent": "fastapi-auth-smoke"})
    with opener.open(request, timeout=5) as response:
        token = csrf(response.read().decode())
    request = urllib.request.Request(BASE + "/accounts/login/", data=urllib.parse.urlencode({"csrf_token": token, "email": address, "password": password}).encode(), headers={"User-Agent": "fastapi-auth-smoke"})
    response = opener.open(request, timeout=5)
    return jar, opener, response


ids = []
try:
    ids.extend([create_user(email, old_password), create_user(reset_email, old_password)])
    jar, opener, response = login(email, old_password)
    assert response.geturl().endswith("/") and "Мои задачи" in response.read().decode()
    with opener.open(BASE + "/accounts/password/", timeout=5) as response:
        token = csrf(response.read().decode())
    request = urllib.request.Request(BASE + "/accounts/password/", data=urllib.parse.urlencode({"csrf_token": token, "old_password": old_password, "password": new_password, "password_confirm": new_password}).encode(), headers={"User-Agent": "fastapi-auth-smoke"})
    response = opener.open(request, timeout=5)
    assert response.geturl().endswith("/accounts/login/")
    _, _, response = login(email, new_password)
    assert response.geturl().endswith("/")

    recovery_jar = http.cookiejar.CookieJar()
    recovery_opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler(), urllib.request.HTTPCookieProcessor(recovery_jar))
    with recovery_opener.open(urllib.request.Request(BASE + "/accounts/recovery/", headers={"User-Agent": "fastapi-recovery-smoke"}), timeout=5) as response:
        token = csrf(response.read().decode())
    request = urllib.request.Request(BASE + "/accounts/recovery/", data=urllib.parse.urlencode({"csrf_token": token, "email": reset_email}).encode(), headers={"User-Agent": "fastapi-recovery-smoke"})
    with recovery_opener.open(request, timeout=5) as response:
        assert "Если адрес принадлежит" in response.read().decode()
    # The SMTP delivery is covered by smoke.py. For the one-time transaction,
    # insert a deterministic grant here so this check does not depend on the
    # Mailpit admin API being reachable from the web container.
    from fastapi_app.auth import token_digest
    reset_token = f"{uuid4()}:{secrets.token_urlsafe(24)}"
    now = datetime.now(timezone.utc)
    with db() as conn:
        with conn.transaction():
            user = conn.execute("SELECT id,auth_version FROM accounts_user WHERE email=%s", (reset_email,)).fetchone()
            conn.execute("UPDATE accounts_passwordresetgrant SET revoked_at=%s WHERE user_id=%s AND used_at IS NULL AND revoked_at IS NULL", (now, user["id"]))
            conn.execute("INSERT INTO accounts_passwordresetgrant(id,token_digest,auth_version,created_at,expires_at,user_id) VALUES (%s,%s,%s,%s,%s,%s)", (reset_token.split(":", 1)[0], token_digest(reset_token), user["auth_version"], now, now + timedelta(hours=1), user["id"]))
    with recovery_opener.open(urllib.request.Request(BASE + "/accounts/reset/?token=" + urllib.parse.quote(reset_token), headers={"User-Agent": "fastapi-recovery-smoke"}), timeout=5) as response:
        token = csrf(response.read().decode())
    request = urllib.request.Request(BASE + "/accounts/reset/", data=urllib.parse.urlencode({"csrf_token": token, "token": reset_token, "password": new_password, "password_confirm": new_password}).encode(), headers={"User-Agent": "fastapi-recovery-smoke"})
    with recovery_opener.open(request, timeout=5) as response:
        assert "Пароль сохранен" in response.read().decode()
    _, _, response = login(reset_email, new_password)
    assert response.geturl().endswith("/")
    print("Auth: login, session rotation, recovery request and one-time reset — OK")
finally:
    if ids:
        with db() as conn:
            with conn.transaction():
                # Audit rows are append-only and deliberately retain actor IDs.
                # Retire the temporary accounts instead of deleting their history.
                conn.execute("UPDATE accounts_user SET full_name='Retired smoke user', is_active=false, email_verified_at=NULL, updated_at=now() WHERE id = ANY(%s)", (ids,))
