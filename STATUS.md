# Состояние проекта

Обновлено: 11.10.2026 01:36 Москва.

Проектирование и этапы 1–5 приняты пользователем. Коммиты: **34b12bb**, **f34e6b1**, **d3854b7**, **ba89479**, принятый этап 5 **7b6f134** (`Implement responsive work item interface and browser validation`). **Этап 6 реализован полностью**; пользователь разрешил коммит и push всех изменений в origin/main 11.10.2026. Этап 7 не начинался. SPEC.md, принятая PERMISSIONS.md, runtime/browser lockfiles и digest базовых образов сохранены.

## Реализовано в этапе 6

- EventMessage для событий: bigint id, автор и серверное время, обычный текст 1–4000 символов, скрытие с обязательной причиной, FK last_change к append-only аудиту. Сообщения не редактируются/удаляются; нет вложений и почтовых уведомлений.
- Серверные актуальные права во всех чтениях, POST и повторах: автор с текущим чтением и active исполнители участвуют, выполненное назначение сохраняет чат; view_all дает чтение, send_accessible и moderate — разные действия и не предоставляют чтения сами. Отзыв, снятие роли и деактивация закрывают соответствующее основание доступа.
- MutationReceipt отправки с UUID/digest/результатом, конфликт другого ввода 409. Сообщение, receipt, аудит и расход DB-лимита атомарны. Повтор не создает дублей/аудита и не расходует лимит; после отзыва возвращает отказ, после скрытия — заглушку. При сохраненном чтении повтор после закрытия/архива читает прежний результат без новой записи.
- PostgreSQL quotas для всех процессов: 10 сообщений/минутное окно на пользователя в событии, 30 суммарно по событиям; 429/Retry-After. Guard захватывается до chat bucket locks; auth/invitation bucket locks освобождаются до своего guard. Невозможность аудита откатывает действие и quota.
- История по 50 с before_id, polling по отдельному курсору изменений, порции по 50 и watermark до последнего выданного изменения. Скрытие старого уже показанного сообщения обнаруживается; visible refresh ограничен событием и 50 ids на запрос. Все показанные ФИО/активные группы перечитываются порциями без нового сообщения. Email и чужой прогресс не выдаются.
- Русская адаптивная страница чата, серверные POST формы и история без JS. JS отправляет/обновляет без полной перезагрузки, вставляет текст через textContent, обновляет права и закрытие/архив, polling 10 секунд на видимой вкладке, backoff до 60. Следующий polling 401/403/404 очищает чат и останавливает обновление; доставленный ранее текст сервер ретроспективно не отзывает.
- Автор с edit_own / доступный edit_all закрывает чат по версии; повторное открытие запрещено. Закрытый чат читается и допускает модерацию, архив — только чтение. Новые отправки запрещены всем, включая администратора. Скрытый оригинал отсутствует в обычных HTML/JSON/аудите/логах; separate original page требует системного администратора + audit.view. Autoescape сохранен.
- Миграции discussions 0001/0002 и workitems 0006. DB constraints/triggers защищают event-only, длину/причину, неизменяемость текста/автора/времени, необратимое скрытие/закрытие, audit change и согласованность message receipt. Runtime DELETE/TRUNCATE сообщений запрещены. Прежний populated migration test теперь восстанавливает все актуальные leaf migrations.

## Реально выполненные проверки этапа 6

Linux amd64, Python 3.12.15/Django 5.2.18/psycopg 3.3.6, PostgreSQL 17.11, Mailpit 1.31.4, Docker 28.4.0/Compose 2.40.3. Функциональные проверки выполнены на web image **sha256:8be42cf4879412fb9a517b0d99ed0580963dc7a7f0709dda05c6bb0f3f008630**. После них изменены документация и добавлен отдельно проверенный host-script smoke_chat_workers; код приложения не менялся.

