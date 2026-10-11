"""Настоящий Chromium/SMTP; синтетические сотрудники только в пустом отдельном project."""
import argparse
import json
from pathlib import Path
import re
import secrets
import subprocess
import sys
import unittest
import urllib.parse
import urllib.request
import uuid
from playwright.sync_api import sync_playwright, expect

expect.set_options(timeout=25000)

parser = argparse.ArgumentParser()
parser.add_argument('--project', required=True)
parser.add_argument('--web-port', type=int, required=True)
parser.add_argument('--mailpit-port', type=int, required=True)
parser.add_argument('--chromium', default='/usr/bin/chromium')
parser.add_argument('--artifacts', default='test-results/stage6')
args = parser.parse_args()
if not args.project.startswith('kp11-stage6-browser-') or args.web_port == 8000 or args.mailpit_port == 8025:
    parser.error('Нужны отдельный kp11-stage6-browser-* project и отдельные порты.')
root = Path(__file__).resolve().parents[1]
artifacts = root / args.artifacts
artifacts.mkdir(parents=True, exist_ok=True)
web = f'http://127.0.0.1:{args.web_port}'
mailpit = f'http://127.0.0.1:{args.mailpit_port}'
compose = ['docker', 'compose', '-p', args.project]
password = 'Browser-only-' + secrets.token_urlsafe(24)
private = [password]
reports = []
auth_headers = []
run_id = str(uuid.uuid4())
original_stderr = sys.stderr


def run(command):
    return subprocess.run(compose + command, cwd=root, capture_output=True, text=True, check=True)


def letter(email):
    with urllib.request.urlopen(mailpit + '/api/v1/messages', timeout=5) as response:
        messages = json.load(response)['messages']
    row = next(row for row in messages if any(to['Address'] == email for to in row['To']))
    with urllib.request.urlopen(mailpit + '/api/v1/message/' + row['ID'], timeout=5) as response:
        text = json.load(response)['Text']
    url = re.search(r'https?://\S+', text).group(0)
    private.append(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)['token'][0])
    return url


def click(page, name):
    page.get_by_role('button', name=name, exact=True).click()
    page.wait_for_load_state('domcontentloaded')


def label(page, text):
    return page.get_by_label(re.compile(r'^\s*' + re.escape(text) + r':?\s*$'))


def login(page, email):
    page.goto(web + '/accounts/login/')
    label(page, 'Корпоративная почта').fill(email)
    label(page, 'Пароль').fill(password)
    click(page, 'Войти')
    expect(page.get_by_role('heading', name='Мои задачи', exact=True)).to_be_visible()


def activate(browser, email):
    context = browser.new_context()
    try:
        page = context.new_page()
        response = page.goto(letter(email))
        assert response.header_value('referrer-policy') == 'strict-origin'
        label(page, 'Новый пароль').fill(password)
        label(page, 'Повторите пароль').fill(password)
        with page.expect_request(lambda request: request.method == 'POST') as submitted:
            page.locator('main button[type=submit]').click()
        headers = submitted.value.all_headers()
        assert headers.get('origin') == web
        assert headers.get('referer') == web + '/'
        auth_headers.append({'origin_matches': True, 'referer_has_no_path_query': True})
        expect(page.get_by_role('heading', name='Пароль сохранен', exact=True)).to_be_visible()
    finally:
        context.close()


