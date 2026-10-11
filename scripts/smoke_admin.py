"""Functional admin smoke check with retired temporary records."""
from datetime import datetime, timezone
import http.cookiejar
import re
import secrets
import urllib.parse
import urllib.request
from pathlib import Path
import sys
from uuid import uuid4

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi_app.security import make_password


BASE = "http://127.0.0.1:8000"
suffix = secrets.token_hex(4)
admin_email = f"fastapi-admin-{suffix}@kp11.ru"
employee_email = f"fastapi-employee-{suffix}@yandex.ru"
password = "AdminPassword-123"


def db():
    import os
    return psycopg.connect(dbname=os.environ["POSTGRES_DB"], user=os.environ["DB_APP_USER"], password=os.environ["DB_APP_PASSWORD"], host=os.environ["DB_HOST"], port=os.environ["DB_PORT"], row_factory=psycopg.rows.dict_row)


def token(body):
    return re.search(r'name="csrf_token" value="([^"]+)"', body).group(1)


def create_admin():
    uid = uuid4()
    now = datetime.now(timezone.utc)
    with db() as conn:
        with conn.transaction():
            conn.execute("INSERT INTO accounts_user(id,full_name,email,password,is_active,email_verified_at,auth_version,version,created_at,updated_at) VALUES (%s,'FastAPI admin smoke',%s,%s,true,%s,1,1,%s,%s)", (uid, admin_email, make_password(password), now, now, now))
            role = conn.execute("SELECT id FROM access_role WHERE is_system_admin=true LIMIT 1").fetchone()
            conn.execute("INSERT INTO access_userrole(user_id,role_id,created_at) VALUES (%s,%s,%s)", (uid, role["id"], now))
    return uid


uid = create_admin()
try:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler(), urllib.request.HTTPCookieProcessor(jar))
    with opener.open(urllib.request.Request(BASE + "/accounts/login/", headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        csrf = token(response.read().decode())
    with opener.open(urllib.request.Request(BASE + "/accounts/login/", data=urllib.parse.urlencode({"csrf_token": csrf, "email": admin_email, "password": password}).encode(), headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        assert response.geturl().endswith("/")
    with opener.open(urllib.request.Request(BASE + "/accounts/administration/", headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        body = response.read().decode()
        assert "Сотрудники" in body and admin_email in body
        csrf = token(body)
    with opener.open(urllib.request.Request(BASE + "/workitems/new/", headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        csrf = token(response.read().decode())
    title = "FastAPI workflow smoke " + suffix
    with opener.open(urllib.request.Request(BASE + "/workitems/new/", data=urllib.parse.urlencode({"csrf_token": csrf, "title": title, "description": "workflow", "kind": "task", "due_at": "2030-01-01T12:00", "event_at": ""}).encode(), headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        assert "/workitems/" in response.geturl()
    with opener.open(urllib.request.Request(BASE + "/", headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        body = response.read().decode()
        assert title in body
        assignment_id = re.search(r"/assignments/([0-9a-f-]+)/completion/", body).group(1)
        csrf = token(body)
    with opener.open(urllib.request.Request(BASE + f"/assignments/{assignment_id}/completion/", data=urllib.parse.urlencode({"csrf_token": csrf, "completed": "true"}).encode(), headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        assert response.geturl().endswith("/")
    with opener.open(urllib.request.Request(BASE + "/?tab=completed", headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        assert title in response.read().decode()
    with opener.open(urllib.request.Request(BASE + "/accounts/invite/", data=urllib.parse.urlencode({"csrf_token": csrf, "email": employee_email, "full_name": "FastAPI invited smoke"}).encode(), headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        invite_body = response.read().decode()
        assert "Приглаш" in invite_body, invite_body[-500:]
    with db() as conn:
        employee = conn.execute("SELECT id FROM accounts_user WHERE email=%s", (employee_email,)).fetchone()
    assert employee
    with opener.open(urllib.request.Request(BASE + f"/accounts/users/{employee['id']}/", headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        body = response.read().decode()
        csrf = token(body)
    with opener.open(urllib.request.Request(BASE + f"/accounts/users/{employee['id']}/", data=urllib.parse.urlencode({"csrf_token": csrf, "full_name": "FastAPI invited smoke", "is_active": ""}).encode(), headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        assert response.geturl().endswith("/accounts/administration/")
    with db() as conn:
        assert not conn.execute("SELECT is_active FROM accounts_user WHERE id=%s", (employee["id"],)).fetchone()["is_active"]
    print("Admin/workflow: permission gate, task creation, active/completed tabs, invitation and employee deactivation — OK")
finally:
    with db() as conn:
        with conn.transaction():
            conn.execute("UPDATE accounts_user SET is_active=false,email_verified_at=NULL,full_name='Retired admin smoke',updated_at=now() WHERE email IN (%s,%s)", (admin_email, employee_email))