| Команда / проверка | Подтвержденный результат |
| --- | --- |
| Cloud build с BUILDX_CONFIG/compose.cloud.yaml | Exit 0; TLS и закрепленные hashes не отключались |
| `docker compose run --rm migrate`, `docker compose up -d --wait web` | Exit 0; upgrade основной схемы (3 новые миграции) и runtime grants успешны; web/db/Mailpit healthy |
| Два новых изолированных Compose projects | Все миграции с нуля, runtime grants/readiness успешны |
| `docker compose run --rm tests` | **162 tests, 162 passed, 0 failures/errors/skipped**, **122.694 секунды**, exit 0; test_kp11 создана и удалена. 134 прежних + 28 новых chat tests |
| PostgreSQL concurrency | Одновременные повторы (4 соединения): 1 сообщение/аудит; 12 соединений: 10 отправок/2 rate refusals. Send/revoke, send/close, hide/archive сериализованы; после commit отзыва/закрытия/архива запрещенная запись не проходит |
| `scripts/browser_stage6.py --project kp11-stage6-browser-run1-20261011 --web-port 18210 --mailpit-port 18235` через .venv-browser | **8 tests, 8 passed, 0 failures/errors/skipped**, **177.277 секунды**, exit 0; настоящие страницы **360/1280 px**, Chromium **151.0.7922.173**, Playwright **1.62.0** |
| Текущий stage6 browser JSON | run_id **b3674110-c963-4808-aeaf-2d501ac4ecc0** совпал с runner; **10 DOM/layout/contrast audits**, 44 экземпляра controls, 876 замеров текстового контраста; failures/duplicate ids/missing labels/overflow отсутствуют. 4 реальные SMTP-активации с корректными CSRF/Origin/Referer |
| Browser chat cases | История 55 и следующие 52 fixtures: порции/старые страницы и новые обновления без пропусков; живой HTML/XSS показан текстом. Старое сообщение скрыто на открытой странице; текущая группа обновилась без нового сообщения. Completed assignment сохранил отправку. Moderator отдельно от отправителя/администратора, отдельный оригинал, close/archive, task 404, отзыв с очисткой страницы и повтором 404, no-JS отправка, retry 200, conflict 409, CSRF 403, quota 429 |
| Fixtures больших histories | Созданы настоящим service с receipt/audit; consume_limits отключен только при подготовке 55/52 сообщений. Это не проверка 107 успешных пользовательских отправок сверх quota. Отдельные browser HTTP quota tests выполнялись без отключения лимита |
| `python3 scripts/smoke_chat_workers.py --project kp11-stage6-browser-run1-20261011` | Exit 0, run_id **e3157b4a-c71c-408a-ba29-8cd2d44092d2**; два отдельных процесса (PID 483/490), каждый 5 отправок/1 отказ. БД: **10 messages, 10 receipts, 10 audit**. Окно времени одинаково фиксировано в обоих процессах, PostgreSQL quotas/locks настоящие |
| Stage5 Chromium regression на новой схеме | `browser_stage5.py --project kp11-stage5-browser-stage6-regression --web-port 18250 --mailpit-port 18275 --artifacts test-results/stage6-stage5-regression`: **8/8**, 0 failures/errors/skipped, **35.097 секунды**, exit 0. run_id **0ded8516-acbd-4a35-ae74-46c954a3a914**, **46 DOM audits**, 340 controls/956 contrast samples; keyboard/focus/labels и no-JS прежних workflows прошли |
| Секреты/оригиналы в server logs | Оба browser runners проверили отсутствие всех текущих случайных паролей/activation tokens/query links; stage6 также проверил скрытые оригиналы. Auth traces/HAR/video не создавались |
| Browser screenshots | 4 screenshots stage6; просмотрен 360-no-js-chat. Это просмотр артефакта, не ручной проход человеком |
| `check --database default`, `makemigrations --check --dry-run`, runtime `pip check` | Exit 0: 0 issues, No changes detected, No broken requirements |
| `collectstatic --noinput` | Exit 0; CSS и оба JS скопированы (3 файла) |
| `python3 scripts/smoke.py` | Exit 0: реальные login/русский HTML/CSS/no-store/private health/DB и новое matching SMTP-письмо Mailpit |
| `scripts/check_production.py` | Exit 0; одно известное **security.W021**, HSTS preload выключен сознательно до внешней HTTPS-проверки. Только настройки с example hosts/SMTP, не production-запуск |
| `environment_probe --marker stage1-persist --check-only` | Exit 0, прежний маркер сохранен |
| Основная development БД | **0 Users, 0 WorkItems, 0 EventMessages** после проверок; synthetic fixtures только в отдельных projects |
| EXPLAIN ANALYZE/BUFFERS | На маленькой изолированной истории history: backward PK index scan, около 0.063 ms; changes: seq scan/top-N sort, около 0.061 ms. Не production/load benchmark; составные индексы созданы |
| compileall / Compose config / git diff --check | Exit 0; выполнялись на текущих Python-файлах и конфигурации |

Серверные проверки дополнительно подтверждают пустой/длинный текст и UUID, scope чужих message ids, отказ обычному аудитору/модератору в оригинале, hidden text отсутствует в JSON/replay/audit, неизменяемость SQL, malformed cursors, page boundary, текущие группы, readonly архив, revoke/deactivate/revoke-role с открытой сессией, общую квоту 30 по разным событиям и полный rollback при отказе аудита. В обеих browser suites нет skipped tests; предварительные неуспешные запуски не подменяют финальные результаты.

## Неуспешные запуски этапа 6

- Первая генерация миграций host-venv остановилась на отсутствующем DJANGO_SECRET_KEY; bind mount с runtime UID 10001 не прочитал checkout. Миграции сгенерированы отдельным временным tool-container с --user root; runtime web остался UID 10001, .env не раскрывался/не изменялся.
- Первый chat suite остановился при миграции на синтаксисе PL/pgSQL CASE: **0 тестов выполнено**. Добавлены скобки выражения CASE, миграции затем успешно применены с нуля и к прежней схеме.
- Следующий chat suite: **27 tests, 1 failure, 1 error**, 27.725 s. Ошибки тестов: русский JSON проверялся как недекодированная строка, после деактивации использовалась устаревшая версия User. Исправлены decoded assertions и fresh User.
- Третий chat suite: **27 tests, 1 error**, 27.399 s: после реактивации fixture User оставался is_active=False в памяти. Перечитан после обеих операций; финальные 162 tests включают этот отрицательный сценарий и проходят. Assertions не удалялись и лимиты не отключались ради quota tests.

## Конфигурация среды и пределы проверки этапа 6

Изолированные kp11-stage6-browser-run1-20261011 и kp11-stage5-browser-stage6-regression остановлены через down **без -v**. Их volumes сохранены; исходные .env/основные volumes сохранены. Пользователь разрешил коммит и push всех изменений, включая этап 6; разрешения на этап 7 нет. Точные команды запуска, browser/worker checks и ручной сценарий — [README.md](README.md).

Reusable **start_skill сохранен**: принятый коммит этапа 5, текущие миграции/чат и отдельные stage6 browser/worker checks. Ответ configuration tool: **status=saved, requires_publish=true**. install_script, сеть и секреты не изменены. Сохранение draft не исполняет команды и не публикует snapshot; публикация остается действием пользователя в настройках среды. Новая cloud-задача/восстановление не проверялись.

**Не выполнены:** ручной проход человеком, screen reader, другие браузеры/ОС, восстановление snapshot в новой cloud-задаче, внешние production HTTPS/proxy/SMTP/Gunicorn deployment, dump/restore, load/длительные outages/backoff. Наличие кода visibility/backoff и successful local tests не объявляется проверкой этих внешних/длительных условий. Для этапа 6 блокирующих проектных вопросов нет; внешние P1–P4 остаются для этапа 7.

## История этапа 5 (принят, коммит 7b6f134)

Далее сохранены фактические результаты на момент завершения этапа 5; их image/run_id относятся к прежнему коду. Текущие повторные проверки находятся выше.