AUDIT = r'''() => {
  const failures = [];
  const visible = el => { const r = el.getBoundingClientRect(); return !!el.getClientRects().length && r.bottom >= 0 && getComputedStyle(el).visibility !== 'hidden'; };
  if (document.documentElement.scrollWidth > innerWidth) failures.push(`overflow ${document.documentElement.scrollWidth}/${innerWidth}`);
  const ids = [...document.querySelectorAll('[id]')].map(el => el.id);
  if (new Set(ids).size !== ids.length) failures.push('duplicate ids');
  let controls = 0;
  for (const el of document.querySelectorAll('input:not([type=hidden]),select,textarea,button')) {
    if (!visible(el) || el.disabled) continue;
    controls++;
    const labelled = (el.labels && el.labels.length) || el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || (el.tagName === 'BUTTON' && el.textContent.trim());
    if (!labelled) failures.push(`missing label ${el.id || el.tagName}`);
    if (el.matches('input[type=checkbox]') && !el.closest('fieldset') && !el.labels.length) failures.push('checkbox without label');
  }
  const rgb = value => (value.match(/[\d.]+/g) || []).map(Number);
  const luminance = value => { const v = rgb(value).slice(0,3).map(x => { x /= 255; return x <= .04045 ? x/12.92 : ((x+.055)/1.055)**2.4; }); return .2126*v[0]+.7152*v[1]+.0722*v[2]; };
  const background = el => { for (let p=el;p;p=p.parentElement) { const color=getComputedStyle(p).backgroundColor, c=rgb(color); if (c.length===3 || c[3]===1) return color; } return 'rgb(255,255,255)'; };
  let contrast = 0;
  for (const el of document.querySelectorAll('a,button,label,legend,h1,h2,h3,p,dt,dd,.helptext,.badge,summary')) {
    if (!visible(el) || el.disabled || !el.textContent.trim()) continue;
    const s=getComputedStyle(el), a=luminance(s.color), b=luminance(background(el)), ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);
    const large=parseFloat(s.fontSize)>=24 || (parseFloat(s.fontSize)>=18.66 && Number(s.fontWeight)>=700);
    contrast++;
    if (ratio < (large ? 3 : 4.5)) failures.push(`contrast ${el.tagName} ${ratio.toFixed(2)}`);
  }
  return {failures, controls, contrast, width:innerWidth, scrollWidth:document.documentElement.scrollWidth};
}'''


