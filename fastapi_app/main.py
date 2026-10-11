"""FastAPI application for the task tracker."""

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .db import connection, ensure_runtime_schema, many, one, transaction
from .auth import INVALID_LINK, change_password, find_reset, issue_recovery, normalize_email, reset_password
from .auth import activate, find_invitation, issue_invitation
from .access import has_permission, require_permission
from .security import SESSION_COOKIE, create_session, csrf_token, csrf_valid, current_user, delete_session
from .templates import render


app = FastAPI(title="Трек задач КП11", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parents[1] / "static"), name="static")


@app.on_event("startup")
def startup():
    # Runtime role is intentionally not allowed to create schema objects.
    # The migrate service creates app_sessions before web starts.
    return None


def _csrf(request: Request):
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        return csrf_token(raw)
    return hmac.new(os.environ["APP_SECRET_KEY"].encode(), request.headers.get("user-agent", "").encode(), hashlib.sha256).hexdigest()


def _user(request):
    return current_user(request.cookies.get(SESSION_COOKIE))


def _page(template, request, **values):
    user = _user(request)
    is_admin = False
    if user:
        with connection() as conn:
            is_admin = has_permission(conn, user["id"], "accounts.manage_users")
    return HTMLResponse(render(template, user=user, csrf=_csrf(request), is_admin=is_admin, **values))


def _require_user(request):
    user = _user(request)
    if user:
        return user
    return None


def _anonymous_csrf_valid(request, supplied):
    return bool(supplied) and hmac.compare_digest(str(supplied), _csrf(request))


def _can_read_item(conn, item_id, user_id):
    return one(conn, "SELECT 1 FROM workitems_workitem w WHERE w.id=%s AND (w.author_id=%s OR EXISTS (SELECT 1 FROM workitems_assignment a WHERE a.work_item_id=w.id AND a.user_id=%s AND a.state='active'))", (item_id, user_id, user_id)) is not None


def _redirect_login(request):
    return RedirectResponse(f"/accounts/login/?next={request.url.path}", status_code=303)


MOSCOW = ZoneInfo("Europe/Moscow")


def _date(value, with_seconds=False):
    if not value:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(MOSCOW)
    return value.strftime("%d.%m.%Y %H:%M:%S" if with_seconds else "%d.%m.%Y %H:%M")