## Реализовано в этапе 5

- Русские «Мои задачи» с вкладками «Назначены» / «Выполнены», счетчиками, автором/видом и сортировкой срока в обе стороны. По умолчанию ближайшие сверху. Переключение вкладок сохраняет фильтры; сброс фильтров сохраняет вкладку. Завершение/возврат меняет категорию назначения, сохраняет предусмотренные отметки/историю.
- Серверные фильтры поверх ограниченных selectors: варианты автора только из доступных объектов, чужие данные/прогресс не появляются в списках. Неизвестное значение дает 400 с пустым результатом, не отменяет ограничения. Детерминированный due_at + UUID, страницы по 20 объектов, параметры сохраняются между страницами.
- «Выданные», отдельные «Все задания» по праву и доступный архив с фильтрами; архивные исключены из текущих списков. Карточки показывают вид, автора, личную дату назначения, срок/событие, отметки/выполнение и текстовую просрочку. Статистика, roster «Все исполнители»/«Не выполнили», readonly progress и история доступны только по текущим основаниям чтения.
- Навигация/действия по текущим правам, административные разделы и forms сотрудников/ролей/групп. Крупные controls, labels/legend, помощь, сообщения ошибок и сохранение полей/checkbox choices. Сервер повторно проверяет права независимо от видимости кнопок.
- Светлая адаптивная верстка: карточки на телефоне, две колонки на ПК, перенос длинных ФИО/текстов, текстовые состояния вместе с цветом. Видимый keyboard focus, skip-link и доступные checkbox semantics личных отметок. Небольшой JS добавляет строки checklist с уникальными ids/labels и фокусом; server validation остается обязательной.
- Превью и возврат к заполненной форме работают без JS, с проверкой подписи/actor/item/текущих прав. Все формы имеют явный правильный action, включая повтор после конфликта/возврата. Без JS работают выдача, отметки, completion/return и редактирование с подтверждением.
- Исправлен реальный браузерный отказ CSRF локальных HTTP-форм: **Referrer-Policy: strict-origin** вместо no-referrer. Origin остается правильным, Referer содержит только origin без пути/query/секретного токена; CSRF/проверка Origin не отключены.

## Реально выполненные проверки этапа 5

Linux amd64; Docker 28.4.0/Compose 2.40.3; runtime Python 3.12.15, Django 5.2.18, psycopg 3.3.6; PostgreSQL 17.11, Mailpit 1.31.4. Все DB/конкурентные проверки на PostgreSQL, без SQLite. Финальный функциональный web image **sha256:35dfb0d59a18c770d764c3f89ce93a41a328a04083d9480df2e5ef26277a4fc7**. После этой сборки изменялась только документация.

| Команда / проверка | Реальный результат |
| --- | --- |
| Cloud build с BUILDX_CONFIG и compose.cloud.yaml | Exit 0; закрепленные runtime зависимости, TLS и lock hashes сохранены |
| `docker compose run --rm migrate`, `docker compose up -d --wait web` | Exit 0; No migrations to apply, grants успешны, основная db/web/Mailpit healthy |
| Startup пустых изолированных PostgreSQL projects | Все миграции с нуля, grants и readiness успешны; browser/mail links используют matching PUBLIC_BASE_URL |
| `docker compose run --rm tests` на финальном коде | **134 tests, 134 passed, 0 failed, 0 skipped**, **107.858 секунды**, exit 0. Fresh test_kp11 создана миграциями и удалена после suite |
| `.venv-browser/bin/python scripts/browser_stage5.py --project kp11-stage5-browser-run6-20261011 --web-port 18170 --mailpit-port 18195` | **8 tests, 8 passed, 0 failures/errors/skipped**, **39.248 секунды**, exit 0. Chromium **151.0.7922.173**, Playwright **1.62.0**, реальные страницы на **360/1280 px**; 4 активации через SMTP, изолированные cookies, реальные CSRF/Origin/Referer |
| Проверка текущего browser JSON | run_id **f2028766-a910-422d-b17e-c3ab199594fc** совпал с runner; **46 успешных DOM/layout/contrast audits**, 340 проверенных экземпляров controls и 956 замеров текстового контраста. На проверенных страницах scrollWidth == innerWidth; duplicate ids/missing labels/contrast failures отсутствуют. Четыре запроса активации: правильный Origin и Referer без пути/query |
| Keyboard / без JavaScript | На обеих ширинах Tab/Enter/Space: skip-link, переход к задаче, выполнение/возврат и отметка; видимый focus 3px проверен. Два отдельные no-JS сценария проходят выдачу/возврат из preview, отметки/выполнение/возврат и edit/reset; дополнительная JS-кнопка скрыта |
| Browser screenshots | 10 текущих screenshots синтетических рабочих страниц в игнорируемом test-results/stage5. Просмотрены 360-home и 1280-issued; это просмотр артефактов, не ручной проход приложением человеком |
| `scripts/smoke_accounts.py` на kp11-stage2-smoke-stage5-regression, 18160/18185 | Exit 0: прежний HTTP/SMTP сценарий этапа 2 прошел после изменения form rendering/referrer policy, включая безопасные web logs |
| `scripts/smoke_workflow.py` на kp11-stage4-smoke-stage5-regression, 18130/18155 | Exit 0: независимые marks/completion/return, reorder/reset/reconfirm, revoke/restore, версии, deactivation/reactivation, статистика/история и readonly archive. Проверка видимых текстов адаптирована к новым span, условия не ослаблены |
| `docker compose exec -T web python manage.py check --database default` | Exit 0, no issues (0 silenced) |
| `docker compose run --rm migrate python manage.py makemigrations --check --dry-run` | Exit 0, No changes detected; models/миграции не менялись |
| Runtime и browser venv `python -m pip check` | Exit 0, No broken requirements found. Отдельные browser wheels скачаны с PyPI, установлены с --require-hashes и trusted system CA; runtime lock не менялся |
| `python3 scripts/smoke.py` | Exit 0: redirect/login, русский HTML/CSS, no-store, закрытый health/DB readiness и новое SMTP-письмо именно текущего запуска |
| `docker compose exec -T web python scripts/check_production.py` | Exit 0 **с одним security.W021** (HSTS preload выключен); только settings с примерами hosts/SMTP, внешние HTTPS/SMTP не проверялись |
| `docker compose exec -T web python manage.py collectstatic --noinput` | Exit 0, CSS и JS собраны runtime-пользователем |
| Compose config --quiet, Python compileall, 22 локальные Markdown-ссылки, git diff --check | Exit 0; SPEC/PERMISSIONS, runtime lock/base image pins и models/миграции не изменены |
| Основная development-БД и environment_probe stage1-persist | Exit 0, **0 пользователей, 0 WorkItem**, прежний маркер сохранен; bootstrap/синтетические сотрудники только в test_kp11 или изолированных projects |

