"""Реальный HTTP/SMTP сценарий этапа 3 на отдельной пустой development БД.

Не создает fixtures в обычном проекте, не печатает секретные auth-ссылки.
"""
import argparse
import json
from pathlib import Path
import re
import secrets
import subprocess
import urllib.parse
import urllib.request

from http_helpers import Browser, Inputs

parser = argparse.ArgumentParser()
parser.add_argument('--project', required=True)
parser.add_argument('--web-port', required=True, type=int)
parser.add_argument('--mailpit-port', required=True, type=int)
args = parser.parse_args()
assert args.project.startswith('kp11-stage3-smoke-'), 'Нужен отдельный пустой project kp11-stage3-smoke-*.'
assert args.web_port != 8000 and args.mailpit_port != 8025
root = Path(__file__).resolve().parents[1]
compose = ['docker', 'compose', '-p', args.project]
web = f'http://127.0.0.1:{args.web_port}'
mailpit = f'http://127.0.0.1:{args.mailpit_port}'

def run(command):
    return subprocess.run(compose + command, cwd=root, capture_output=True, text=True, check=True)

run(['exec', '-T', 'web', 'python', 'manage.py', 'shell', '-c', 'from apps.accounts.models import User; from apps.workitems.models import WorkItem; assert User.objects.count() == 0 and WorkItem.objects.count() == 0'])
password = 'Smoke-only-' + secrets.token_urlsafe(24)
tokens = []
admin_email = 'stage3.admin@corp.kp11.ru'
manager_email = 'stage3.manager@corp.kp11.ru'
recipient_email = 'stage3.recipient@corp.kp11.ru'
observer_email = 'stage3.observer@corp.kp11.ru'


def activate(email):
    with urllib.request.urlopen(mailpit + '/api/v1/messages', timeout=5) as response:
        messages = json.load(response)['messages']
    message = next(row for row in messages if any(to['Address'] == email for to in row['To']))
    with urllib.request.urlopen(mailpit + '/api/v1/message/' + message['ID'], timeout=5) as response:
        text = json.load(response)['Text']
    link = re.search(r'https?://\S+', text).group(0)
    token = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)['token'][0]
    tokens.append(token)
    browser = Browser(web)
    status, body, _, _ = browser.post_form('/accounts/activate/?' + urllib.parse.urlencode({'token': token}), {'password': password, 'password_confirm': password})
    assert status == 200 and 'Пароль сохранен' in body


def login(email):
    browser = Browser(web)
    status, body, url, _ = browser.post_form('/accounts/login/', {'email': email, 'password': password})
    assert status == 200 and url == web + '/'
    return browser


def checkbox(body, field, label):
    match = re.search(r'<input[^>]+name="' + re.escape(field) + r'"[^>]+value="([0-9a-f-]+)"[^>]*>\s*' + re.escape(label), body)
    assert match, f'Не найден выбор {field}: {label}'
    return match.group(1)


def confirm(browser, body):
    values = Inputs(body).values
    return browser.request('/workitems/confirm/', {key: values[key] for key in ('csrfmiddlewaretoken', 'preview', 'request_key')})

run(['exec', '-T', 'web', 'python', 'manage.py', 'bootstrap_admin', '--email', admin_email, '--full-name', 'Тестовый администратор выдачи'])
activate(admin_email)
admin = login(admin_email)
body = admin.request('/administration/invite/')[1]
manager_role = checkbox(body, 'roles', 'Руководитель')
recipient_roles = [checkbox(body, 'roles', label) for label in ['Преподаватель', 'Куратор']]
groups = [checkbox(body, 'groups', label) for label in ['Кураторы 1-го курса', 'Кураторы 2-го курса']]
for email, name, roles, memberships in [(manager_email, 'Тестовый руководитель', [manager_role], []), (recipient_email, 'Тестовый получатель', recipient_roles, groups), (observer_email, 'Другой руководитель', [manager_role], [])]:
    status, body, _, _ = admin.post_form('/administration/invite/', {'email': email, 'full_name': name, 'roles': roles, 'groups': memberships})
    assert status == 200 and 'Письмо передано SMTP' in body
    activate(email)