class BrowserChecks(unittest.TestCase):
    width = 360

    @classmethod
    def setUpClass(cls):
        cls.contexts = [browser.new_context(viewport={'width': cls.width, 'height': 900}) for _ in range(4)]
        cls.admin, cls.manager, cls.first, cls.second = [context.new_page() for context in cls.contexts]
        cls.errors = []
        for page, email in zip([cls.admin, cls.manager, cls.first, cls.second], emails):
            page.on('pageerror', lambda error: cls.errors.append(str(error)))
            page.set_default_timeout(20000)
            login(page, email)

    @classmethod
    def tearDownClass(cls):
        for context in cls.contexts:
            context.close()

    def audit(self, page, name, screenshot=False):
        result = page.evaluate(AUDIT)
        self.assertEqual(result['failures'], [], result['failures'])
        self.assertEqual(self.errors, [])
        reports.append({'page': name, **result})
        if screenshot:
            page.screenshot(path=str(artifacts / f'{self.width}-{name}.png'), full_page=True)

    def issue(self, name, *, kind='event', recipients=None, page=None):
        page = page or self.manager
        page.goto(web + '/workitems/new/')
        label(page, 'Вид').select_option(kind)
        label(page, 'Название').fill(f'{name} {self.width}')
        label(page, 'Описание').fill('Синтетическая проверка чата')
        label(page, 'Срок выполнения').fill('2030-01-04')
        if kind == 'event':
            label(page, 'Дата и время события (Москва)').fill('2030-01-02T12:00')
        for name in recipients or names[2:]:
            label(page, name).check()
        click(page, 'Показать получателей')
        click(page, 'Подтвердить и выдать')
        path = urllib.parse.urlsplit(page.url).path
        item_id = str(uuid.UUID(path.split('/')[2]))
        return path, f'/events/{item_id}/chat/'

    def send(self, page, body):
        label(page, 'Сообщение').fill(body)
        click(page, 'Отправить сообщение')
        expect(page.locator('.message-text').filter(has_text=body)).to_have_count(1)

    def seed_history(self, chat, count, prefix):
        # Only construct large synthetic history via the same audited service.
        # The rate limiter is omitted for this fixture; the separate quota test is live.
        item_id = str(uuid.UUID(chat.split('/')[2]))
        code = f"""import uuid
from unittest.mock import patch
from apps.accounts.models import User
from apps.discussions.services import send
actor=User.objects.get(email='{emails[2]}')
with patch('apps.discussions.services.consume_limits'):
 for i in range({count}):
  send(actor_id=actor.pk, auth_version=actor.auth_version, item_id=uuid.UUID('{item_id}'), request_key=uuid.uuid4(), text='{prefix} '+str(i))
"""
        run(['exec', '-T', 'web', 'python', 'manage.py', 'shell', '-c', code])

    def test_01_live_poll_history_xss_hide_old_and_refresh_groups(self):
        _, chat = self.issue('История и обновления')
        self.seed_history(chat, 55, 'История')
        self.first.goto(web + chat)
        expect(self.first.locator('[data-message-id]')).to_have_count(50)
        expect(self.first.locator('.message-text').first).to_have_text('История 5')
        self.second.goto(web + chat)
        self.send(self.second, '<script>window.chat_xss=true</script>\nТекст сообщения')
        expect(self.first.locator('.message-text').filter(has_text='window.chat_xss')).to_have_count(1)
        self.assertFalse(self.first.evaluate('Boolean(window.chat_xss)'))
        self.assertFalse(self.second.evaluate('Boolean(window.chat_xss)'))
        self.assertNotIn(emails[2], self.first.locator('main').inner_text())
        # More than one changes page must be drained without skipped messages.
        self.seed_history(chat, 52, 'Добавлено')
        expect(self.first.locator('.message-text').filter(has_text=re.compile(r'^Добавлено \d+$'))).to_have_count(52)
        self.admin.goto(web + chat + '?before_id=' + self.first.locator('[data-message-id]').first.get_attribute('data-message-id'))
        expect(self.admin.locator('.message-text').first).to_have_text('История 0')
        self.audit(self.admin, 'older-history')
        old_id = self.first.locator('[data-message-id]').first.get_attribute('data-message-id')
        self.admin.goto(web + chat + '?before_id=' + str(int(old_id) + 1))
        target = self.admin.locator('#message-' + old_id)
        target.locator('summary').click()
        target.get_by_label('Причина скрытия').fill('Скрыто старое сообщение')
        target.get_by_role('button', name='Скрыть сообщение', exact=True).click()
        expect(self.first.locator('#message-' + old_id + ' .message-text')).to_have_text('Сообщение скрыто. Причина: Скрыто старое сообщение')
        self.assertNotIn('История 5', self.first.locator('#message-' + old_id).inner_text())
        # Actual administration changes current groups; no chat message is sent.
        self.admin.goto(web + employee_paths[2])
        label(self.admin, 'Кураторы 1-го курса').check()
        click(self.admin, 'Сохранить')
        expect(self.first.locator('#message-' + old_id + ' .message-author')).to_contain_text('Кураторы 1-го курса')
        self.audit(self.first, 'live-chat', screenshot=True)

    def test_02_separate_moderator_original_close_archive_and_task_routes(self):
        path, chat = self.issue('Права и закрытие', recipients=[names[2]])
        self.first.goto(web + path)
        click(self.first, 'Выполнено')
        self.first.goto(web + chat)
        body = f'Скрытый оригинал только администратору {self.width}'
        private.append(body)
        self.send(self.first, body)
        message_id = self.first.locator('[data-message-id]').first.get_attribute('data-message-id')
        self.second.goto(web + chat)
        self.assertEqual(self.second.locator('#chat-send').count(), 0)
        # Moderator has global read + moderate, without send_accessible.
        token = self.second.locator('[name=csrfmiddlewaretoken]').first.input_value()
        denied = self.second.request.post(web + chat + 'send/', form={'csrfmiddlewaretoken': token, 'request_key': str(uuid.uuid4()), 'text': 'Нельзя отправлять'}, headers={'Accept': 'application/json'})
        self.assertEqual(denied.status, 403)
        self.second.locator('summary').filter(has_text='Скрыть сообщение').first.click()
        label(self.second, 'Причина скрытия').fill('Модерация без доступа к оригиналу')
        click(self.second, 'Скрыть сообщение')
        expect(self.first.locator('.message-text').first).to_contain_text('Модерация без доступа к оригиналу')
        self.assertNotIn(body, self.first.content())
        self.assertEqual(self.second.get_by_role('link', name=re.compile('Оригинал')).count(), 0)
        response = self.second.goto(web + chat + f'messages/{message_id}/original/')
        self.assertEqual(response.status, 403)
        self.admin.goto(web + chat)
        self.admin.get_by_role('link', name=f'Оригинал сообщения {message_id}', exact=True).click()
        expect(self.admin.locator('.message-text')).to_have_text(body)
        self.manager.goto(web + chat)
        self.manager.get_by_text('Закрытие чата', exact=True).click()
        click(self.manager, 'Закрыть чат')
        expect(self.first.locator('#chat-state')).to_have_text('Чат закрыт — только чтение')
        expect(self.first.locator('#chat-compose')).to_be_hidden()
        self.admin.goto(web + chat)
        self.assertEqual(self.admin.locator('#chat-send').count(), 0)
        self.audit(self.first, 'closed-chat')
        self.manager.goto(web + path)
        self.manager.get_by_text('Архивирование', exact=True).click()
        click(self.manager, 'Архивировать')
        expect(self.first.locator('#chat-state')).to_have_text('Архив — только чтение')
        self.second.goto(web + chat)
        self.assertEqual(self.second.get_by_text('Скрыть сообщение', exact=True).count(), 0)
        task, task_chat = self.issue('Задача без чата', kind='task')
        self.first.goto(web + task)
        self.assertEqual(self.first.get_by_role('link', name='Чат события').count(), 0)
        self.assertEqual(self.first.goto(web + task_chat).status, 404)

    def test_03_revoke_with_open_page_clears_and_blocks_replay(self):
        path, chat = self.issue('Отзыв открытого чата', recipients=[names[2]])
        self.first.goto(web + chat)
        key = self.first.locator('[name=request_key]').input_value()
        token = self.first.locator('[name=csrfmiddlewaretoken]').first.input_value()
        body = 'Текст до отзыва'
        self.send(self.first, body)
        self.manager.goto(web + path + 'assignees/')
        label(self.manager, 'Причина отзыва').fill('Проверка немедленного отзыва')
        click(self.manager, 'Отозвать назначение')
        expect(self.first.get_by_role('heading', name='Доступ к чату прекращен', exact=True)).to_be_visible()
        self.assertEqual(self.first.locator('[data-message-id]').count(), 0)
        self.assertNotIn(body, self.first.content())
        response = self.first.request.post(web + chat + 'send/', form={'csrfmiddlewaretoken': token, 'request_key': key, 'text': body}, headers={'Accept': 'application/json'})
        self.assertEqual(response.status, 404)
        self.assertNotIn(body, response.text())
        self.audit(self.first, 'revoked-chat')

    def test_04_no_javascript_retry_limits_and_csrf(self):
        context = browser.new_context(java_script_enabled=False, viewport={'width': self.width, 'height': 900})
        try:
            page = context.new_page()
            login(page, emails[2])
            _, chat = self.issue('Без JavaScript и лимиты', recipients=[names[2]])
            page.goto(web + chat)
            key = page.locator('[name=request_key]').input_value()
            token = page.locator('[name=csrfmiddlewaretoken]').first.input_value()
            self.send(page, 'Без JavaScript')
            form = {'csrfmiddlewaretoken': token, 'request_key': key, 'text': 'Без JavaScript'}
            repeated = page.request.post(web + chat + 'send/', form=form, headers={'Accept': 'application/json'})
            self.assertEqual(repeated.status, 200)
            self.assertFalse(repeated.json()['created'])
            conflict = page.request.post(web + chat + 'send/', form=form | {'text': 'Другой текст'}, headers={'Accept': 'application/json'})
            self.assertEqual(conflict.status, 409)
            csrf = page.request.post(web + chat + 'send/', form={'request_key': str(uuid.uuid4()), 'text': 'Без CSRF'})
            self.assertEqual(csrf.status, 403)
            for i in range(9):
                response = page.request.post(web + chat + 'send/', form=form | {'request_key': str(uuid.uuid4()), 'text': f'Лимит {i}'}, headers={'Accept': 'application/json'})
                self.assertEqual(response.status, 201)
            limited = page.request.post(web + chat + 'send/', form=form | {'request_key': str(uuid.uuid4()), 'text': 'Одиннадцатое'}, headers={'Accept': 'application/json'})
            self.assertEqual(limited.status, 429)
            self.assertGreater(int(limited.headers['retry-after']), 0)
            page.reload()
            expect(page.locator('[data-message-id]')).to_have_count(10)
            self.audit(page, 'no-js-chat', screenshot=True)
        finally:
            context.close()