134 теста = прежние 125 + 9 новых interface tests. Новые: scoped filters/author directory, default/reverse order, completed/return и сохранение фильтров, invalid query 400 без выдачи полного списка, revoked/archive исключения, pagination, own/all/nav ACL, back-preview сохранение ввода/action/actor без мутации, снятие роли при открытой сессии. Прежние PostgreSQL constraints/migrations/concurrency проверки входят в итоговый suite.

8 browser tests = по четыре больших сценария на каждой ширине: full workflow/privacy/reset/stale form/revoke/restore/archive; tabs/filters/sort/navigation; keyboard/forms/errors/long text/accessibility/JS-row; no-JS workflow. Сами письма/активации выполняются в подготовке и не выданы за дополнительные unittest cases. DOM audit проверяет подписи видимых controls/checkbox groups, уникальность ids и горизонтальную ширину, контраст текстов (4.5:1 обычный, 3:1 крупный); это не сертификация доступности или проверка screen reader.

## Неуспешные запуски и исправления этапа 5

- До финальных изменений были два успешных промежуточных server suite: **134/134**, 91.278 и 95.130 секунды. Они не заменяют финальный 107.858-секундный запуск после исправления повторной отправки форм.
- Первые browser-подготовки остановились до unittest cases: selector не учитывал colon/whitespace стандартных Django labels; затем реальная активация получила CSRF 403. Диагностика показала совпадение cookie/form CSRF и **Origin:null** при no-referrer. strict-origin исправил отказ, не отключая CSRF. Следующие подготовительные ошибки — неоднозначный text selector «Пароль сохранен» и whitespace checkbox labels; fixtures исправлены на точные семантические selectors. Эти прогоны не засчитаны успешными.
- Первый полный browser suite: **8 tests, 8 errors, 0 skipped**, 134.188 секунды, run_id b7baf508-654a-43ea-add2-992b5d8460eb. После back form отправлялся на confirmation URL, а не на new/edit/add; исправлены явные action и добавлены server assertions. Также исправлен неверно переписанный helper self.label; keyboard case получил собственный fixture вместо зависимости от предыдущего case. Повтор: 8/8 passed, assertions сохранены.
- Публичные docs.djangoproject.com/playwright.dev оказались закрыты сетевым proxy (403). Официальные Django 5.2 forms docs и Playwright browser API прочитаны из официальных GitHub repositories по разрешенному HTTPS; verification/allowlist не ослаблялись.

## Конфигурация среды и ограничения

Через cloud-environment-onboarding:setup сохранены **install_script** (app build + отдельный browser venv с lock/hash/trusted CA/pip check) и **start_skill** (принятый этап 4, интерфейс/проверки этапа 5, изолированные browser projects, текущий run_id/exit/counts, сохранение .env/volumes). Ответ сервиса: **status=saved, requires_publish=true**. Repositories, network allowlist и secret bindings не менялись. Для будущих задач сохраните изменения в настройках среды и опубликуйте среду; публикация и fresh-task restoration **не выполнены**. Наличие системного Chromium в новой задаче нельзя считать подтвержденным текущим запуском.

- Этапы 6–7 не реализованы: чат и release/backup/restore/production operations.
- Ручной сценарий человеком, screen reader/assistive technology, другие браузеры/OS/CPU, внешние HTTPS/SMTP/proxy, dump/restore и нагрузка **не проверены**. Автоматический Chromium на 360/1280 px действительно выполнен, но не заменяет production-приемку. W021 не скрыт.
- Обычная сборка без cloud overlay за пределами этой среды не проверена. Основные db/web/Mailpit healthy; все изолированные browser/HTTP smoke projects остановлены без удаления volumes. Основная БД не удалялась, .env не перезаписывался, тестовыми сотрудниками она не заполнялась.

Точные команды запуска/проверок, bootstrap, отдельного Chromium suite и короткий ручной сценарий — [README.md](README.md). Production-параметры — [DECISIONS.md](DECISIONS.md).

## История этапа 4 (коммит ba89479)

Ниже сохранены результаты этапа 4 до реализации этапа 5; отсутствие полного UI/browser acceptance и будущий этап 5 относятся к историческому состоянию.

## Реализовано в этапе 4