manager = login(manager_email)
recipient = login(recipient_email)
observer = login(observer_email)
body = manager.request('/workitems/new/')[1]
recipient_id = checkbox(body, 'users', 'Тестовый получатель')
assert recipient_email not in body and admin_email not in body
status, first_preview, _, _ = manager.post_form('/workitems/new/', {'kind': 'task', 'title': 'Задача с пересечениями', 'description': 'Безопасный текст <script>evil()</script>', 'due_date': '2026-10-12', 'subtasks': 'Первая\nВторая', 'users': [recipient_id], 'roles': recipient_roles, 'groups': groups})
assert status == 200 and 'Получатели: 1' in first_preview and '12.10.2026 23:59:59' in first_preview
status, body, first_url, _ = confirm(manager, first_preview)
assert status == 200 and 'Все исполнители' in body and '&lt;script&gt;' in body
first_path = urllib.parse.urlsplit(first_url).path
assert recipient.request(first_path)[0] == 200
assert 'Все исполнители' not in recipient.request(first_path)[1]
assert recipient.request(first_path + 'assignees/')[0] == 403
assert observer.request(first_path)[0] == 404
assert observer.request('/workitems/issued/')[0] == 200
assert recipient.request('/workitems/new/')[0] == 403
assert recipient.request('/workitems/all/')[0] == 403
assert manager.request('/workitems/all/')[0] == 403
assert admin.request('/workitems/all/')[0] == 200
assert confirm(manager, first_preview)[2] == first_url
run(['exec', '-T', 'web', 'python', 'manage.py', 'shell', '-c', 'from apps.workitems.models import WorkItem, Assignment, AssignmentSource, MutationReceipt; assert WorkItem.objects.count() == Assignment.objects.count() == MutationReceipt.objects.count() == 1; assert AssignmentSource.objects.count() == 5'])
# Изменение состава между превью и подтверждением: новый список и 409.
status, before_change, _, _ = manager.post_form('/workitems/new/', {'kind': 'task', 'title': 'Выдача после изменения группы', 'due_date': '2026-10-13', 'groups': [groups[0]]})
assert status == 200 and 'Получатели: 1' in before_change
body = admin.request('/administration/')[1]
paths = {email: re.search(r'<a href="(/administration/employees/[0-9a-f-]+/)">[^<]+</a> · ' + re.escape(email), body).group(1) for email in [recipient_email, observer_email, manager_email]}
assert admin.post_form(paths[observer_email], {'full_name': 'Другой руководитель', 'is_active': 'on', 'roles': [manager_role], 'groups': [groups[0]]})[0] == 200
status, after_change, _, _ = confirm(manager, before_change)
assert status == 409 and 'Получатели: 2' in after_change and 'еще раз' in after_change
assert confirm(manager, after_change)[0] == 200
# Старое назначение не пересчитывается после удаления membership.
assert admin.post_form(paths[recipient_email], {'full_name': 'Тестовый получатель', 'is_active': 'on', 'roles': recipient_roles})[0] == 200
assert recipient.request(first_path)[0] == 200
assert confirm(manager, first_preview)[2] == first_url
assert 'Активных исполнителей: 1' in manager.request(first_path + 'assignees/')[1]
# Событие: event datetime и срок независимы.
status, event_preview, _, _ = manager.post_form('/workitems/new/', {'kind': 'event', 'title': 'Событие с отдельным сроком', 'due_date': '2026-10-14', 'event_at': '2026-10-20T09:30', 'users': [recipient_id]})
assert status == 200 and '20.10.2026 09:30' in event_preview
status, body, event_url, _ = confirm(manager, event_preview)
assert status == 200 and '14.10.2026 23:59:59' in body and '20.10.2026 09:30' in body
# Нельзя подменить author/kind в подтверждении: сервер берет подписанное содержание.
values = Inputs(event_preview).values
values.update({'author_id': recipient_id, 'kind': 'task'})
assert manager.request('/workitems/confirm/', values)[2] == event_url
assert recipient.request(first_path + 'chat/', {'csrfmiddlewaretoken': Inputs(recipient.request('/')[1]).values['csrfmiddlewaretoken'], 'text': 'Чат задачи'})[0] == 404
# Повтор после снятия роли автора запрещен даже при прежнем receipt.
assert admin.post_form(paths[manager_email], {'full_name': 'Тестовый руководитель', 'is_active': 'on'})[0] == 200
assert confirm(manager, first_preview)[0] == 403
assert manager.request(first_path)[0] == 404
run(['exec', '-T', 'web', 'python', 'manage.py', 'shell', '-c', 'from apps.workitems.models import WorkItem, Assignment, MutationReceipt; from apps.audit.models import AuditEntry; assert WorkItem.objects.count() == MutationReceipt.objects.count() == AuditEntry.objects.filter(action="workitems.issued").count() == 3; assert Assignment.objects.count() == 4; event=WorkItem.objects.get(kind="event"); assert event.event_at != event.due_at'])
logs_result = run(['logs', '--no-color', 'web'])
logs = logs_result.stdout + logs_result.stderr
for secret in [password, '?token=', *tokens]:
    assert secret not in logs, 'Auth-секрет обнаружен в журнале web.'
print('Этап 3 HTTP/SMTP: 4 приглашения/активации, OR/dedup/5 причин, превью и 409, повтор, snapshot, own/all/roster права, отдельные даты события, отзыв права автора, CSRF-формы и безопасные логи — OK')
