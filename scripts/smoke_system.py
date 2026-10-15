"""Exercise system management and fixed role/group audience expansion."""
import http.cookiejar
import os
import sys
import re
import secrets
from pathlib import Path
import urllib.parse
import urllib.request
from urllib.error import HTTPError
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi_app.security import make_password


BASE = "http://127.0.0.1:8000"
suffix = secrets.token_hex(4)
password = "SystemSmoke-123"
admin_email = f"system-admin-{suffix}@kp11.ru"
first_email = f"system-one-{suffix}@kp11.ru"
second_email = f"system-two-{suffix}@yandex.ru"
role_name = f"Smoke Audience Role {suffix}"
group_name = f"Smoke Group {suffix}"
ids = {"admin": None, "first": None, "second": None, "role": None, "group": None, "admin_role": None}
items = []


def db():
    return psycopg.connect(
        dbname=os.environ["POSTGRES_DB"], user=os.environ["DB_APP_USER"], password=os.environ["DB_APP_PASSWORD"],
        host=os.environ["DB_HOST"], port=os.environ["DB_PORT"], row_factory=psycopg.rows.dict_row,
    )


def csrf(body):
    return re.search(r'name="csrf_token" value="([^"]+)"', body).group(1)


def create_user(email, name):
    user_id = uuid4()
    now = datetime.now(timezone.utc)
    with db() as conn:
        with conn.transaction():
            conn.execute(
                """INSERT INTO accounts_user(id,full_name,email,password,is_active,email_verified_at,auth_version,version,created_at,updated_at)
                   VALUES (%s,%s,%s,%s,true,%s,1,1,%s,%s)""",
                (user_id, name, email, make_password(password), now, now, now),
            )
    return user_id


def login(email):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler(), urllib.request.HTTPCookieProcessor(jar))
    headers = {"User-Agent": "system-feature-smoke"}
    with opener.open(urllib.request.Request(BASE + "/accounts/login/", headers=headers), timeout=5) as response:
        token = csrf(response.read().decode())
    with opener.open(urllib.request.Request(BASE + "/accounts/login/", data=urllib.parse.urlencode({"csrf_token": token, "email": email, "password": password}).encode(), headers=headers), timeout=5) as response:
        assert response.geturl().endswith("/")
    return opener


def get(opener, path):
    return opener.open(urllib.request.Request(BASE + path, headers={"User-Agent": "system-feature-smoke"}), timeout=5)


def post(opener, path, fields):
    return opener.open(urllib.request.Request(BASE + path, data=urllib.parse.urlencode(fields, doseq=True).encode(), headers={"User-Agent": "system-feature-smoke"}), timeout=5)


def expect_status(opener, path, fields, status):
    try:
        post(opener, path, fields)
    except HTTPError as error:
        assert error.code == status, (path, error.code, status, error.read().decode())
    else:
        raise AssertionError(f"{path} unexpectedly succeeded")


def expect_get_status(opener, path, status):
    try:
        get(opener, path)
    except HTTPError as error:
        assert error.code == status, (path, error.code, status)
    else:
        raise AssertionError(f"{path} unexpectedly succeeded")