- SubtaskProgress с версиями, CHECK, уникальностью Assignment/Subtask и двумя составными FK: прогресс не может ссылаться на назначение или подзадачу другого WorkItem. Внутренний bigint ключ строки связи документирован в ARCHITECTURE; доменные ID назначения/подзадачи остаются UUID. При выдаче и миграции существующих назначений создаются пустые отметки.
- Независимые личные отметки, завершение только после всех текущих подзадач (без подзадач — сразу), серверная дата завершения, возврат с сохранением отметок и истории. Просроченное назначение можно выполнить. Автор/администратор не меняют чужой прогресс. Completed-отметки меняются после «Вернуть». No-op не создает повторного аудита.
- Редактирование названия, описания, срока/event_at и checklist со стабильными UUID. Название/срок/reorder не сбрасывают completion; новый/измененный текст подзадачи повышает progress_epoch и снимает completion только у действующих назначений. Сбрасываются только несовместимые отметки; остальные сохраняются. Удаление мягкое, история не теряется, автоматического выполнения нет. kind/author/Subtask.work_item неизменяемы.
- Подписанное серверное превью последствий на 600 секунд, привязанное к actor/item/версии/вводу. До commit под общим AccessGuard повторно проверяются права, версии, назначения/отметки/выполнение и последствия. Изменившиеся последствия требуют нового подтверждения с 409. Устаревшие формы не перезаписывают данные; ввод/выбор сохраняются и требуют нового превью. Содержание, прогресс, completion и аудит изменяются одной транзакцией.
- Отзыв с причиной, добавление по OR-снимку и явное восстановление прежнего Assignment. Сохраняются first_assigned_at, совместимые отметки и источники истории; assigned_at обновляется. Совместимое completion восстанавливается только при совпадении epoch и актуальных отметок, превью предупреждает о восстановлении. Новый исполнитель начинает с пустыми отметками; уже активный не переназначается.
- Деактивация сотрудника отзывает его активные назначения, включая архивные, в той же транзакции, что права/сессии и аудит. Реактивация ничего автоматически не восстанавливает. Миграция 0005 отзывает прежние active-назначения отключенных сотрудников по фактическому аудиту отключения этапа 2; отсутствие такого решения явно останавливает миграцию.
- SQL-статистика active + user.is_active: 3/5 = 60%, при нуле «Нет активных исполнителей» без деления. Даты назначения/завершения, списки «Все исполнители» / «Не выполнили», отдельный readonly detail прогресса. Автор с view_own / читатель с view_all видит общую статистику; обычный исполнитель — только свой прогресс.
- Архивирование окончательное в V1, отмена запрещена trigger; текущие основания чтения сохраняются, mutations закрыты. Отозванный не получает доступ через архив/историю/прямой URL. История показывает даты выполнения/возврата, отзыв/восстановление, отметки со снимком текста и прежнее/новое содержание авторизованному читателю; обычному исполнителю не раскрывает чужие отметки/назначения. Полный интерфейс, фильтры и browser acceptance остаются этапом 5.

## Реально выполненные проверки этапа 4

Linux amd64; Docker 28.4.0/Compose 2.40.3; Python 3.12.15, Django 5.2.18, psycopg 3.3.6; PostgreSQL 17.11, Mailpit 1.31.4. SQLite не использовался. Финальный функциональный образ **sha256:d7a7f49da8df87a6d4c641884c792285149e71e4569960726c09737256fbb85c**; после сборки менялась только документация.

| Команда / проверка | Реальный результат |
| --- | --- |
| `BUILDX_CONFIG="$PWD/.cache/buildx" docker compose -f compose.yaml -f compose.cloud.yaml build web` | Exit 0, зависимости закреплены, TLS/lock hashes сохранены |
| `docker compose run --rm migrate`, `docker compose up -d --wait web` | 0003/0004/0005 и runtime grants применены к существующей development-БД; web healthy |
| Пустая БД `kp11-stage4-smoke-verified-20261010`, web 18020 / Mailpit 18045 | Все миграции с нуля и grants успешны, db/web/Mailpit healthy |
| `docker compose run --rm tests` на финальном коде | **125 tests, 125 passed, 0 failed, 0 skipped**, 82.247 секунды, exit 0; свежая test_kp11 создана миграциями и удалена после suite |
| `docker compose exec -T web python manage.py check --database default` | Exit 0, no issues (0 silenced) |
| `docker compose run --rm migrate python manage.py makemigrations --check --dry-run` | Exit 0, No changes detected |
| `docker compose exec -T web python -m pip check` | Exit 0, No broken requirements found |
| `python3 scripts/smoke.py` | Exit 0: реальный redirect/login, русский HTML/CSS, no-store, закрытый health/DB и новое SMTP-письмо в Mailpit |
| `python3 scripts/smoke_workflow.py --project kp11-stage4-smoke-verified-20261010 --web-port 18020 --mailpit-port 18045` | Exit 0: активации через настоящие письма/CSRF-формы; независимые отметки/partial completion/return; reorder без сброса; изменение completion между preview/submit дает reconfirm 409; reset сохраняет старые отметки; revoke/restore compatible completion; stale edit сохраняет ввод; deactivation/reactivation не восстанавливает; статистика, ограниченная история и readonly archive. Проверено отсутствие использованных паролей/токенов в web logs |
| Реальное обновление сохраненного `kp11-stage3-smoke-20261010` | Старым принятым image отключен сотрудник с 3 active-назначениями; текущие 0004/0005 применены к заполненной БД после исправления порядка DDL. Созданы 2 progress, отозваны 3 назначения с аудитом; реактивация оставила 0 active-назначений сотрудника. Exit 0. Дополнительно PopulatedUpgradeTests мигрирует заполненную схему 0002 → 0005 в suite |
| Restart db/web в отдельном финальном smoke project + повторный smoke.py | Exit 0 после исправления команды Compose; HTTP/readiness/SMTP успешны. Сохранены 1 архивное WorkItem, 1 active + 1 revoked Assignment, 6 SubtaskProgress и аудит архивирования |
| `docker compose exec -T web python scripts/check_production.py` | Exit 0 **с одним security.W021** (HSTS preload выключен); только settings с примерными hosts/SMTP, внешние HTTPS/SMTP не проверялись |
| `docker compose exec -T web python manage.py collectstatic --noinput` | Exit 0; CSS собран runtime-пользователем |
| Основная development-БД и `environment_probe --marker stage1-persist --check-only` | Exit 0, **0 пользователей, 0 WorkItem**, прежний маркер OK; синтетические сотрудники только в test_kp11 и отдельных projects |
| Compose config --quiet, Python compileall, 20 локальных Markdown-ссылок, git diff --check | Exit 0; SPEC/PERMISSIONS, lock/dependency declarations и Docker/Compose конфигурация не изменены |

