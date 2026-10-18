"""Functional admin smoke check with retired temporary records."""
from datetime import datetime, timezone
import http.cookiejar
import re
import secrets
import urllib.parse
import urllib.request
from urllib.error import HTTPError
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
        new_body = response.read().decode()
        csrf = token(new_body)
        assert 'class="form-grid issue-dates event-fields" hidden' in new_body
    title = "FastAPI workflow smoke " + suffix
    with opener.open(urllib.request.Request(BASE + "/workitems/new/", data=urllib.parse.urlencode({"csrf_token": csrf, "title": title, "description": "workflow", "kind": "task", "due_at": "2030-01-01T12:00", "event_at": ""}).encode(), headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        assert "/workitems/" in response.geturl()
    with opener.open(urllib.request.Request(BASE + "/", headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        body = response.read().decode()
        assert title in body
        assert "task-card-hitarea" in body
        assert re.search(r'<a class="task-card-hitarea" href="/workitems/[^\"]+/"><div class="card-meta">.*?<h2>' + re.escape(title) + r'</h2><p class="task-description">workflow</p></a>', body, re.S)
        assignment_id = re.search(r"/assignments/([0-9a-f-]+)/completion/", body).group(1)
        csrf = token(body)
    with opener.open(urllib.request.Request(BASE + f"/assignments/{assignment_id}/completion/", data=urllib.parse.urlencode({"csrf_token": csrf, "completed": "true"}).encode(), headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        assert response.geturl().endswith("/")
    with opener.open(urllib.request.Request(BASE + "/?tab=completed", headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        assert title in response.read().decode()

    with opener.open(urllib.request.Request(BASE + "/workitems/new/", headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        switch_csrf = token(response.read().decode())
    with opener.open(urllib.request.Request(BASE + "/workitems/new/", data=urllib.parse.urlencode({"csrf_token": switch_csrf, "title": "FastAPI type switch smoke " + suffix, "description": "type switch", "kind": "task", "due_at": "2030-01-01T12:00", "event_at": ""}).encode(), headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        switch_id = response.geturl().rstrip("/").split("/")[-1]
    with opener.open(urllib.request.Request(BASE + f"/workitems/{switch_id}/edit/", headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        switch_csrf = token(response.read().decode())
    switch_event = urllib.parse.urlencode({"csrf_token": switch_csrf, "title": "FastAPI type switch smoke " + suffix, "description": "type switch", "kind": "event", "due_date": "2030-01-01", "due_time": "12:00", "event_date": "2030-01-02", "event_time": "12:00", "assignee_ids": str(uid)})
    with opener.open(urllib.request.Request(BASE + f"/workitems/{switch_id}/edit/", data=switch_event.encode(), headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        assert response.geturl().endswith(f"/workitems/{switch_id}/")
    with db() as conn:
        switched = conn.execute("SELECT kind,event_at FROM workitems_workitem WHERE id=%s", (switch_id,)).fetchone()
        assert switched["kind"] == "event" and switched["event_at"] is not None
    with opener.open(urllib.request.Request(BASE + f"/workitems/{switch_id}/edit/", headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        switch_body = response.read().decode()
        assert 'class="form-grid issue-dates event-fields" hidden' not in switch_body and "ДАТА СОБЫТИЯ" in switch_body
        switch_csrf = token(switch_body)
    switch_task = urllib.parse.urlencode({"csrf_token": switch_csrf, "title": "FastAPI type switch smoke " + suffix, "description": "type switch", "kind": "task", "due_date": "2030-01-01", "due_time": "12:00", "event_date": "", "event_time": "", "assignee_ids": str(uid)})
    with opener.open(urllib.request.Request(BASE + f"/workitems/{switch_id}/edit/", data=switch_task.encode(), headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        assert response.geturl().endswith(f"/workitems/{switch_id}/")
    with db() as conn:
        switched = conn.execute("SELECT kind,event_at FROM workitems_workitem WHERE id=%s", (switch_id,)).fetchone()
        assert switched["kind"] == "task" and switched["event_at"] is None
        with conn.transaction():
            conn.execute("DELETE FROM workitems_assignment WHERE work_item_id=%s", (switch_id,))
            conn.execute("DELETE FROM workitems_workitem WHERE id=%s", (switch_id,))

    with opener.open(urllib.request.Request(BASE + "/workitems/new/", headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        csrf = token(response.read().decode())
    delete_task_title = "FastAPI delete task smoke " + suffix
    create_delete_task = urllib.parse.urlencode([("csrf_token", csrf), ("title", delete_task_title), ("description", "delete workflow"), ("kind", "task"), ("due_at", "2030-01-01T12:00"), ("event_at", ""), ("subtasks", "one"), ("subtasks", "two")])
    with opener.open(urllib.request.Request(BASE + "/workitems/new/", data=create_delete_task.encode(), headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        delete_task_id = response.geturl().rstrip("/").split("/")[-1]
    with opener.open(urllib.request.Request(BASE + f"/workitems/{delete_task_id}/", headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        detail_body = response.read().decode()
        assert "Отметить выполненной" in detail_body and "disabled" in detail_body
        assert "ДАТА СОБЫТИЯ" not in detail_body
    with opener.open(urllib.request.Request(BASE + f"/workitems/{delete_task_id}/edit/", headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        edit_body = response.read().decode()
        assert "УДАЛИТЬ" in edit_body and "delete-workitem-dialog" in edit_body and "Удалить только у себя" in edit_body
        csrf = token(edit_body)
    with opener.open(urllib.request.Request(BASE + f"/workitems/{delete_task_id}/delete/", data=urllib.parse.urlencode({"csrf_token": csrf, "delete_scope": "self"}).encode(), headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        assert response.geturl().endswith("/")
    with db() as conn:
        assert conn.execute("SELECT 1 FROM workitems_workitem WHERE id=%s", (delete_task_id,)).fetchone() is not None
        assert conn.execute("SELECT 1 FROM workitems_assignment WHERE work_item_id=%s AND user_id=%s AND state='active'", (delete_task_id, uid)).fetchone() is not None
        assert conn.execute("SELECT 1 FROM workitems_userarchive WHERE work_item_id=%s AND user_id=%s", (delete_task_id, uid)).fetchone() is not None
    try:
        opener.open(urllib.request.Request(BASE + f"/workitems/{delete_task_id}/edit/", headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5)
    except HTTPError as error:
        assert error.code == 404
    else:
        raise AssertionError("self-deleted task remained editable for its owner")
    with db() as conn:
        with conn.transaction():
            conn.execute("DELETE FROM workitems_userarchive WHERE work_item_id=%s", (delete_task_id,))
            conn.execute("DELETE FROM workitems_subtaskprogress WHERE work_item_id=%s", (delete_task_id,))
            conn.execute("DELETE FROM workitems_subtask WHERE work_item_id=%s", (delete_task_id,))
            conn.execute("DELETE FROM workitems_assignment WHERE work_item_id=%s", (delete_task_id,))
            conn.execute("DELETE FROM workitems_workitem WHERE id=%s", (delete_task_id,))

    with opener.open(urllib.request.Request(BASE + "/workitems/new/", headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        csrf = token(response.read().decode())
    delete_event_title = "FastAPI delete event smoke " + suffix
    with opener.open(urllib.request.Request(BASE + "/workitems/new/", data=urllib.parse.urlencode({"csrf_token": csrf, "title": delete_event_title, "description": "delete event workflow", "kind": "event", "due_at": "2030-01-01T12:00", "event_at": "2030-01-02T12:00"}).encode(), headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        delete_event_id = response.geturl().rstrip("/").split("/")[-1]
    with opener.open(urllib.request.Request(BASE + "/", headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        event_body = response.read().decode()
        assert f'class="button" href="/events/{delete_event_id}/chat/">чат</a>' in event_body
    with opener.open(urllib.request.Request(BASE + f"/events/{delete_event_id}/chat/", headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        csrf = token(response.read().decode())
    with opener.open(urllib.request.Request(BASE + f"/events/{delete_event_id}/chat/send/", data=urllib.parse.urlencode({"csrf_token": csrf, "text": "message before delete"}).encode(), headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        assert response.geturl().endswith(f"/events/{delete_event_id}/chat/")
    with opener.open(urllib.request.Request(BASE + f"/workitems/{delete_event_id}/edit/", headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        event_edit_body = response.read().decode()
        csrf = token(event_edit_body)
        assert 'class="form-grid issue-dates event-fields" hidden' not in event_edit_body and "ДАТА СОБЫТИЯ" in event_edit_body
    with opener.open(urllib.request.Request(BASE + f"/workitems/{delete_event_id}/delete/", data=urllib.parse.urlencode({"csrf_token": csrf}).encode(), headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5) as response:
        assert response.geturl().endswith("/")
    with db() as conn:
        assert conn.execute("SELECT archived_at FROM workitems_workitem WHERE id=%s AND archived_at IS NOT NULL", (delete_event_id,)).fetchone() is not None
        assert conn.execute("SELECT 1 FROM workitems_assignment WHERE work_item_id=%s AND state='active'", (delete_event_id,)).fetchone() is None
        assert conn.execute("SELECT 1 FROM discussions_eventmessage WHERE work_item_id=%s", (delete_event_id,)).fetchone() is not None
        assert conn.execute("SELECT 1 FROM audit_auditentry WHERE object_id=%s", (delete_event_id,)).fetchone() is not None
    for path in (f"/workitems/{delete_event_id}/", f"/events/{delete_event_id}/chat/"):
        try:
            opener.open(urllib.request.Request(BASE + path, headers={"User-Agent": "fastapi-admin-smoke"}), timeout=5)
        except HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError(f"globally deleted event remained visible at {path}")
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
    print("Admin/workflow: permission gate, task card rendering/progress gate, active/completed tabs, task/event deletion scopes, invitation and employee deactivation — OK")
finally:
    with db() as conn:
        with conn.transaction():
            conn.execute("UPDATE accounts_user SET is_active=false,email_verified_at=NULL,full_name='Retired admin smoke',updated_at=now() WHERE email IN (%s,%s)", (admin_email, employee_email))