try:
    ids["admin"] = create_user(admin_email, "System Smoke Admin")
    ids["first"] = create_user(first_email, "System Smoke One")
    ids["second"] = create_user(second_email, "System Smoke Two")
    with db() as conn:
        with conn.transaction():
            ids["admin_role"] = conn.execute("SELECT id FROM access_role WHERE is_system_admin=true LIMIT 1").fetchone()["id"]
            conn.execute("INSERT INTO access_userrole(user_id,role_id,created_at) VALUES (%s,%s,now())", (ids["admin"], ids["admin_role"]))

    opener = login(admin_email)
    regular_opener = login(second_email)
    expect_get_status(regular_opener, "/accounts/system/", 403)
    with get(opener, "/accounts/system/") as response:
        body = response.read().decode()
        assert all(marker in body for marker in ("СИСТЕМА", "ПОЛЬЗОВАТЕЛИ", "РОЛИ И ПРАВА", "СОЗДАТЬ ГРУППУ"))
        token = csrf(body)
    with post(opener, "/accounts/system/roles/create/", {"csrf_token": token, "name": role_name}):
        pass
    with db() as conn:
        ids["role"] = conn.execute("SELECT id FROM access_role WHERE name=%s", (role_name,)).fetchone()["id"]
    with get(opener, "/accounts/system/") as response:
        token = csrf(response.read().decode())
    expect_status(opener, "/accounts/system/roles/create/", {"csrf_token": token, "name": role_name.lower()}, 409)
    with get(opener, "/accounts/system/") as response:
        token = csrf(response.read().decode())
    expect_status(opener, f"/accounts/system/roles/{ids['admin_role']}/delete/", {"csrf_token": token}, 403)
    with get(opener, "/accounts/system/") as response:
        token = csrf(response.read().decode())
    expect_status(opener, f"/accounts/system/users/{ids['admin']}/roles/", {"csrf_token": token}, 403)
    renamed_role = f"{role_name} renamed"
    with get(opener, "/accounts/system/") as response:
        token = csrf(response.read().decode())
    with post(opener, f"/accounts/system/roles/{ids['role']}/save/", {"csrf_token": token, "name": renamed_role}):
        pass
    with db() as conn:
        assert conn.execute("SELECT 1 FROM access_role WHERE id=%s AND name=%s", (ids["role"], renamed_role)).fetchone()
    with get(opener, "/accounts/system/") as response:
        token = csrf(response.read().decode())
    with post(opener, f"/accounts/system/roles/{ids['role']}/permissions/", {"csrf_token": token, "permission_codes": ["workitems.create", "accounts.manage_users"]}):
        pass
    with db() as conn:
        assert conn.execute("SELECT 1 FROM access_rolepermission WHERE role_id=%s AND permission_id='workitems.create'", (ids["role"],)).fetchone()
        assert conn.execute("SELECT 1 FROM access_rolepermission WHERE role_id=%s AND permission_id='accounts.manage_users'", (ids["role"],)).fetchone() is None
    with get(opener, "/accounts/system/") as response:
        token = csrf(response.read().decode())
    with post(opener, "/accounts/system/groups/create/", {"csrf_token": token, "name": group_name}):
        pass
    with db() as conn:
        ids["group"] = conn.execute("SELECT id FROM access_group WHERE name=%s", (group_name,)).fetchone()["id"]
    with get(opener, "/accounts/system/") as response:
        token = csrf(response.read().decode())
    expect_status(opener, "/accounts/system/groups/create/", {"csrf_token": token, "name": group_name.upper()}, 409)
    renamed_group = f"{group_name} renamed"
    with get(opener, "/accounts/system/") as response:
        token = csrf(response.read().decode())
    with post(opener, f"/accounts/system/groups/{ids['group']}/save/", {"csrf_token": token, "name": renamed_group}):
        pass
    with db() as conn:
        assert conn.execute("SELECT 1 FROM access_group WHERE id=%s AND name=%s", (ids["group"], renamed_group)).fetchone()
    with get(opener, "/accounts/system/") as response:
        token = csrf(response.read().decode())
    with post(opener, f"/accounts/system/groups/{ids['group']}/members/add/", {"csrf_token": token, "user_id": ids["first"]}):
        pass
    with get(opener, "/accounts/system/") as response:
        token = csrf(response.read().decode())
    with post(opener, f"/accounts/system/users/{ids['first']}/roles/", {"csrf_token": token, "role_ids": str(ids["role"])}):
        pass
    with get(opener, "/accounts/system/") as response:
        token = csrf(response.read().decode())
    expect_status(opener, f"/accounts/system/roles/{ids['role']}/delete/", {"csrf_token": token}, 409)
    with get(opener, "/accounts/system/") as response:
        token = csrf(response.read().decode())
    expect_status(opener, f"/accounts/system/groups/{ids['group']}/delete/", {"csrf_token": token}, 409)

    due = (datetime.now(timezone.utc) + timedelta(days=2)).astimezone().strftime("%Y-%m-%d")
    with get(opener, "/workitems/new/") as response:
        token = csrf(response.read().decode())
    fields = {"csrf_token": token, "title": f"System audience {suffix}", "description": "fixed audience", "kind": "task", "due_date": due, "due_time": "12:00", "event_date": "", "event_time": "", "group_ids": str(ids["group"]), "role_ids": str(ids["role"])}
    with post(opener, "/workitems/new/", fields) as response:
        item_id = response.geturl().rstrip("/").split("/")[-1]
    items.append(item_id)
    with db() as conn:
        assert [row["user_id"] for row in conn.execute("SELECT user_id FROM workitems_assignment WHERE work_item_id=%s", (item_id,))] == [ids["first"]]

    with get(opener, "/accounts/system/") as response:
        token = csrf(response.read().decode())
    with post(opener, f"/accounts/system/groups/{ids['group']}/members/{ids['first']}/delete/", {"csrf_token": token}):
        pass
    with get(opener, "/accounts/system/") as response:
        token = csrf(response.read().decode())
    with post(opener, f"/accounts/system/groups/{ids['group']}/members/add/", {"csrf_token": token, "user_id": ids["second"]}):
        pass
    with get(opener, "/workitems/new/") as response:
        fields["csrf_token"] = csrf(response.read().decode())
    fields["title"] = f"System audience second {suffix}"
    fields.pop("role_ids")
    with post(opener, "/workitems/new/", fields) as response:
        second_item_id = response.geturl().rstrip("/").split("/")[-1]
    items.append(second_item_id)
    with db() as conn:
        assert [row["user_id"] for row in conn.execute("SELECT user_id FROM workitems_assignment WHERE work_item_id=%s", (item_id,))] == [ids["first"]]
        assert [row["user_id"] for row in conn.execute("SELECT user_id FROM workitems_assignment WHERE work_item_id=%s", (second_item_id,))] == [ids["second"]]
        authored = uuid4()
        conn.execute("INSERT INTO workitems_workitem(id,kind,title,description,author_id,created_at,due_at,version,progress_epoch) VALUES (%s,'task','Authored by deleted user','',%s,now(),now()+interval '2 days',1,1)", (authored, ids["first"]))
        conn.execute("INSERT INTO workitems_assignment(id,work_item_id,user_id,state,first_assigned_at,assigned_at,revoked_reason,version,assigned_by_id) VALUES (%s,%s,%s,'active',now(),now(),'',1,%s)", (uuid4(), authored, ids["second"], ids["first"]))
        conn.commit()
    items.append(str(authored))

    with get(opener, "/accounts/system/") as response:
        token = csrf(response.read().decode())
    with post(opener, f"/accounts/system/users/{ids['first']}/delete/", {"csrf_token": token}):
        pass
    with db() as conn:
        assert conn.execute("SELECT 1 FROM accounts_user WHERE id=%s", (ids["first"],)).fetchone() is None
        assert conn.execute("SELECT 1 FROM workitems_assignment WHERE user_id=%s", (ids["first"],)).fetchone() is None
        assert conn.execute("SELECT 1 FROM workitems_workitem WHERE id=%s", (authored,)).fetchone() is None
        assert conn.execute("SELECT 1 FROM workitems_workitem WHERE id=%s", (item_id,)).fetchone() is not None
        assert conn.execute("SELECT 1 FROM workitems_assignment WHERE work_item_id=%s", (item_id,)).fetchone() is None
    with get(opener, "/accounts/system/") as response:
        token = csrf(response.read().decode())
    with post(opener, f"/accounts/system/groups/{ids['group']}/members/{ids['second']}/delete/", {"csrf_token": token}):
        pass
    with get(opener, "/accounts/system/") as response:
        token = csrf(response.read().decode())
    with post(opener, f"/accounts/system/groups/{ids['group']}/delete/", {"csrf_token": token}):
        pass
    with get(opener, "/accounts/system/") as response:
        token = csrf(response.read().decode())
    with post(opener, f"/accounts/system/roles/{ids['role']}/delete/", {"csrf_token": token}):
        pass
    ids["group"] = ids["role"] = None
    print("System workflow: admin UI, role/group audience, deduplication, frozen membership and deletion semantics — OK")