125 тестов = 92 прежних и 33 новых: 27 workflow-проверок, 5 TransactionTestCase с независимыми PostgreSQL соединениями и 1 обновление заполненной схемы. Проверены пустой/частичный/полный прогресс, no-op, overdue, чужие ids/прямой POST, read/edit ACL и отзыв роли, revision/epoch, title/due/reorder/delete, подтверждение изменившихся последствий/actor/подпись, конфликт с сохранением ввода, новый/отозванный/восстановленный исполнитель, 3/5/0 статистика, деактивация/реактивация/последний администратор, rollback при сбое аудита, immutable/FK/CHECK, readonly archive и фильтрация истории. Все прежние проверки этапов 1–3 включены в этот запуск.

Пять новых конкурентных сценариев: выполнение против добавления подзадачи; выполнение против отзыва; две отметки с одной версией; два редактирования с одной версией; архивирование против отметки. Допустимые порядки сохраняют согласованность; устаревшее состояние требует конфликта/повторного подтверждения, архив/отзыв блокируют повтор.

## Найденные ошибки и неуспешные запуски этапа 4

- Первый полный прогон: **121 тест, 1 failure + 1 error**, 75.297 секунды. В тестовом helper перед foreign-id проверкой ошибочно искался несуществующий progress; в другом тесте force_login использовал старый User.auth_version после реактивации. Исправлены fixtures, assertions сохранены. Следующий промежуточный прогон **121/121 passed**, 75.251 секунды; он не заменяет финальные 125 тестов.
- Затем **124 теста, 1 failure**, 80.524 секунды; еще один запуск **125 тестов, 1 failure**, 80.284 секунды, был начат со старым образом до исправления. Проверка rollback последнего администратора сравнивала cached WorkItem.version после добавления исполнителя; fixture теперь получает актуальную версию из БД до проверки rollback. Финальный 125/125 прошел с исправлением.
- Реальное обновление заполненной stage3-БД выявило **pending trigger events**: INSERT backfill ставил отложенные FK events, последующий ALTER TABLE в той же миграции PostgreSQL запрещал. 0004 теперь сначала создает ограничения/trigger, затем заполняет прогресс. Повтор на той же заполненной БД и специальный migration test успешны.
- При подготовке дополнительного smoke project неверная переменная порта Mailpit оставила default 8025 и вызвала port conflict. Исправлена на MAILPIT_PORT=18045, startup и финальный smoke успешны. Это ошибка команды подготовки, основной сервис/БД не изменялись.
- Дополнительный restart/up smoke project был выполнен без прежних переменных портов; преждевременное повторное up пересеклось с recreate и завершилось `No such container`, HTTP-check — connection refused. После завершения обоих процессов web создан последовательной командой с исходными портами; повторный реальный HTTP/SMTP и проверки сохраненных данных успешны. Эти неуспешные команды не засчитаны как проверки приложения.
- Часть вывода позднего запуска не сохранилась в контексте; без подтвержденного exit/result он не засчитан. Финальный suite и HTTP smoke повторены с захватом результата в локальные /tmp logs; только их подтвержденные результаты приведены выше.

## Конфигурация среды и ограничения

Через cloud-environment-onboarding:setup сохранен обновленный черновик **start_skill** для принятого этапа 3 и реализованного этапа 4: build/start/migrate/readiness, PostgreSQL tests, изолированные HTTP/SMTP сценарии, legacy upgrade, сохранение .env/volumes и безопасный bootstrap. Ответ сервиса: **status=saved, requires_publish=true**. install_script, repositories, allowlist и secret bindings не менялись. Для использования новых инструкций в будущих задачах проверьте и сохраните изменения в настройках среды, затем опубликуйте среду. Публикация и fresh-task restoration **не выполнены**.

- Этапы 5–7 не реализованы: полный интерфейс/фильтры/mobile browser acceptance, чат, release/backup/restore/production operations.
- Ручной сценарий человеком, браузер/JS engine и 360/1280 px, внешние HTTPS/SMTP/proxy, dump/restore и нагрузка **не проверены**. Автоматический HTTP smoke не заменяет браузерную или production-приемку. W021 не скрыт.
- Обычная сборка без cloud overlay за пределами этой среды и другие CPU/OS не проверены. Основные db/web/Mailpit работают и healthy. Все изолированные smoke projects остановлены без удаления volumes; основная БД не удалялась и не заполнялась тестовыми сотрудниками.

Точные команды запуска/проверок, bootstrap и короткий ручной сценарий этапа 4 — [README.md](README.md). Production-параметры — [DECISIONS.md](DECISIONS.md).

## История этапа 3 (коммит d3854b7)

Ниже сохранены результаты этапа 3 до реализации этапа 4; упоминания отсутствовавшего прогресса относятся к этому историческому состоянию.

## Реализовано в этапе 3

