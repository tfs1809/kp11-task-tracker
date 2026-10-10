"""Приемка этапа 4 через реальный HTTP/SMTP, только отдельная пустая БД."""
import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import secrets
import subprocess
import urllib.parse
import urllib.request

from http_helpers import Browser, Inputs


class Forms(HTMLParser):
    def __init__(self, body):
        super().__init__()
        self.forms = []
        self.current = None
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            self.current = {'action': attrs.get('action', ''), 'data': {}}
            self.forms.append(self.current)
        if tag == 'input' and self.current is not None and attrs.get('name'):
            if attrs.get('type') in ('checkbox', 'radio') and 'checked' not in attrs:
                return
            self.current['data'][attrs['name']] = attrs.get('value', '')

    def handle_endtag(self, tag):
        if tag == 'form':
            self.current = None


def form(body, action_contains):
    return next(row for row in Forms(body).forms if action_contains in row['action'])


parser = argparse.ArgumentParser()
parser.add_argument('--project', required=True)
parser.add_argument('--web-port', type=int, required=True)
parser.add_argument('--mailpit-port', type=int, required=True)
args = parser.parse_args()
assert args.project.startswith('kp11-stage4-smoke-') and args.web_port != 8000 and args.mailpit_port != 8025
root = Path(__file__).resolve().parents[1]
compose = ['docker', 'compose', '-p', args.project]
web = f'http://127.0.0.1:{args.web_port}'
mailpit = f'http://127.0.0.1:{args.mailpit_port}'

def run(command):
    return subprocess.run(compose + command, cwd=root, capture_output=True, text=True, check=True)

run(['exec', '-T', 'web', 'python', 'manage.py', 'shell', '-c', 'from apps.accounts.models import User; assert User.objects.count() == 0'])
password = 'Smoke-only-' + secrets.token_urlsafe(24)
tokens = []
admin_email, manager_email, first_email, second_email = [f'stage4.{name}@corp.kp11.ru' for name in ['admin', 'manager', 'first', 'second']]

def activate(email):
    with urllib.request.urlopen(mailpit + '/api/v1/messages', timeout=5) as response:
        messages = json.load(response)['messages']
    message = next(row for row in messages if any(to['Address'] == email for to in row['To']))
    with urllib.request.urlopen(mailpit + '/api/v1/message/' + message['ID'], timeout=5) as response:
        text = json.load(response)['Text']
    token = urllib.parse.parse_qs(urllib.parse.urlsplit(re.search(r'https?://\S+', text).group(0)).query)['token'][0]
    tokens.append(token)
    status, body, _, _ = Browser(web).post_form('/accounts/activate/?' + urllib.parse.urlencode({'token': token}), {'password': password, 'password_confirm': password})
    assert status == 200 and 'Пароль сохранен' in body


def login(email):
    browser = Browser(web)
    assert browser.post_form('/accounts/login/', {'email': email, 'password': password})[0] == 200
    return browser


def choice(body, field, label):
    match = re.search(r'<input[^>]+name="' + field + r'"[^>]+value="([0-9a-f-]+)"[^>]*>\s*' + re.escape(label), body)
    assert match
    return match.group(1)


def submit(browser, body, needle, **overrides):
    row = form(body, needle)
    return browser.request(row['action'], row['data'] | overrides)


def finish(browser, path):
    for _ in range(100):
        body = browser.request(path)[1]
        remaining = [row for row in Forms(body).forms if '/subtasks/' in row['action'] and row['data']['value'] == 'true']
        if not remaining:
            break
        assert browser.request(remaining[0]['action'], remaining[0]['data'])[0] == 200
    else:
        raise AssertionError('Checklist did not converge')
    assert submit(browser, browser.request(path)[1], '/completion/')[0] == 200

run(['exec', '-T', 'web', 'python', 'manage.py', 'bootstrap_admin', '--email', admin_email, '--full-name', 'Администратор проверки прогресса'])
activate(admin_email)
admin = login(admin_email)
manager_role = choice(admin.request('/administration/invite/')[1], 'roles', 'Руководитель')
for email, name, roles in [(manager_email, 'Руководитель проверки прогресса', [manager_role]), (first_email, 'Первый исполнитель', []), (second_email, 'Второй исполнитель', [])]:
    assert admin.post_form('/administration/invite/', {'email': email, 'full_name': name, 'roles': roles})[0] == 200
    activate(email)
manager, first, second = [login(email) for email in [manager_email, first_email, second_email]]
body = manager.request('/workitems/new/')[1]
first_id = choice(body, 'users', 'Первый исполнитель')
second_id = choice(body, 'users', 'Второй исполнитель')
status, preview, _, _ = manager.post_form('/workitems/new/', {'kind': 'task', 'title': 'Рабочий цикл этапа 4', 'due_date': '2026-10-12', 'users': [first_id, second_id], 'subtasks': 'Первый пункт\nВторой пункт'})
assert status == 200
status, body, url, _ = submit(manager, preview, '/workitems/confirm/')
assert status == 200
path = urllib.parse.urlsplit(url).path
# Сервер запрещает завершение при неполном checklist.
assert submit(first, first.request(path)[1], '/completion/')[0] == 400
body = first.request(path)[1]
assert submit(first, body, '/subtasks/')[0] == 200
assert 'Первый пункт — не отмечено' in second.request(path)[1]
finish(first, path)
assert '50%' in manager.request(path)[1]
assert 'Выполнено:' in first.request(path)[1]
assert '/subtasks/' not in first.request(path)[1]
# Возврат сохраняет чекбоксы и историю, повторное выполнение.
assert submit(first, first.request(path)[1], '/completion/')[0] == 200
assert 'Первый пункт — отмечено' in first.request(path)[1]
finish(first, path)
# Перестановка не сбрасывает, новая подзадача потребует подтверждения последствий.
def edit_form():
    body = manager.request(path + 'edit/')[1]
    return form(body, '')['data'] if Forms(body).forms[0]['action'] == '' else next(row['data'] for row in Forms(body).forms if row['action'] == '')