finally:
    with db() as conn:
        with conn.transaction():
            if items:
                conn.execute("DELETE FROM discussions_eventmessage WHERE work_item_id=ANY(%s)", (items,))
                conn.execute("DELETE FROM workitems_subtaskprogress WHERE work_item_id=ANY(%s)", (items,))
                conn.execute("DELETE FROM workitems_subtask WHERE work_item_id=ANY(%s)", (items,))
                conn.execute("DELETE FROM workitems_assignment WHERE work_item_id=ANY(%s)", (items,))
                conn.execute("DELETE FROM workitems_workitem WHERE id=ANY(%s)", (items,))
            if ids["group"]:
                conn.execute("DELETE FROM access_groupmember WHERE group_id=%s", (ids["group"],))
                conn.execute("DELETE FROM access_group WHERE id=%s", (ids["group"],))
            if ids["role"]:
                conn.execute("DELETE FROM access_userrole WHERE role_id=%s", (ids["role"],))
                conn.execute("DELETE FROM access_rolepermission WHERE role_id=%s", (ids["role"],))
                conn.execute("DELETE FROM access_role WHERE id=%s", (ids["role"],))
            for key in ("first", "second", "admin"):
                if ids[key]:
                    conn.execute("DELETE FROM access_userrole WHERE user_id=%s", (ids[key],))
                    conn.execute("DELETE FROM access_groupmember WHERE user_id=%s", (ids[key],))
                    conn.execute("DELETE FROM app_sessions WHERE user_id=%s", (ids[key],))
                    conn.execute("DELETE FROM accounts_user WHERE id=%s", (ids[key],))