def _local_datetime(value):
    """Parse a browser datetime-local value as Moscow time."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MOSCOW)
    return parsed


def _assignments(user_id, completed=False, *, search="", kind="all", sort="asc"):
    with connection() as conn:
        clauses = [
            "a.user_id=%s", "a.state='active'", "w.archived_at IS NULL",
            "((%s AND (a.completed_at IS NOT NULL OR (w.kind='event' AND w.event_at IS NOT NULL AND w.event_at <= now() AND (a.event_returned_at IS NULL OR w.event_at + interval '1 day' <= now())))) OR (NOT %s AND a.completed_at IS NULL AND NOT (w.kind='event' AND w.event_at IS NOT NULL AND w.event_at <= now() AND (a.event_returned_at IS NULL OR w.event_at + interval '1 day' <= now()))))",
        ]
        params = [user_id, completed, completed]
        if search:
            clauses.append("w.title ILIKE %s")
            params.append(f"%{search}%")
        if kind in {"task", "event"}:
            clauses.append("w.kind=%s")
            params.append(kind)
        ordering = "DESC" if sort == "desc" else "ASC"
        rows = many(
            conn,
            f"""
            SELECT a.id assignment_id, a.completed_at, a.version assignment_version,
                   a.completion_epoch, a.event_returned_at,
                   w.id item_id, w.kind, w.title, w.description, w.due_at, w.event_at,
                   u.full_name author_name
            FROM workitems_assignment a
            JOIN workitems_workitem w ON w.id=a.work_item_id
            JOIN accounts_user u ON u.id=w.author_id
            WHERE {' AND '.join(clauses)}
            ORDER BY COALESCE(w.event_at,w.due_at) {ordering}, w.id {ordering}
            """,
            tuple(params),
        )
        for row in rows:
            row["kind_label"] = "Событие" if row["kind"] == "event" else "Задача"
            due_value = row["due_at"]
            event_value = row["event_at"]
            row["due_at"] = _date(due_value)
            row["event_at"] = _date(event_value)
            now = datetime.now(timezone.utc)
            row["completed"] = row["completed_at"] is not None or (row["kind"] == "event" and event_value and event_value <= now and (row["event_returned_at"] is None or event_value + timedelta(days=1) <= now))
            row["overdue"] = bool(due_value and due_value < datetime.now(timezone.utc))
            row["subtasks"] = many(conn, "SELECT id,text FROM workitems_subtask WHERE work_item_id=%s AND deleted_at IS NULL ORDER BY position,id", (row["item_id"],))
            for sub in row["subtasks"]:
                progress = one(conn, "SELECT checked_at FROM workitems_subtaskprogress WHERE assignment_id=%s AND subtask_id=%s", (row["assignment_id"], sub["id"]))
                sub["checked"] = bool(progress and progress["checked_at"])
            row["all_checked"] = all(sub["checked"] for sub in row["subtasks"])
    return rows


@app.middleware("http")
async def headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("Referrer-Policy", "strict-origin")
    response.headers["X-KP11-Backend"] = "fastapi"
    return response


@app.get("/health/live/", include_in_schema=False)
def live(request: Request):
    if not hmac.compare_digest(request.headers.get("X-Healthcheck-Key", ""), os.environ.get("HEALTHCHECK_TOKEN", "")):
        return PlainTextResponse("Not found\n", status_code=404)
    return PlainTextResponse("ok\n")


@app.get("/health/ready/", include_in_schema=False)
def ready(request: Request):
    if not hmac.compare_digest(request.headers.get("X-Healthcheck-Key", ""), os.environ.get("HEALTHCHECK_TOKEN", "")):
        return PlainTextResponse("Not found\n", status_code=404)
    try:
        with connection() as conn:
            ok = one(conn, "SELECT id FROM access_accessguard WHERE id=1") is not None
    except Exception:
        ok = False
    return PlainTextResponse("ok\n" if ok else "unavailable\n", status_code=200 if ok else 503)


@app.get("/accounts/login/", response_class=HTMLResponse)
async def login_page(request: Request):
    if _user(request):
        return RedirectResponse("/", status_code=303)
    return _page("login.html", request, title="Вход")


@app.post("/accounts/login/")
async def login(request: Request):
    form = await request.form()
    supplied = str(form.get("csrf_token", ""))
    raw = request.cookies.get(SESSION_COOKIE)
    expected = csrf_token(raw) if raw else _csrf(request)
    if not hmac.compare_digest(supplied, expected):
        return _page("login.html", request, title="Вход", error="Проверка безопасности формы не пройдена.", email=form.get("email", ""))
    email = str(form.get("email", "")).strip().lower()
    with connection() as conn:
        user = one(conn, "SELECT * FROM accounts_user WHERE email=%s", (email,))
    from .security import verify_password
    if not user or not verify_password(user.get("password"), str(form.get("password", ""))) or not user["is_active"] or not user["email_verified_at"]:
        return _page("login.html", request, title="Вход", error="Неверная почта или пароль.", email=email)
    token = create_session(user)
    response = RedirectResponse(str(form.get("next") or "/"), status_code=303)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", secure=False, max_age=43200)
    return response


@app.post("/accounts/logout/")
async def logout(request: Request):
    raw = request.cookies.get(SESSION_COOKIE)
    form = await request.form()
    if not csrf_valid(raw, str(form.get("csrf_token", ""))):
        return PlainTextResponse("Forbidden\n", status_code=403)
    delete_session(raw)
    response = RedirectResponse("/accounts/login/", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/accounts/recovery/", response_class=HTMLResponse)
async def recovery_page(request: Request):
    if _user(request):
        return RedirectResponse("/", status_code=303)
    return _page("recovery.html", request, title="Восстановление доступа")


@app.post("/accounts/recovery/")
async def recovery(request: Request):
    form = await request.form()
    if not _anonymous_csrf_valid(request, form.get("csrf_token", "")):
        return PlainTextResponse("Forbidden\n", status_code=403)
    email = str(form.get("email", "")).strip().lower()
    # The response is intentionally identical for unknown and known addresses.
    try:
        issue_recovery(email)
    except (ValueError, KeyError):
        pass
    return _page("recovery.html", request, title="Восстановление доступа", message="Если адрес принадлежит действующему активированному сотруднику, на него отправлено письмо.")


@app.get("/accounts/reset/", response_class=HTMLResponse)
async def reset_page(request: Request):
    token = request.query_params.get("token", "")
    if not find_reset(token):
        return _page("password.html", request, title="Ссылка недействительна", error=INVALID_LINK, action="/accounts/reset/", finished=True)
    return _page("password.html", request, title="Новый пароль", action="/accounts/reset/", token=token)


@app.post("/accounts/reset/")
async def reset(request: Request):
    form = await request.form()
    if not _anonymous_csrf_valid(request, form.get("csrf_token", "")):
        return PlainTextResponse("Forbidden\n", status_code=403)
    token = str(form.get("token", ""))
    password = str(form.get("password", ""))
    if password != str(form.get("password_confirm", "")):
        return _page("password.html", request, title="Новый пароль", action="/accounts/reset/", token=token, error="Пароли не совпадают.")
    ok, error = reset_password(token, password)
    if not ok:
        return _page("password.html", request, title="Новый пароль", action="/accounts/reset/", token=token, error=error)
    return _page("password.html", request, title="Пароль сохранен", action="/accounts/reset/", finished=True, message="Пароль сохранен. Теперь войдите с корпоративной почтой.")


@app.get("/accounts/activate/", response_class=HTMLResponse)
async def activation_page(request: Request):
    token = request.query_params.get("token", "")
    if not find_invitation(token):
        return _page("password.html", request, title="Ссылка недействительна", error=INVALID_LINK, action="/accounts/activate/", finished=True)
    return _page("password.html", request, title="Активация доступа", action="/accounts/activate/", token=token)


@app.post("/accounts/activate/")
async def activation(request: Request):
    form = await request.form()
    if not _anonymous_csrf_valid(request, form.get("csrf_token", "")):
        return PlainTextResponse("Forbidden\n", status_code=403)
    token = str(form.get("token", ""))
    if str(form.get("password", "")) != str(form.get("password_confirm", "")):
        return _page("password.html", request, title="Активация доступа", action="/accounts/activate/", token=token, error="Пароли не совпадают.")
    ok, error = activate(token, str(form.get("password", "")))
    if not ok:
        return _page("password.html", request, title="Активация доступа", action="/accounts/activate/", token=token, error=error)
    return _page("password.html", request, title="Доступ активирован", action="/accounts/activate/", finished=True, message="Доступ активирован. Теперь войдите с корпоративной почтой.")


@app.get("/accounts/administration/", response_class=HTMLResponse)
async def administration(request: Request):
    user = _require_user(request)
    if not user:
        return _redirect_login(request)
    with connection() as conn:
        try:
            require_permission(conn, user["id"], "accounts.manage_users")
        except PermissionError:
            return PlainTextResponse("Forbidden\n", status_code=403)
        users = many(conn, "SELECT id,full_name,email,is_active,email_verified_at,version FROM accounts_user ORDER BY full_name,email")
    return _page("administration.html", request, active="administration", users=users)


@app.get("/accounts/invite/", response_class=HTMLResponse)
async def invite_page(request: Request):
    user = _require_user(request)
    if not user:
        return _redirect_login(request)
    with connection() as conn:
        try:
            require_permission(conn, user["id"], "accounts.manage_users")
        except PermissionError:
            return PlainTextResponse("Forbidden\n", status_code=403)
    return _page("invite.html", request, values={})


@app.post("/accounts/invite/")
async def invite(request: Request):
    user = _require_user(request)
    if not user:
        return _redirect_login(request)
    form = await request.form()
    raw = request.cookies.get(SESSION_COOKIE)
    if not csrf_valid(raw, str(form.get("csrf_token", ""))):
        return PlainTextResponse("Forbidden\n", status_code=403)
    values = {"email": str(form.get("email", "")), "full_name": str(form.get("full_name", ""))}
    try:
        with connection() as conn:
            require_permission(conn, user["id"], "accounts.manage_users")
        sent, _ = issue_invitation(user["id"], values["email"], values["full_name"])
    except (PermissionError, ValueError):
        return _page("invite.html", request, values=values, error="Не удалось создать приглашение. Проверьте данные и права.")
    return _page("invite.html", request, values={}, message="Приглашение сохранено и передано SMTP." if sent else "Приглашение сохранено, но доставка SMTP не подтверждена.")


@app.get("/accounts/users/{user_id}/", response_class=HTMLResponse)
async def employee_page(request: Request, user_id: UUID):
    actor = _require_user(request)
    if not actor:
        return _redirect_login(request)
    with connection() as conn:
        try:
            require_permission(conn, actor["id"], "accounts.manage_users")
        except PermissionError:
            return PlainTextResponse("Forbidden\n", status_code=403)
        employee = one(conn, "SELECT id,full_name,email,is_active,email_verified_at,version FROM accounts_user WHERE id=%s", (user_id,))
    if not employee:
        return PlainTextResponse("Not found\n", status_code=404)
    return _page("employee.html", request, employee=employee)


@app.post("/accounts/users/{user_id}/")
async def employee_save(request: Request, user_id: UUID):
    actor = _require_user(request)
    if not actor:
        return _redirect_login(request)
    form = await request.form()
    raw = request.cookies.get(SESSION_COOKIE)
    if not csrf_valid(raw, str(form.get("csrf_token", ""))):
        return PlainTextResponse("Forbidden\n", status_code=403)
    try:
        with transaction() as conn:
            require_permission(conn, actor["id"], "accounts.manage_users")
            employee = one(conn, "SELECT id,full_name,email,is_active,email_verified_at,version FROM accounts_user WHERE id=%s FOR UPDATE", (user_id,))
            if not employee:
                return PlainTextResponse("Not found\n", status_code=404)
            active = str(form.get("is_active", "")) == "true"
            now = datetime.now(timezone.utc)
            if employee["is_active"] and not active:
                conn.execute("UPDATE accounts_user SET auth_version=auth_version+1,version=version+1,is_active=false,updated_at=%s WHERE id=%s", (now, user_id))
                conn.execute("UPDATE accounts_invitation SET revoked_at=%s WHERE user_id=%s AND used_at IS NULL AND revoked_at IS NULL", (now, user_id))
                conn.execute("UPDATE accounts_passwordresetgrant SET revoked_at=%s WHERE user_id=%s AND used_at IS NULL AND revoked_at IS NULL", (now, user_id))
                conn.execute("DELETE FROM app_sessions WHERE user_id=%s", (user_id,))
            else:
                conn.execute("UPDATE accounts_user SET full_name=%s,is_active=%s,version=version+1,updated_at=%s WHERE id=%s", (str(form.get("full_name", "")).strip(), active, now, user_id))
    except (PermissionError, ValueError):
        return PlainTextResponse("Forbidden\n", status_code=403)
    return RedirectResponse("/accounts/administration/", status_code=303)


@app.get("/accounts/password/", response_class=HTMLResponse)
async def password_page(request: Request):
    if not _require_user(request):
        return _redirect_login(request)
    return _page("password.html", request, title="Изменение пароля", action="/accounts/password/", change=True)


@app.post("/accounts/password/")
async def password_change(request: Request):
    user = _require_user(request)
    if not user:
        return _redirect_login(request)
    form = await request.form()
    raw = request.cookies.get(SESSION_COOKIE)
    if not csrf_valid(raw, str(form.get("csrf_token", ""))):
        return PlainTextResponse("Forbidden\n", status_code=403)
    if str(form.get("password", "")) != str(form.get("password_confirm", "")):
        return _page("password.html", request, title="Изменение пароля", action="/accounts/password/", change=True, error="Пароли не совпадают.")
    ok, error = change_password(user["id"], str(form.get("old_password", "")), str(form.get("password", "")))
    if not ok:
        return _page("password.html", request, title="Изменение пароля", action="/accounts/password/", change=True, error=error)
    # Password rotation invalidates all sessions, including this one.
    response = RedirectResponse("/accounts/login/", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = _require_user(request)
    if not user:
        return _redirect_login(request)
    completed = request.query_params.get("tab") == "completed"
    search = request.query_params.get("q", "").strip()[:120]
    kind = request.query_params.get("kind", "all")
    if kind not in {"all", "task", "event"}:
        kind = "all"
    sort = request.query_params.get("sort", "asc")
    if sort not in {"asc", "desc"}:
        sort = "asc"
    rows = _assignments(user["id"], completed, search=search, kind=kind, sort=sort)
    return _page(
        "home.html", request, active="home", tab="completed" if completed else "assigned",
        assignments=rows, search=search, kind=kind, sort=sort,
        counts={"active": len(_assignments(user["id"], False)), "completed": len(_assignments(user["id"], True))},
    )


@app.get("/accounts/profile/", response_class=HTMLResponse)
async def profile(request: Request):
    if not _require_user(request):
        return _redirect_login(request)
    return _page("profile.html", request, active="profile")


@app.get("/workitems/{item_id}/", response_class=HTMLResponse)
async def detail(request: Request, item_id: UUID):
    user = _require_user(request)
    if not user:
        return _redirect_login(request)
    with connection() as conn:
        item = one(conn, "SELECT w.*,u.full_name author_name FROM workitems_workitem w JOIN accounts_user u ON u.id=w.author_id WHERE w.id=%s AND (w.author_id=%s OR EXISTS (SELECT 1 FROM workitems_assignment a WHERE a.work_item_id=w.id AND a.user_id=%s AND a.state='active'))", (item_id, user["id"], user["id"]))
        subtasks = many(conn, "SELECT id,text FROM workitems_subtask WHERE work_item_id=%s AND deleted_at IS NULL ORDER BY position,id", (item_id,)) if item else []
    if not item:
        return PlainTextResponse("Not found\n", status_code=404)
    item["kind_label"] = "Событие" if item["kind"] == "event" else "Задача"
    item["overdue"] = bool(item["due_at"] and item["due_at"] < datetime.now(timezone.utc))
    item["due_at"] = _date(item["due_at"], True)
    item["event_at"] = _date(item["event_at"])
    return _page("detail.html", request, item=item, subtasks=subtasks)


@app.get("/events/{item_id}/chat/", response_class=HTMLResponse)
async def chat(request: Request, item_id: UUID):
    user = _require_user(request)
    if not user:
        return _redirect_login(request)
    with connection() as conn:
        item = one(conn, "SELECT w.*,u.full_name author_name FROM workitems_workitem w JOIN accounts_user u ON u.id=w.author_id WHERE w.id=%s AND w.kind='event' AND (w.author_id=%s OR EXISTS (SELECT 1 FROM workitems_assignment a WHERE a.work_item_id=w.id AND a.user_id=%s AND a.state='active'))", (item_id, user["id"], user["id"]))
        messages = many(conn, "SELECT m.id,m.text,m.created_at,u.full_name author_name FROM discussions_eventmessage m JOIN accounts_user u ON u.id=m.author_id WHERE m.work_item_id=%s ORDER BY m.id", (item_id,)) if item else []
    if not item:
        return PlainTextResponse("Not found\n", status_code=404)
    item["kind_label"] = "Событие"
    can_send = not item["event_at"] or item["event_at"] + timedelta(days=1) >= datetime.now(timezone.utc)
    return _page("chat.html", request, item=item, messages=messages, can_send=can_send)


@app.post("/events/{item_id}/chat/send/")
async def send_message(request: Request, item_id: UUID):
    user = _require_user(request)
    if not user:
        return _redirect_login(request)
    form = await request.form()
    raw = request.cookies.get(SESSION_COOKIE)
    if not csrf_valid(raw, str(form.get("csrf_token", ""))):
        return PlainTextResponse("Forbidden\n", status_code=403)
    text = str(form.get("text", "")).strip()
    if not 1 <= len(text) <= 4000:
        return PlainTextResponse("Некорректное сообщение\n", status_code=400)
    with transaction() as conn:
        if not _can_read_item(conn, item_id, user["id"]):
            return PlainTextResponse("Not found\n", status_code=404)
        event = one(conn, "SELECT kind,event_at FROM workitems_workitem WHERE id=%s", (item_id,))
        if not event or event["kind"] != "event" or (event["event_at"] and event["event_at"] + timedelta(days=1) < datetime.now(timezone.utc)):
            return PlainTextResponse("Чат закрыт\n", status_code=403)
        import uuid
        audit = one(conn, "INSERT INTO audit_auditentry(actor_id,actor_label,action,object_type,object_id,occurred_at,request_id,changes,reason) VALUES (%s,%s,'message.send','event_message',%s,now(),%s,'{}','') RETURNING id", (user["id"], user["full_name"], str(item_id), uuid.uuid4()))
        conn.execute("INSERT INTO discussions_eventmessage(work_item_id,author_id,text,created_at,hidden_reason,last_change_id) VALUES (%s,%s,%s,now(),'',%s)", (item_id, user["id"], text, audit["id"]))
    return RedirectResponse(f"/events/{item_id}/chat/", status_code=303)


@app.get("/workitems/issued/", response_class=HTMLResponse)
async def issued(request: Request):
    user = _require_user(request)
    if not user:
        return _redirect_login(request)
    search = request.query_params.get("q", "").strip()[:120]
    kind = request.query_params.get("kind", "all")
    if kind not in {"all", "task", "event"}:
        kind = "all"
    sort = request.query_params.get("sort", "asc")
    if sort not in {"asc", "desc"}:
        sort = "asc"
    conditions = ["w.author_id=%s", "w.archived_at IS NULL"]
    params = [user["id"]]
    if search:
        conditions.append("w.title ILIKE %s")
        params.append(f"%{search}%")
    if kind in {"task", "event"}:
        conditions.append("w.kind=%s")
        params.append(kind)
    ordering = "DESC" if sort == "desc" else "ASC"
    with connection() as conn:
        items = many(conn, f"""
            SELECT w.id,w.kind,w.title,w.description,w.due_at,w.event_at,
                   COUNT(a.id) FILTER (WHERE a.user_id <> w.author_id) assignees
            FROM workitems_workitem w
            LEFT JOIN workitems_assignment a ON a.work_item_id=w.id AND a.state='active'
            WHERE {' AND '.join(conditions)}
            GROUP BY w.id
            HAVING COUNT(a.id) FILTER (WHERE a.user_id <> w.author_id) > 0
            ORDER BY COALESCE(w.event_at,w.due_at) {ordering}, w.id {ordering}
        """, tuple(params))
    for item in items:
        item["kind_label"] = "Событие" if item["kind"] == "event" else "Задача"
        due_value = item["due_at"]
        item["overdue"] = bool(due_value and due_value < datetime.now(timezone.utc))
        item["due_at"] = _date(item["due_at"])
        item["event_at"] = _date(item["event_at"])
    return _page("issued.html", request, active="issued", items=items, search=search, kind=kind, sort=sort)


@app.get("/workitems/new/", response_class=HTMLResponse)
async def issue_page(request: Request):
    user = _require_user(request)
    if not user:
        return _redirect_login(request)
    with connection() as conn:
        assignees = many(conn, "SELECT id,full_name,email FROM accounts_user WHERE is_active AND email_verified_at IS NOT NULL ORDER BY full_name,email")
    return _page("issue.html", request, values={"kind": "task"}, assignees=assignees, current_user_id=str(user["id"]))


@app.post("/workitems/new/")
async def issue(request: Request):
    user = _require_user(request)
    if not user:
        return _redirect_login(request)
    form = await request.form()
    raw = request.cookies.get(SESSION_COOKIE)
    values = {key: str(form.get(key, "")) for key in ("title", "description", "kind", "due_at", "event_at")}
    values["assignee_ids"] = [str(value) for value in form.getlist("assignee_ids")]
    values["subtasks"] = [str(value).strip() for value in form.getlist("subtasks") if str(value).strip()]
    if not csrf_valid(raw, str(form.get("csrf_token", ""))):
        return PlainTextResponse("Forbidden\n", status_code=403)
    if not values["title"].strip() or values["kind"] not in {"task", "event"}:
        with connection() as conn:
            assignees = many(conn, "SELECT id,full_name,email FROM accounts_user WHERE is_active AND email_verified_at IS NOT NULL ORDER BY full_name,email")
        return _page("issue.html", request, values=values, assignees=assignees, current_user_id=str(user["id"]), error="Заполните обязательные поля.")
    with connection() as conn:
        try:
            require_permission(conn, user["id"], "workitems.create")
        except PermissionError:
            return PlainTextResponse("Forbidden\n", status_code=403)
    try:
        due = _local_datetime(values["due_at"])
        event = _local_datetime(values["event_at"]) if values["event_at"] else None
    except ValueError:
        with connection() as conn:
            assignees = many(conn, "SELECT id,full_name,email FROM accounts_user WHERE is_active AND email_verified_at IS NOT NULL ORDER BY full_name,email")
        return _page("issue.html", request, values=values, assignees=assignees, current_user_id=str(user["id"]), error="Проверьте даты.")
    if values["kind"] == "event" and event is None:
        with connection() as conn:
            assignees = many(conn, "SELECT id,full_name,email FROM accounts_user WHERE is_active AND email_verified_at IS NOT NULL ORDER BY full_name,email")
        return _page("issue.html", request, values=values, assignees=assignees, current_user_id=str(user["id"]), error="Для события укажите дату.")
    assignee_ids = []
    try:
        assignee_ids = list(dict.fromkeys(UUID(value) for value in values["assignee_ids"]))
    except ValueError:
        assignee_ids = []
    if not assignee_ids:
        assignee_ids = [user["id"]]
    import uuid
    item_id = uuid.uuid4()
    with transaction() as conn:
        conn.execute("INSERT INTO workitems_workitem(id,kind,title,description,author_id,created_at,due_at,event_at,version,progress_epoch) VALUES (%s,%s,%s,%s,%s,now(),%s,%s,1,1)", (item_id, values["kind"], values["title"].strip(), values["description"], user["id"], due, event))
        valid_users = {row["id"] for row in many(conn, "SELECT id FROM accounts_user WHERE id = ANY(%s) AND is_active AND email_verified_at IS NOT NULL", (assignee_ids,))}
        valid_users.add(user["id"]) if not valid_users else None
        for assignee_id in valid_users:
            conn.execute("INSERT INTO workitems_assignment(id,work_item_id,user_id,state,first_assigned_at,assigned_at,revoked_reason,version,assigned_by_id) VALUES (%s,%s,%s,'active',now(),now(),'',1,%s)", (uuid.uuid4(), item_id, assignee_id, user["id"]))
        for position, text in enumerate(values["subtasks"], start=1):
            conn.execute("INSERT INTO workitems_subtask(id,text,position,content_revision,created_at,work_item_id) VALUES (%s,%s,%s,1,now(),%s)", (uuid.uuid4(), text[:1000], position, item_id))
    return RedirectResponse(f"/workitems/{item_id}/", status_code=303)


@app.post("/assignments/{assignment_id}/completion/")
async def completion(request: Request, assignment_id: UUID):
    user = _require_user(request)
    if not user:
        return _redirect_login(request)
    form = await request.form()
    raw = request.cookies.get(SESSION_COOKIE)
    if not csrf_valid(raw, str(form.get("csrf_token", ""))):
        return PlainTextResponse("Forbidden\n", status_code=403)
    completed = str(form.get("completed")) == "true"
    with transaction() as conn:
        assignment = one(conn, "SELECT a.id,a.completed_at,w.kind,w.event_at FROM workitems_assignment a JOIN workitems_workitem w ON w.id=a.work_item_id WHERE a.id=%s AND a.user_id=%s AND a.state='active' FOR UPDATE", (assignment_id, user["id"]))
        if not assignment:
            return PlainTextResponse("Not found\n", status_code=404)
        if completed and assignment["kind"] == "task" and one(conn, "SELECT 1 FROM workitems_subtask s WHERE s.work_item_id=(SELECT work_item_id FROM workitems_assignment WHERE id=%s) AND s.deleted_at IS NULL AND NOT EXISTS (SELECT 1 FROM workitems_subtaskprogress p WHERE p.assignment_id=%s AND p.subtask_id=s.id AND p.checked_at IS NOT NULL)", (assignment_id, assignment_id)):
            return PlainTextResponse("Сначала выполните все подзадачи\n", status_code=409)
        if completed and assignment["kind"] == "event":
            # Hiding is per-assignment; the event and its chat remain visible.
            pass
        if not completed and assignment["kind"] == "event" and assignment["event_at"] and assignment["event_at"] + timedelta(days=1) < datetime.now(timezone.utc):
            return PlainTextResponse("Событие уже закрыто\n", status_code=409)
        conn.execute("UPDATE workitems_assignment AS a SET completed_at=CASE WHEN %s THEN now() ELSE NULL END, completion_epoch=CASE WHEN %s THEN 1 ELSE NULL END, event_returned_at=CASE WHEN %s THEN NULL WHEN w.kind='event' THEN now() ELSE NULL END, version=a.version+1 FROM workitems_workitem w WHERE a.id=%s AND w.id=a.work_item_id", (completed, completed, completed, assignment_id))
    return RedirectResponse("/", status_code=303)


@app.post("/assignments/{assignment_id}/subtasks/{subtask_id}/")
async def mark(request: Request, assignment_id: UUID, subtask_id: UUID):
    user = _require_user(request)
    if not user:
        return _redirect_login(request)
    form = await request.form()
    raw = request.cookies.get(SESSION_COOKIE)
    if not csrf_valid(raw, str(form.get("csrf_token", ""))):
        return PlainTextResponse("Forbidden\n", status_code=403)
    checked = str(form.get("value")) == "true"
    with transaction() as conn:
        assignment = one(conn, "SELECT id,work_item_id FROM workitems_assignment WHERE id=%s AND user_id=%s AND state='active'", (assignment_id, user["id"]))
        subtask = one(conn, "SELECT id,work_item_id,content_revision FROM workitems_subtask WHERE id=%s AND deleted_at IS NULL", (subtask_id,))
        if not assignment or not subtask or assignment["work_item_id"] != subtask["work_item_id"]:
            return PlainTextResponse("Not found\n", status_code=404)
        conn.execute(
            """INSERT INTO workitems_subtaskprogress(assignment_id,subtask_id,work_item_id,checked_at,checked_revision,version)
               VALUES (%s,%s,%s,CASE WHEN %s THEN now() ELSE NULL END,CASE WHEN %s THEN %s ELSE NULL END,1)
               ON CONFLICT (assignment_id,subtask_id) DO UPDATE SET checked_at=EXCLUDED.checked_at,checked_revision=EXCLUDED.checked_revision,version=workitems_subtaskprogress.version+1""",
            (assignment_id, subtask_id, assignment["work_item_id"], checked, checked, subtask["content_revision"]),
        )
    return RedirectResponse("/", status_code=303)


# Starlette otherwise lets the UUID converter reject `/workitems/new/` and
# `/workitems/issued/` before their literal routes are considered. Keep the
# literal endpoints ahead of the generic detail route explicitly.
_literal_paths = {"/workitems/new/", "/workitems/issued/"}
_literal_routes = [route for route in app.router.routes if getattr(route, "path", "") in _literal_paths]
app.router.routes = _literal_routes + [route for route in app.router.routes if getattr(route, "path", "") not in _literal_paths]
