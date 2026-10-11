"""HTTP + SMTP приемка этапа 2. Только отдельный пустой Compose project.

Не печатает пароли/ссылки/токены; не сбрасывает БД или почту.
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
parser.add_argument('--web-port', type=int, required=True)
parser.add_argument('--mailpit-port', type=int, required=True)
args = parser.parse_args()
assert args.project.startswith('kp11-stage2-smoke-'), 'Для проверки нужен отдельный project kp11-stage2-smoke-*.'
assert args.web_port != 8000 and args.mailpit_port != 8025, 'Не использовать обычные порты development.'
root = Path(__file__).resolve().parents[1]
compose = ['docker','compose','-p',args.project]
web = f'http://127.0.0.1:{args.web_port}'
mailpit = f'http://127.0.0.1:{args.mailpit_port}'
run = lambda command: subprocess.run(compose + command, cwd=root, capture_output=True, text=True, check=True)
# Проверка до записи: только пустая изолированная БД.
result = run(['exec','-T','web','python','manage.py','shell','-c','from apps.accounts.models import User; assert User.objects.count() == 0; print("empty isolated database OK")'])
assert 'empty isolated database OK' in result.stdout
password = 'Smoke-only-' + secrets.token_urlsafe(24)
new_password = 'Smoke-only-new-' + secrets.token_urlsafe(24)
admin_email = 'stage2.first@kp11.ru'
second_email = 'stage2.second@kp11.ru'
employee_email = 'stage2.employee@kp11.ru'


def mail_token(email):
    with urllib.request.urlopen(mailpit + '/api/v1/messages', timeout=5) as response:
        messages = json.load(response)['messages']
    message = next(m for m in messages if any(to['Address'] == email for to in m['To']))
    with urllib.request.urlopen(mailpit + '/api/v1/message/' + message['ID'],timeout=5) as response:
        body = json.load(response)['Text']
    link = re.search(r'https?://\S+',body).group(0)
    return urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)['token'][0]


def activate(email):
    token = mail_token(email)
    browser = Browser(web)
    status,body,_,headers = browser.post_form('/accounts/activate/?'+urllib.parse.urlencode({'token':token}),{'password':password,'password_confirm':password})
    assert status == 200 and 'Пароль сохранен' in body
    assert headers['Referrer-Policy'] == 'strict-origin'
    assert browser.request('/accounts/activate/?'+urllib.parse.urlencode({'token':token}))[0] == 400
    return token


def login(browser,email,current=password):
    status,body,url,headers = browser.post_form('/accounts/login/',{'email':email,'password':current})
    assert status == 200 and url == web+'/' and 'Задач пока нет' in body
    return body


result = run(['exec','-T','web','python','manage.py','bootstrap_admin','--email',admin_email,'--full-name','Первый тестовый администратор'])
assert 'ссылка находится только в письме' in result.stdout
used_tokens = [activate(admin_email)]
admin = Browser(web);login(admin,admin_email)
status,body,_,_ = admin.request('/administration/invite/')
assert status == 200
# IDs каталога считываются из реальной формы, без специальных API.
role_match = re.search(r'<input[^>]+name="roles"[^>]+value="([0-9a-f-]+)"[^>]*>\s*Администратор',body)
assert role_match, 'Системная роль отсутствует в форме.'
admin_role = role_match.group(1)
for email,name,roles in [(second_email,'Второй тестовый администратор',[admin_role]),(employee_email,'Тестовый сотрудник',[])]:
    status,body,_,_ = admin.post_form('/administration/invite/',{'email':email,'full_name':name,'roles':roles})
    assert status == 200 and 'Письмо передано SMTP' in body
    used_tokens.append(activate(email))
second = Browser(web);login(second,second_email)
regular = Browser(web);login(regular,employee_email)
assert regular.request('/administration/')[0] == 403
csrf=Inputs(regular.request('/')[1]).values['csrfmiddlewaretoken']
assert regular.request('/administration/roles/new/',{'name':'Обход','csrfmiddlewaretoken':csrf})[0] == 403
status,body,_,_ = admin.request('/administration/')
assert status == 200
employee_paths = {email:re.search(r'<a href="(/administration/employees/[0-9a-f-]+/)">[^<]+</a> · '+re.escape(email),body).group(1) for email in [second_email,employee_email]}
# Снять администраторскую роль у второй уже открытой сессии.
status,body,_,_ = admin.post_form(employee_paths[second_email],{'full_name':'Второй тестовый администратор','is_active':'on'})
assert status == 200 and 'Администрирование' in body
assert second.request('/administration/')[0] == 403
assert second.request('/')[0] == 200
# Отключить обычного сотрудника, его следующая сессия сразу отзывается.
status,body,_,_ = admin.post_form(employee_paths[employee_email],{'full_name':'Тестовый сотрудник'})
assert status == 200
assert regular.request('/')[2].startswith(web+'/accounts/login/')
# Восстановление меняет пароль и отзывает обе старые сессии.
old = Browser(web);login(old,admin_email)
anonymous = Browser(web)
status,body,_,_ = anonymous.post_form('/accounts/recovery/',{'email':admin_email})
assert status == 200 and 'Если адрес принадлежит' in body
reset_token = mail_token(admin_email)
status,body,_,_ = anonymous.post_form('/accounts/reset/?'+urllib.parse.urlencode({'token':reset_token}),{'password':new_password,'password_confirm':new_password})
assert status == 200 and 'Пароль сохранен' in body
assert admin.request('/')[2].startswith(web+'/accounts/login/')
assert old.request('/')[2].startswith(web+'/accounts/login/')
login(Browser(web),admin_email,new_password)
log_result = run(['logs','--no-color','web'])
logs = log_result.stdout + log_result.stderr
for secret in [password,new_password,reset_token,'?token=',*used_tokens]:
    assert secret not in logs, 'Секрет обнаружен в журнале web.'
print('Этап 2 HTTP/SMTP: bootstrap, приглашения двух сотрудников, CSRF-формы, одноразовая активация, вход, запрет POST, отзыв роли/сессии, reset, отсутствие секретов в web logs — OK')