- WorkItem (task/event), Assignment, стабильные UUID и упорядоченные Subtask; event_at отдельно от обязательного due_at. Срок по дате — последний микросекундный момент московского дня, в PostgreSQL хранится UTC. Поля lifecycle/версий предусмотрены в схеме, операции этапа 4 пока отсутствуют.
- AudienceSelection и выбранные пользователи/роли/группы с label_snapshot, AssignmentSource со всеми причинами пересечений. Объединение ИЛИ, одно Assignment на сотрудника, минимум один получатель. Допускаются активные приглашенные до подтверждения почты; неподтвержденный не получает доступ к приложению. Изменения состава/названий после выдачи не пересчитывают назначения и сохраненные источники.
- Серверная форма выдачи, отдельное подписанное превью с ФИО/количеством, подтверждение. Подпись связана с actor, request_key, каноническими полями и полным снимком, действует 600 секунд. Перед выдачей снимок повторно вычисляется под AccessGuard. Изменение/истечение дает 409 с новой формой подтверждения; недоступный выбранный адресат дает ошибку выбора. JS-ids не считаются доверенными.
- Одна транзакция для задания, подзадач, назначений, источников, MutationReceipt и аудита. Повтор ключа с тем же payload возвращает прежний объект без изменения даты назначения; с другим payload — 409. Текущие права проверяются перед receipt; снятая роль не обходится повтором. Завершенный receipt не требует повторного вычисления состава или непросроченного старого превью.
- Только create + view_own + manage_own_assignees разрешают выдачу. Личные назначения доступны любому действующему сотруднику; «Выданные» — свои с view_own, «Все задания» — с view_all. Карточки ищутся через ограниченную выборку. Исполнитель не получает roster; UUID чужой карточки дает 404, запрещенный roster видимого задания — 403. Автор не становится исполнителем автоматически. view_all не дает edit; edit_all не раскрывает новые объекты.
- PostgreSQL UNIQUE/CHECK и индексы; triggers запрещают смену author/kind и identity назначения, изменение/удаление истории источников/receipt, источник другого задания/batch. Runtime имеет только SELECT/INSERT для истории выдачи. Общая синхронизация с изменением ролей/членства остается AccessGuard; выдача журналируется без рабочих текстов и auth-секретов.

## Реально выполненные проверки этапа 3

Linux amd64; Docker 28.4.0/Compose 2.40.3; Python 3.12.15, Django 5.2.18; PostgreSQL 17.11, Mailpit 1.31.4. Все DB/конкурентные проверки использовали PostgreSQL, не SQLite.

| Команда / проверка | Реальный результат |
| --- | --- |
| `BUILDX_CONFIG="$PWD/.cache/buildx" docker compose -f compose.yaml -f compose.cloud.yaml build web` | Exit 0, TLS/lock hashes сохранены; финальный функциональный образ `sha256:9eff1a9985d3eb30c35ca178953776e533ca57883a835057bf410167007b4bad` |
| `docker compose run --rm migrate`, `docker compose up -d --wait web` | Обе новые миграции применены к существующей БД; повтор migrate: No migrations to apply, grants успешны; web healthy |
| Startup/migrations на пустой БД `kp11-stage3-smoke-20261010` | Все миграции с нуля и runtime grants успешны, реальный web/SMTP доступны |
| `docker compose run --rm tests` после исправления | **92 tests, 92 passed, 0 failed, 0 skipped**, 51.284 секунды. Fresh test_kp11 создана миграциями, удалена после suite |
| `docker compose exec -T web python manage.py check --database default` | Exit 0, no issues (0 silenced) |
| `docker compose run --rm migrate python manage.py makemigrations --check --dry-run` | Exit 0, No changes detected; host makemigrations сначала предупреждал, что hostname db доступен только в Compose, поэтому итоговая проверка выполнена внутри контейнера |
| `docker compose exec -T web python -m pip check` | Exit 0, No broken requirements found |
| `python3 scripts/smoke.py` | Exit 0: реальный redirect/login, HTML/CSS, no-store, private health/DB и новое SMTP-письмо в Mailpit |
| `python3 scripts/smoke_workitems.py --project kp11-stage3-smoke-20261010 --web-port 18000 --mailpit-port 18025` | Exit 0: четыре активации, три выдачи, OR/dedup и пять причин, повтор/снимок/409, отдельные даты, own/all/roster права, отзыв роли автора и отсутствие использованных auth-секретов в web logs |
| `scripts/smoke_accounts.py` в пустом `kp11-stage2-smoke-stage3-regression`, порты 18010/18035 | Exit 0. Сценарий этапа 2 повторно прошел после выделения общего HTTP-клиента: bootstrap/activation/login, CSRF/права, отзыв роли/сессий, recovery и безопасные логи |
| Restart db/web в отдельном stage3 smoke project + повторный smoke.py | Exit 0. После restart сохранены 3 WorkItem, 4 Assignment, 8 AssignmentSource, 3 Receipt и 3 записи workitems.issued; HTTP/readiness/SMTP снова успешны |
| EXPLAIN личных/выданных/всех выборок на smoke-БД | Выполнен; личные/выданные используют индексы, all — Seq Scan на маленькой выборке. Это не нагрузочная проверка. Проверены runtime privileges: SELECT/INSERT разрешены, UPDATE/DELETE запрещены для всех шести таблиц истории выдачи |
| `docker compose exec -T web python scripts/check_production.py` | Exit 0 **с одним security.W021** (HSTS preload выключен). Проверены settings с примерными hosts/SMTP; внешние HTTPS/SMTP не проверялись |
| `docker compose exec -T web python manage.py collectstatic --noinput` | Exit 0; CSS собран runtime-пользователем |
| Основная development-БД и `environment_probe --marker stage1-persist --check-only` | **0 пользователей, 0 WorkItem**, прежний маркер OK. Все синтетические сотрудники/выдачи только в test_kp11 или отдельных smoke projects |
| Compose config --quiet, Python compileall, 20 локальных Markdown-ссылок, git diff --check | Exit 0; найденный лишний пустой конец HTTP helper исправлен |

92 теста = 62 прежних и 30 новых. Новые проверки: OR/dedup/все источники, active/invited eligibility, пустая выдача, snapshot после membership, reconfirm при новом члене/ФИО/истечении, подпись/actor/key/content, повтор и другой payload, полный rollback, текущие права при повторе, object selectors/roster/email privacy, CSRF/GET, UTC/Москва, независимое event_at, SQL immutable/unique/check/cross-item source. У task chat-поля запрещены CHECK и chat endpoint отсутствует (404); EventMessage пока не существует, его trigger будет этапом 6.

Пять новых TransactionTestCase с независимыми PostgreSQL соединениями: два одинаковых ключа дают один объект/аудит; разные payload одного ключа — один успех и один конфликт; выдача против изменения membership; выдача против снятия роли автора; два одновременных HTTP POST подтверждения с enforce_csrf_checks=True возвращают одну Location. Оба допустимых порядка конкурентных изменений сохраняют согласованное состояние.