data = edit_form()
data.update({'title': 'Рабочий цикл этапа 4', 'description': 'Редактирование по HTTP', 'due_date': '2026-10-12', 'subtasks-0-position': '2', 'subtasks-1-position': '1'})
status, preview, _, _ = manager.request(path + 'edit/', data)
assert status == 200 and 'Выполнение и оставшиеся отметки сохранятся' in preview
assert submit(manager, preview, '/confirm-change/')[0] == 200
assert 'Выполнено:' in first.request(path)[1]
data = edit_form()
data.update({'title': 'Рабочий цикл этапа 4', 'description': 'Редактирование по HTTP', 'due_date': '2026-10-12', 'subtasks-2-text': 'Новый пункт', 'subtasks-2-position': '3'})
status, old_preview, _, _ = manager.request(path + 'edit/', data)
assert status == 200 and 'из них выполнили: 1' in old_preview
finish(second, path)
status, new_preview, _, _ = submit(manager, old_preview, '/confirm-change/')
assert status == 409 and 'из них выполнили: 2' in new_preview
assert submit(manager, new_preview, '/confirm-change/')[0] == 200
assert 'Выполнено:' not in first.request(path)[1]
assert 'Новый пункт — не отмечено' in first.request(path)[1]
assert 'Первый пункт — отмечено' in first.request(path)[1]
finish(first, path)
# Отзыв: доступ 404; восстановление compatible progress явно показано.
roster = manager.request(path + 'assignees/')[1]
first_assignment_id = re.search(r'/assignees/([0-9a-f-]+)/">Первый исполнитель', roster).group(1)
revoke_form = next(row for row in Forms(roster).forms if '/revoke/' in row['action'] and first_assignment_id in row['action'])
assert manager.request(revoke_form['action'], revoke_form['data'] | {'reason': 'Проверка отзыва'})[0] == 200
assert first.request(path)[0] == 404
status, add_preview, _, _ = manager.post_form(path + 'add/', {'users': [first_id]})
assert status == 200 and 'Восстановление прежнего прогресса' in add_preview and 'Прежнее выполнение восстановится' in add_preview
assert submit(manager, add_preview, '/confirm-change/')[0] == 200
assert 'Выполнено:' in first.request(path)[1]
# Две формы: устаревшая не перезаписывает и сохраняет ввод.
old_data, fresh_data = edit_form(), edit_form()
fresh_data.update({'title': 'Актуальное название', 'description': '', 'due_date': '2026-10-12'})
status, fresh_preview, _, _ = manager.request(path + 'edit/', fresh_data)
assert status == 200 and submit(manager, fresh_preview, '/confirm-change/')[0] == 200
old_data.update({'title': 'Сохраненный устаревший ввод', 'description': '', 'due_date': '2026-10-12'})
status, conflict, _, _ = manager.request(path + 'edit/', old_data)
assert status == 409 and 'Сохраненный устаревший ввод' in conflict
assert 'Актуальное название' in manager.request(path)[1]
# Деактивация и реактивация не восстанавливают назначение.
body = admin.request('/administration/')[1]
second_path = re.search(r'<a href="(/administration/employees/[0-9a-f-]+/)">[^<]+</a> · ' + re.escape(second_email), body).group(1)
assert admin.post_form(second_path, {'full_name': 'Второй исполнитель'})[0] == 200
assert second.request(path)[2].startswith(web + '/accounts/login/')
assert admin.post_form(second_path, {'full_name': 'Второй исполнитель', 'is_active': 'on'})[0] == 200
second = login(second_email)
assert second.request(path)[0] == 404
assert 'Отозвано' in manager.request(path + 'assignees/?history=1')[1]
# Архив читается действующим участником и не содержит действий записи.
assert submit(manager, manager.request(path)[1], '/archive/')[0] == 200
assert 'Архив — только чтение' in first.request(path)[1]
assert '/completion/' not in first.request(path)[1]
assert 'Актуальное название' in first.request('/workitems/archive/')[1]
assert second.request(path)[0] == 404
assert manager.request(path + 'edit/')[0] == 403
assert 'Назначение выполнено' in first.request(path + 'history/')[1]
assert second_email not in first.request(path + 'history/')[1]
run(['exec', '-T', 'web', 'python', 'manage.py', 'shell', '-c', 'from apps.workitems.models import WorkItem,Assignment,SubtaskProgress; from apps.audit.models import AuditEntry; item=WorkItem.objects.get(); assert item.archived_at; assert Assignment.objects.filter(state="active").count()==1; assert Assignment.objects.filter(state="revoked").count()==1; assert SubtaskProgress.objects.count()==6; assert AuditEntry.objects.filter(action="workitems.archived").count()==1'])
logs = run(['logs', '--no-color', 'web'])
for secret in [password, '?token=', *tokens]:
    assert secret not in logs.stdout + logs.stderr
print('Этап 4 HTTP/SMTP: независимые отметки, completion/return, reorder/reset/reconfirm, отзыв/restore, версии, деактивация/реактивация, статистика/история и readonly archive — OK')