class MobileChecks(BrowserChecks):
    width = 360


class DesktopChecks(BrowserChecks):
    width = 1280


class RedactedStream:
    def write(self, value):
        for secret in private:
            value = value.replace(secret, '[REDACTED]')
        value = re.sub(r'([?&]token=)[^&\s<>"\']+', r'\1[REDACTED]', value)
        original_stderr.write(value)

    def flush(self):
        original_stderr.flush()


sys.stderr = RedactedStream()
run(['exec', '-T', 'web', 'python', 'manage.py', 'shell', '-c', 'from apps.accounts.models import User; assert User.objects.count() == 0'])
emails = [f'stage6.{name}@corp.kp11.ru' for name in ['admin', 'manager', 'first', 'second']]
names = ['Администратор проверки', 'Руководитель проверки', 'Первый исполнитель ' + 'ДлинноеФИО' * 20, 'Второй исполнитель проверки']
employee_paths = {}
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(executable_path=args.chromium, headless=True, args=['--no-sandbox'])
    try:
        run(['exec', '-T', 'web', 'python', 'manage.py', 'bootstrap_admin', '--email', emails[0], '--full-name', names[0]])
        activate(browser, emails[0])
        setup = browser.new_context()
        page = setup.new_page()
        login(page, emails[0])
        for index, (email, name, role) in enumerate(zip(emails[1:], names[1:], ['Руководитель', 'Преподаватель', 'Преподаватель']), 1):
            page.goto(web + '/administration/invite/')
            label(page, 'Корпоративная почта').fill(email)
            label(page, 'ФИО').fill(name)
            label(page, role).check()
            click(page, 'Пригласить')
            expect(page.get_by_role('heading', name='Сотрудник приглашен', exact=True)).to_be_visible()
            employee_paths[index] = urllib.parse.urlsplit(page.get_by_role('link', name='Открыть сотрудника', exact=True).get_attribute('href')).path
            activate(browser, email)
        page.goto(web + '/administration/roles/new/')
        label(page, 'Название роли').fill('Читатель и модератор чата')
        label(page, 'Просмотр всех заданий').check()
        label(page, 'Модерация чата доступного события').check()
        click(page, 'Сохранить')
        page.goto(web + employee_paths[3])
        label(page, 'Читатель и модератор чата').check()
        click(page, 'Сохранить')
        setup.close()
        suite = unittest.TestSuite()
        for case in [MobileChecks, DesktopChecks]:
            suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
        result = unittest.TextTestRunner(stream=RedactedStream(), verbosity=2).run(suite)
        logs = run(['logs', '--no-color', 'web']).stdout
        assert not any(value in logs for value in private) and '?token=' not in logs, 'Secret or hidden original in web logs'
        report = {'run_id': run_id, 'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped), 'auth_headers': auth_headers, 'audits': reports, 'widths': [360, 1280], 'browser': browser.version}
        (artifacts / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(f'Run {run_id}. Browser: {result.testsRun} tests; {len(reports)} DOM/layout/contrast audits; Chromium {browser.version}; secrets and hidden originals absent in web logs')
        raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:
        browser.close()