## Найденные ошибки этапа 3

Первый полный прогон: **92 теста, 1 failure**, 49.960 секунды. Старый тест входа обнаружил изменение текста пустого списка («Заданий» вместо «Задач»). Вернут прежний текст, assertion не менялся. Следующий полный прогон: 92/92 passed. Неудачный прогон не помечен успешным. До контейнерной проверки генерация миграции на host сообщала о недоступном Compose hostname; внутри контейнера миграции и проверка отсутствия расхождений прошли.

## История этапа 2 (коммит f34e6b1)

Ниже сохранены результаты предыдущего этапа; они не заменяют текущие результаты этапа 4.

## Реально выполненные проверки этапа 2

Linux amd64; Docker 28.4.0/Compose 2.40.3; Python 3.12.15, Django 5.2.18, psycopg 3.3.6, Gunicorn 26.2.0; PostgreSQL 17.11 и Mailpit 1.31.4. Lockfile и digest образов не менялись. SQLite не использовался.

| Команда / проверка | Реальный результат |
| --- | --- |
| `BUILDX_CONFIG="$PWD/.cache/buildx" docker compose -f compose.yaml -f compose.cloud.yaml build web` | Exit 0; закрепленные зависимости из валидного cache, TLS и lock hashes сохранены |
| `docker compose run --rm migrate`, `docker compose up -d --wait web` | Новые миграции и runtime grants применены к существующей development-БД; web healthy |
| Startup/migrations на новых volumes отдельных `kp11-stage2-smoke-*` projects | Все миграции с нуля успешны; SMTP и настоящий HTTP работают. Повтор migrate основной БД: `No migrations to apply`, повторная выдача прав успешна |
| `docker compose run --rm tests` на финальном коде | **62 tests, 62 passed, 0 failed, 0 skipped**, 24.936 секунды; свежая test_kp11 создана миграциями и удалена после suite |
| `docker compose exec -T web python manage.py check --database default` | Exit 0, no issues (0 silenced) |
| `docker compose run --rm migrate python manage.py makemigrations --check --dry-run` | Exit 0, No changes detected |
| `docker compose exec -T web python -m pip check` | Exit 0, No broken requirements found |
| `python3 scripts/smoke.py` | Exit 0: root → настоящий login, русский HTML/CSS, no-store, закрытый health и DB readiness; новое SMTP-письмо подтверждено Mailpit API |
| `scripts/smoke_accounts.py` на отдельных пустых projects с отдельными портами | Bootstrap → письмо → активация/вход; приглашены второй администратор и сотрудник, использованы CSRF-формы; повторная ссылка отклонена; прямой POST с действительным CSRF запрещен обычному сотруднику; снятие роли/отключение действуют на открытые сессии; reset отзывает обе старые сессии. Использованные пароли/токены/auth URL отсутствуют в stdout/stderr web logs |
| `docker compose exec -T web python scripts/check_production.py` | Exit 0 **с одним предупреждением security.W021** (HSTS preload выключен). Это проверка settings с примерными hosts/SMTP, без внешнего SMTP/HTTPS |
| `docker compose exec -T web python manage.py collectstatic --noinput` | Exit 0; CSS собран runtime-пользователем |
| SQL/ORM проверки обычной development-БД | **0 сотрудников**, 4 роли, 12 разрешений, 3 группы; bootstrap в обычной БД не выполнялся. Старый `environment_probe --marker stage1-persist --check-only` по-прежнему дает OK |
| Compose config --quiet, Python compileall, 20 локальных Markdown-ссылок, git diff --check | Exit 0. SPEC/PERMISSIONS и dependency lock не изменены |

62 теста включают 28 актуализированных проверок каркаса и 34 проверки этапа 2. Проверены нормализация/дубликаты/чужой домен, неподтвержденный аккаунт, собственный валидный пароль, просроченные/отозванные/использованные ссылки, безопасный bootstrap/resend, SMTP failure, union permissions/отсутствие прав от membership, запреты POST/services и изменения защищенного каталога/identity, generic recovery, отзыв сессий/прав, CSRF и no-store, version conflicts с сохранением ввода и аудит.

Через TransactionTestCase и отдельные PostgreSQL соединения проверены: ровно одна активация из двух POST и ровно один reset из двух POST (два Django Client с enforce_csrf_checks=True); resend против activation; два отключения администраторов; два снятия системной роли с пользователей; отключение системной роли против отключения администратора; не более пяти разрешенных операций из десяти запросов общей квоты; SMTP callback не удерживает guard; shared read блокирует write до commit. Проверка отзыва прав между проверкой ошибочного POST и рендером не позволяет показать профиль.

## Ошибки, найденные и исправленные

При повторной серии из 61 теста обнаружена **1 ошибка**: навигационный context processor обращался к request.user, когда неверный Host отклонялся до AuthenticationMiddleware. Добавлена обработка раннего запроса без user; тест Host не ослаблен. Следующая серия: 61/61 passed. После дополнительной защиты рендера ошибочных POST и ее regression-теста итоговая серия: 62/62 passed. Неудачная серия не выдается за успешную.

Ошибка SMTP моделировалась тестом: приглашение остается в БД, доставка отмечается неподтвержденной, resend создает новую ссылку и отзывает прежнюю. Это проверка отказа, а не утверждение о доступности внешнего SMTP.

## Ранее подтвержденный этап 1

Принятый этап 1 включал чистую установку с TLS/hashes, PostgreSQL runtime/migration separation, 28/28 foundation tests, HTTP/SMTP, persistence после restart db/web, отсутствие секретов .env/CA и owner/migrator credentials в web, запуск от UID 10001. Локальный временный Gunicorn с двумя workers проверял redirect/HTTPS headers с эмуляцией proxy; строгий production check возвращал 1 из-за W021. Эти исторические результаты сохранены в коммите 34b12bb; они не заменяют production-приемку или проверку новых auth-функций через внешний HTTPS.
