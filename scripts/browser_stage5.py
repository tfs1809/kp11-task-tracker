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

parser = argparse.ArgumentParser()
parser.add_argument('--project', required=True)
parser.add_argument('--web-port', type=int, required=True)
parser.add_argument('--mailpit-port', type=int, required=True)
parser.add_argument('--chromium', default='/usr/bin/chromium')
parser.add_argument('--artifacts', default='test-results/stage5')
args = parser.parse_args()
if not args.project.startswith('kp11-stage5-browser-') or args.web_port == 8000 or args.mailpit_port == 8025:
    parser.error('Нужны отдельный kp11-stage5-browser-* project и отдельные порты.')
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
        for context in cls.contexts:
            context.set_default_timeout(10000)
        cls.admin, cls.manager, cls.first, cls.second = [context.new_page() for context in cls.contexts]
        cls.errors = []
        cls.paths = {}
        for page, email in zip([cls.admin, cls.manager, cls.first, cls.second], emails):
            page.on('pageerror', lambda error: cls.errors.append(str(error)))
            login(page, email)

    @classmethod
    def tearDownClass(cls):
        for context in cls.contexts:
            context.close()

    def audit(self, page, name, screenshot=False):
        result = page.evaluate(AUDIT)
        self.assertEqual(result['failures'], [], f'{self.width} / {name}: {result["failures"]}')
        self.assertFalse(self.errors, 'Browser JS errors')
        reports.append({'page': name, **result})
        if screenshot:
            page.screenshot(path=str(artifacts / f'{self.width}-{name}.png'), full_page=True)

    def issue(self, title, kind='task', due='2020-10-10', subtasks='Первый пункт\nВторой пункт', page=None, recipients=None, back=False):
        page = page or self.manager
        page.goto(web + '/workitems/new/')
        label(page, 'Вид').select_option(kind)
        label(page, 'Название').fill(title)
        label(page, 'Описание').fill('Длинное описание ' * 30 + '<script>window.stage5_xss=true</script>')
        label(page, 'Срок выполнения').fill(due)
        if kind == 'event':
            label(page, 'Дата и время события (Москва)').fill('2030-01-02T12:00')
        label(page, 'Подзадачи: каждая на отдельной строке').fill(subtasks)
        for name in recipients or names[2:]:
            label(page, name).check()
        self.audit(page, 'issue')
        click(page, 'Показать получателей')
        expect(page.get_by_role('heading', name='Получатели: 2' if recipients is None else f'Получатели: {len(recipients)}', exact=True)).to_be_visible()
        if back:
            click(page, 'Вернуться к форме')
            expect(label(page, 'Название')).to_have_value(title)
            expect(label(page, 'Подзадачи: каждая на отдельной строке')).to_have_value(subtasks)
            click(page, 'Показать получателей')
        self.audit(page, 'preview')
        click(page, 'Подтвердить и выдать')
        expect(page.get_by_role('heading', name=title, exact=True)).to_be_visible()
        self.assertFalse(page.evaluate('Boolean(window.stage5_xss)'))
        return urllib.parse.urlsplit(page.url).path

    def finish(self, page, path):
        page.goto(web + path)
        while page.locator('button[role=checkbox][aria-checked=false]').count():
            page.locator('button[role=checkbox][aria-checked=false]').first.click()
        click(page, 'Выполнено')
        expect(page.get_by_role('button', name='Вернуть', exact=True)).to_be_visible()

    def test_01_full_workflow_and_privacy(self):
        title = f'Проверка {self.width} ' + 'ДлинноеНазвание' * 12
        path = self.issue(title, back=True)
        self.paths['workflow'] = path
        self.first.goto(web + path)
        expect(self.first.get_by_role('button', name='Выполнено', exact=True)).to_be_disabled()
        expect(self.first.get_by_text('Просрочено.', exact=False)).to_be_visible()
        self.first.locator('button[role=checkbox][aria-checked=false]').first.click()
        self.second.goto(web + path)
        self.assertEqual(self.second.locator('button[role=checkbox][aria-checked=true]').count(), 0)
        self.assertNotIn(names[2], self.second.locator('main').inner_text())
        self.assertEqual(self.second.get_by_role('link', name='Все исполнители', exact=True).count(), 0)
        self.assertEqual(self.second.get_by_role('link', name='Редактировать', exact=True).count(), 0)
        response = self.second.goto(web + path + 'assignees/')
        self.assertEqual(response.status, 403)
        self.finish(self.first, path)
        self.manager.goto(web + path)
        expect(self.manager.get_by_text('Активных: 2 · Выполнили: 1 · 50%', exact=True)).to_be_visible()
        click(self.first, 'Вернуть')
        self.assertEqual(self.first.locator('button[role=checkbox][aria-checked=true]').count(), 2)
        self.finish(self.first, path)
        self.manager.goto(web + path + 'edit/')
        self.manager.locator('[name=subtasks-0-text]').fill('Первый пункт изменен')
        click(self.manager, 'Показать последствия')
        expect(self.manager.get_by_text('Сброс выполнения у 2 активных исполнителей, из них выполнили: 1.', exact=False)).to_be_visible()
        click(self.manager, 'Вернуться к форме')
        expect(self.manager.locator('[name=subtasks-0-text]')).to_have_value('Первый пункт изменен')
        click(self.manager, 'Показать последствия')
        click(self.manager, 'Подтвердить изменения')
        self.first.goto(web + path)
        self.assertEqual(self.first.locator('button[role=checkbox][aria-checked=false]').count(), 1)
        self.assertEqual(self.first.locator('button[role=checkbox][aria-checked=true]').count(), 1)
        self.audit(self.first, 'personal-detail', screenshot=True)
        # Две открытые формы: 409 и сохранение ввода.
        old = self.contexts[1].new_page()
        old.goto(web + path + 'edit/')
        self.manager.goto(web + path + 'edit/')
        label(self.manager, 'Название').fill(title + ' новая')
        click(self.manager, 'Показать последствия')
        click(self.manager, 'Подтвердить изменения')
        label(old, 'Название').fill('Сохраненный конфликт')
        with old.expect_response(lambda response: response.url.endswith('/edit/') and response.request.method == 'POST') as sent:
            click(old, 'Показать последствия')
        self.assertEqual(sent.value.status, 409)
        expect(label(old, 'Название')).to_have_value('Сохраненный конфликт')
        expect(old.get_by_role('alert')).to_be_visible()
        self.audit(old, 'version-conflict')
        old.close()
        self.manager.goto(web + path + 'assignees/')
        first_row = self.manager.locator('.roster-list > li').filter(has=self.manager.get_by_role('link', name=names[2], exact=True))
        label(first_row, 'Причина отзыва').fill('Проверка отзыва браузером')
        first_row.get_by_role('button', name='Отозвать назначение', exact=True).click()
        self.assertEqual(self.first.goto(web + path).status, 404)
        self.manager.goto(web + path + 'add/')
        label(self.manager, names[2]).check()
        click(self.manager, 'Показать получателей и восстановление')
        expect(self.manager.get_by_text('Восстановление прежнего прогресса', exact=False)).to_be_visible()
        click(self.manager, 'Подтвердить изменения')
        self.first.goto(web + path)
        self.assertEqual(self.first.locator('button[role=checkbox][aria-checked=true]').count(), 1)
        self.manager.goto(web + path)
        self.manager.get_by_text('Архивирование', exact=True).click()
        click(self.manager, 'Архивировать')
        expect(self.manager.get_by_text('Архив — только чтение', exact=True)).to_be_visible()
        self.first.goto(web + path)
        self.assertEqual(self.first.locator('button[role=checkbox]').count(), 0)
        self.assertEqual(self.first.get_by_role('button', name='Выполнено', exact=True).count(), 0)
        self.first.goto(web + '/workitems/archive/')
        expect(self.first.get_by_role('link', name=title + ' новая', exact=True)).to_be_visible()
        self.audit(self.first, 'archive', screenshot=True)

    def test_02_tabs_filters_sort_and_navigation(self):
        task = self.issue(f'Ближайшая {self.width}', subtasks='', recipients=[names[2]])
        event = self.issue(f'Событие {self.width}', kind='event', due='2030-01-04', subtasks='', recipients=[names[2]])
        self.first.goto(web + '/')
        label(self.first, 'Вид').select_option('event')
        label(self.first, 'Сортировка по сроку').select_option('-due')
        label(self.first, 'Автор').select_option(label=names[1])
        click(self.first, 'Применить')
        self.assertEqual(self.first.get_by_role('link', name=f'Ближайшая {self.width}', exact=True).count(), 0)
        expect(self.first.get_by_role('link', name=f'Событие {self.width}', exact=True)).to_be_visible()
        self.finish(self.first, event)
        self.first.goto(web + '/?tab=completed&kind=event&sort=-due')
        expect(self.first.get_by_role('link', name=f'Событие {self.width}', exact=True)).to_be_visible()
        expect(self.first.locator('nav[aria-label="Статус личных задач"] a[aria-current]')).to_contain_text('Выполнены')
        assigned_link = self.first.locator('nav[aria-label="Статус личных задач"] a').first
        self.assertIn('kind=event', assigned_link.get_attribute('href'))
        self.first.goto(web + '/?kind=task')
        expect(self.first.get_by_role('link', name=f'Ближайшая {self.width}', exact=True)).to_be_visible()
        self.assertEqual(self.first.get_by_role('link', name='Администрирование', exact=True).count(), 0)
        self.assertEqual(self.first.get_by_role('link', name='Все задания', exact=True).count(), 0)
        self.audit(self.first, 'home', screenshot=True)
        self.manager.goto(web + '/workitems/issued/?kind=task')
        expect(self.manager.get_by_role('link', name=f'Ближайшая {self.width}', exact=True)).to_be_visible()
        self.audit(self.manager, 'issued', screenshot=True)
        self.admin.goto(web + '/workitems/all/?kind=event')
        expect(self.admin.get_by_role('link', name=f'Событие {self.width}', exact=True)).to_be_visible()
        self.audit(self.admin, 'all')
        self.paths['keyboard'] = task

    def tab_to(self, page, selector):
        for _ in range(150):
            page.keyboard.press('Tab')
            if page.evaluate('(selector) => document.activeElement.matches(selector)', selector):
                self.assertEqual(page.evaluate('getComputedStyle(document.activeElement).outlineWidth'), '3px')
                return
        self.fail('Keyboard target not reachable')

    def test_03_keyboard_forms_long_text_and_accessibility(self):
        self.paths['keyboard'] = self.issue(f'Клавиатура {self.width}', subtasks='', recipients=[names[2]])
        page = self.first
        page.goto(web + '/')
        page.keyboard.press('Tab')
        expect(page.get_by_role('link', name='Перейти к содержимому', exact=True)).to_be_focused()
        page.keyboard.press('Enter')
        self.assertEqual(page.evaluate('document.activeElement.id'), 'main')
        href = self.paths['keyboard']
        self.tab_to(page, f'a[href="{href}"]')
        page.keyboard.press('Enter')
        page.wait_for_url(web + href)
        self.tab_to(page, 'button[type=submit][name="unused"], form[action$="/completion/"] button')
        page.keyboard.press('Enter')
        expect(page.get_by_role('button', name='Вернуть', exact=True)).to_be_visible()
        self.tab_to(page, 'form[action$="/completion/"] button')
        page.keyboard.press('Enter')
        expect(page.get_by_role('button', name='Выполнено', exact=True)).to_be_visible()
        # Ошибка сервера сохраняет длинный текст, email и адресатов.
        self.admin.goto(web + '/administration/invite/')
        label(self.admin, 'ФИО').fill('ДлинноеФИО' * 25)
        label(self.admin, 'Корпоративная почта').fill('invalid@example.org')
        label(self.admin, 'Преподаватель').check()
        click(self.admin, 'Пригласить')
        expect(self.admin.get_by_role('alert')).to_be_visible()
        expect(label(self.admin, 'Корпоративная почта')).to_have_value('invalid@example.org')
        expect(label(self.admin, 'Преподаватель')).to_be_checked()
        self.audit(self.admin, 'invite-error', screenshot=True)
        for path, name in [('/administration/', 'administration'), ('/administration/roles/new/', 'role-form'), ('/administration/groups/new/', 'group-form')]:
            self.admin.goto(web + path)
            self.audit(self.admin, name)
        label(self.admin, 'Название группы адресатов').fill(f'Группа браузера {self.width}')
        click(self.admin, 'Сохранить')
        expect(self.admin.get_by_role('link', name=f'Группа браузера {self.width}', exact=True)).to_be_visible()
        # JS добавляет строку с новыми уникальными ids и фокусом.
        self.manager.goto(web + self.paths['keyboard'] + 'edit/')
        click(self.manager, 'Еще подзадача')
        expect(self.manager.locator('[name=subtasks-1-text]')).to_be_focused()
        self.manager.locator('[name=subtasks-0-text]').fill('ДлиннаяПодзадача' * 40)
        self.manager.locator('[name=subtasks-0-position]').fill('1')
        self.manager.locator('[name=subtasks-1-text]').fill('Добавленная строка')
        click(self.manager, 'Показать последствия')
        self.audit(self.manager, 'edit-preview')
        click(self.manager, 'Подтвердить изменения')
        self.first.goto(web + self.paths['keyboard'])
        self.audit(self.first, 'long-checklist')
        self.tab_to(self.first, 'button[role=checkbox][aria-checked=false]')
        self.first.keyboard.press('Space')
        expect(self.first.locator('button[role=checkbox][aria-checked=true]')).to_have_count(1)

    def test_04_without_javascript_issue_completion_and_return(self):
        contexts = [browser.new_context(java_script_enabled=False, viewport={'width': self.width, 'height': 900}) for _ in range(2)]
        try:
            manager, first = [context.new_page() for context in contexts]
            login(manager, emails[1])
            login(first, emails[2])
            path = self.issue(f'Без JavaScript {self.width}', page=manager, recipients=[names[2]], back=True)
            first.goto(web + path)
            expect(first.get_by_role('button', name='Выполнено', exact=True)).to_be_disabled()
            self.finish(first, path)
            click(first, 'Вернуть')
            self.assertEqual(first.locator('button[role=checkbox][aria-checked=true]').count(), 2)
            manager.goto(web + path + 'edit/')
            self.assertTrue(manager.locator('[data-add-subtask]').is_hidden())
            manager.locator('[name=subtasks-0-text]').fill('Изменение без JavaScript')
            click(manager, 'Показать последствия')
            click(manager, 'Подтвердить изменения')
            first.goto(web + path)
            self.assertEqual(first.locator('button[role=checkbox][aria-checked=false]').count(), 1)
            self.audit(first, 'no-js')
        finally:
            for context in contexts:
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
emails = [f'stage5.{name}@corp.kp11.ru' for name in ['admin', 'manager', 'first', 'second']]
names = ['Администратор проверки', 'Руководитель проверки', 'Первый исполнитель ' + 'ДлинноеФИО' * 20, 'Второй исполнитель проверки']
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(executable_path=args.chromium, headless=True, args=['--no-sandbox'])
    try:
        run(['exec', '-T', 'web', 'python', 'manage.py', 'bootstrap_admin', '--email', emails[0], '--full-name', names[0]])
        activate(browser, emails[0])
        setup = browser.new_context()
        page = setup.new_page()
        login(page, emails[0])
        for email, name, role in zip(emails[1:], names[1:], ['Руководитель', 'Преподаватель', 'Преподаватель']):
            page.goto(web + '/administration/invite/')
            label(page, 'Корпоративная почта').fill(email)
            label(page, 'ФИО').fill(name)
            label(page, role).check()
            click(page, 'Пригласить')
            expect(page.get_by_role('heading', name='Сотрудник приглашен', exact=True)).to_be_visible()
            activate(browser, email)
        setup.close()
        suite = unittest.TestSuite()
        for case in [MobileChecks, DesktopChecks]:
            suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
        result = unittest.TextTestRunner(stream=RedactedStream(), verbosity=2).run(suite)
        logs = run(['logs', '--no-color', 'web']).stdout
        assert not any(value in logs for value in private) and '?token=' not in logs, 'Secret in web logs'
        report = {'run_id': run_id, 'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped), 'auth_headers': auth_headers, 'audits': reports, 'widths': [360, 1280], 'browser': browser.version}
        (artifacts / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(f'Run {run_id}. Browser: {result.testsRun} tests; {len(reports)} DOM/layout/contrast audits; Chromium {browser.version}; secrets absent in web logs')
        raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:
        browser.close()
