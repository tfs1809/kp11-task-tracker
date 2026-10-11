# План перехода backend на FastAPI

Дата: 11.10.2026, Москва. Ветка: `feat/fastapi-migration`, база: `abb3577` из `origin/main`.

Статус: **планирование**. Сейчас приложение работает на Django. В этой ветке на текущем шаге создаются план и ссылки на него; реализация и проверки FastAPI еще не выполнялись. Существующие результаты в [STATUS.md](STATUS.md) относятся к Django.

## 1. Цель и границы

Перенести обработку HTTP на FastAPI, сохранив бизнес-правила [SPEC.md](SPEC.md), принятую [матрицу прав](PERMISSIONS.md), рабочие данные и браузерный интерфейс. Новая команда пользователя заменяет выбор HTTP-фреймворка из SPEC A; остальные требования остаются в силе. Выполненный [PLAN.md](PLAN.md) остается историей этапов Django, этап 7 развертывания не входит в этот перенос.

Для сохранения миграций принимается **FastAPI + существующий Django ORM**. Django остается библиотекой для моделей, транзакций, миграций, проверенных механизмов паролей/подписей/сессий, SMTP и шаблонов. Все пользовательские HTTP-обработчики в результате принадлежат FastAPI. Это решение сохраняет текущую схему и не требует переписывать ORM одновременно с транспортом.

Серверный русский HTML, CSS и небольшой JS сохраняются. Не вводятся отдельный frontend, JWT, Redis, очереди, новая база или новый API для внешних клиентов. Существующие HTML-формы и chat JSON endpoints сохраняют свой контракт.

## 2. Что остается без изменений

| Область | Сохраняемый контракт |
| --- | --- |
| Контейнеры | Dockerfile, закрепленный Python image/digest, runtime UID 10001, порт 8000, текущая команда Gunicorn WSGI |
| Compose | compose.yaml / compose.cloud.yaml, web/db/mailpit/migrate/tests, команды запуска и healthcheck, volumes, сети, порты, BuildKit CA/TLS |
| База | PostgreSQL 17.11, все таблицы/колонки/типы, UUID и sequences, индексы, CHECK/UNIQUE/FK, функции и триггеры |
| Миграции | Все apps/*/migrations, их зависимости и история django_migrations; Django migration executor; существующие data migrations |
| DB-права | owner/migrator/runtime, docker/postgres/init.sh, prepare_database и grant_runtime_access; web без DDL и миграционных credentials |
| Конфигурация | .env/.env.example, имена переменных, DJANGO_SETTINGS_MODULE и существующие settings, фиксированный PUBLIC_BASE_URL, настройки SMTP и secret bindings |
| Утилиты | manage.py и команды bootstrap/migrate/tests/collectstatic, init_dev_env.py, check_health.py, существующие smoke/browser точки запуска |
| Авторизация и данные | Существующие password hashes, cookies/DB sessions, auth_version, подписанные ссылки/preview, receipts, аудит и rate-limit buckets |

В этом переносе не добавлять миграции, Alembic, schema auto-create, альтернативное хранилище или новую модель таблиц. Models остаются источником структуры; `makemigrations --check --dry-run` должен оставаться чистым. Migration/DB-grant команды продолжают работать до запуска HTTP-приложения, без запуска ASGI event loop.

Добавление FastAPI и необходимых библиотек в requirements.in / requirements.lock — изменение **зависимостей приложения** при реализации M1. Существующие версии Django/psycopg/Gunicorn и закрепленные базовые образы сохраняются. Новые совместимые версии выбираются по официальным источникам и фиксируются с hashes; сейчас версии не выбраны и зависимости не установлены. Browser lock остается прежним.

## 3. Как сохранить текущий запуск

FastAPI — ASGI-приложение, текущие Docker/Gunicorn/runserver entrypoints — WSGI. Планируемый вход:

```text
Существующий web / Gunicorn / manage.py runserver
                  │ WSGI
          config.wsgi:application
                  │ ASGI → WSGI adapter
             FastAPI routers
                  │
        services / selectors / policies
                  │
        Django ORM → PostgreSQL
```

Сохранить имя `config.wsgi:application`, settings.WSGI_APPLICATION и инфраструктурные команды. В коде приложения создать FastAPI factory и обернуть ее `a2wsgi.ASGIMiddleware`; это кандидат для проверки M1. Django initialize вызывается один раз на процесс до загрузки моделей. Штатная раздача static через development runserver и collectstatic также должна пройти проверку при неизменном Compose.

Официальный [README a2wsgi](https://github.com/abersheeran/a2wsgi#asgimiddleware) описывает преобразование ASGI в WSGI. Это подтверждает назначение библиотеки, **не** совместимость конкретного приложения. Проверить lifecycle, отдельный event loop каждого Gunicorn worker, завершение/таймауты, корректную передачу заголовков/cookies, несколько requests и остановку процесса. Не полагаться на FastAPI lifespan для инициализации, которую WSGI adapter может не вызвать; для текущего синхронного ORM/SMTP использовать явную factory initialization и проверить закрытие ресурсов.

M1 — обязательная техническая проверка до переноса бизнес-функций. Если адаптер не позволяет сохранить безопасность, транзакции или имеющийся запуск, остановить этот вариант и сообщить конкретный воспроизводимый блокер. Изменение Compose/Docker-команд на ASGI runtime не входит в данный план и не может быть сделано молча. WebSocket и ASGI streaming не нужны по SPEC.

## 4. Слои и совместимость

| Слой | План изменения |
| --- | --- |
| HTTP | Новые FastAPI APIRouter для core/accounts/access/audiences/workitems/discussions; FastAPI Request/Response и явные handlers ошибок |
| Ввод | Transport schemas и парсеры query/forms, безопасное отображение ошибок; Django Forms/formsets можно сохранить для HTML-валидации и рендера |
| Контекст запроса | Ограниченный adapter для session/auth/CSRF/шаблонов; method, path, headers, client IP, QueryDict с getlist, route name; не отдавать HTTP Request в новые business APIs |
| services/selectors/policies | Повторно использовать текущие правила, ORM и блокировки. Request-coupled accounts login/logout/password change выделить через явный session/auth adapter, сохранив операции и аудит |
| Представление | Django Templates/autoescape, текущие формы, контекст навигации, {% url %}, {% csrf_token %}, static URL; совместимый reverse/name registry |
| Состояние | Существующие models, Django ORM, PostgreSQL и неизменные миграции |
| Проверки | Business tests на Django/PostgreSQL; HTTP cases направить на FastAPI; внешние smoke/browser scripts сохранить как приемочные контракты |

Не подменять ORM-коннекты асинхронным драйвером. Django transaction/connection привязаны к потоку: **авторизация, AccessGuard, выборки, полная сериализация/рендер одного защищенного чтения выполняются в одном синхронном worker**. Для POST предварительный read guard завершается перед отдельным write service; повторная проверка actor/epoch остается в транзакции изменения. На ошибке/показе формы доступ проверяется снова под read guard.

Нельзя открыть transaction/guard в FastAPI yield dependency, а выполнить endpoint/закрытие в другом потоке. Async endpoint может разобрать ограниченный ввод и вызвать один синхронный обработчик, который полностью управляет ORM request lifecycle, guard и response bytes. Нельзя возвращать наружу lazy QuerySet, ленивого пользователя или TemplateResponse с поздним обращением к БД. Соединения очищаются в том же worker через существующий Django lifecycle/close_old_connections, включая исключения.

Проверенные механизмы SessionStore, hashing/signing/password validators и CSRF переиспользуются через adapter. Не писать новый алгоритм CSRF/криптографии. При конвертации ответа сохранить все Set-Cookie, Vary и остальные заголовки. Session/CSRF cookies должны иметь прежние имена, область и флаги; login/logout/смена пароля сохраняют ротацию и session hash.

## 5. HTTP и безопасность: обязательный контракт

- Сохранить пути, имена маршрутов, trailing slash, GET/POST, field names, повторяющиеся checkbox values, UUID converters, before_id/changes_after/visible, content types и redirect semantics. Существующие `url` tags и request.resolver_match для навигации получают совместимый registry/context без вызова старых views.
- Статусы: неверный ввод 400, отсутствие сессии redirect на вход для HTML / 401 для polling, видимое запрещенное действие 403, недоступный объект 404, конфликт 409, quota 429 с Retry-After, метод 405. Private health скрывается 404 без ключа; readiness проверяет БД и guard, дает 503 при отказе.
- Не выдавать стандартный FastAPI validation payload с input/password/token и случайным 422 вместо текущего контракта. Exception handlers возвращают безопасные русские ошибки; сохраняется безопасный ввод форм. Пароли, auth-токены и traceback не отражаются в validation/log responses.
- Cookie authentication на каждом защищенном запросе проверяет active/verified/auth_version/session hash и текущие права под guard. DB sessions, в том числе созданные Django до переключения, продолжают работать; recovery/deactivate немедленно отзывают их.
- CSRF обязателен для всех изменяющих routes, включая login, logout, administration, progress и чат. Сохранить masked token/cookie, Origin/Referer validation, strict-origin, Trusted Origins. Ошибки CSRF не обходятся через legacy fallback.
- Сохранить проверку Host/HTTPS, no-store, HttpOnly/Secure/SameSite, X-Frame-Options, nosniff и production HSTS/settings. REMOTE_ADDR остается источником rate-limit IP; произвольный X-Forwarded-For не доверяется. SMTP-ссылки строятся из PUBLIC_BASE_URL.
- AccessGuard, версии, подписанные preview, MutationReceipt и quotas остаются общими для всех workers. Аудит и изменение атомарны, SMTP вызывается после commit. Отказ аудита откатывает действие.
- Roles и audiences не смешиваются; останутся запреты последнего администратора, чужого прогресса и архивных изменений. Скрытый текст чата не попадает в обычные HTML/JSON/аудит/лог, включая retry и default exception handlers.
- Access/error logs FastAPI/Starlette/adapter также проходят whitelist/redaction; `/docs`, `/redoc`, `/openapi.json` выключены по умолчанию, чтобы не появлялись новые публичные endpoints.

На переходных этапах явно разделить владельцев путей между FastAPI и прежним Django WSGI. Перенесенный путь обслуживает FastAPI для **всех** методов; его 400/401/403/404/405 не вызывают fallback. Передача к старому backend допускается только для еще не перенесенного пути. Сессии/CSRF совместимы в обе стороны. В финале fallback и исполняемые Django views/middleware для пользовательского HTTP удаляются; Django URL registry может остаться только для reverse шаблонов.

## 6. Последовательные этапы

| Этап | Работа | Критерий завершения |
| --- | --- | --- |
| M0. План — текущий шаг | Отдельная ветка, инвентаризация Django-зависимостей, ограничения и данный документ | Только документация; база ветки и неизменность инфраструктуры/миграций проверены |
| M1. Запуск и транспорт | Выбрать/закрепить FastAPI/adapter dependencies. Factory + неизменный WSGI entrypoint, ограниченный bridge request/response, health и статические ресурсы; явная таблица владения путями | Штатные build/up/migrate/health/collectstatic работают без изменения infra; проверены Gunicorn два workers и dev runserver, cookies/headers/lifecycle/DB cleanup. Новые endpoints действительно проходят через FastAPI, подтверждено router tests |
| M2. Авторизация и общая защита | Session/CSRF/auth/settings/error/log adapters; accounts login/logout/activation/recovery/password и русский HTML. Перенести request-coupled auth operations через session port | Старые hashes/сессии/активационные ссылки работают; новый пароль/rotation/session hash, недействительный токен, CSRF, rate limits, malformed Host/Origin, JSON 401 и безопасные ошибки проверены на новом backend |
| M3. Администрирование и чтения | Сотрудники, роли, группы, bootstrap compatibility, общий security pipeline; личные/выданные/all/archive списки, фильтры, карточки, roster/история | Действуют текущие permissions и отрицательные прямые запросы, последний администратор и конкурентные изменения защищены; фильтры не расширяют scope, статистика/чужой прогресс не утекли |
| M4. Операции задания | Выдача/preview/confirm, marks, completion/return, edit/reset, add/restore/revoke, archive; сохранить текущие service transactions | Повторы/версии/пересчет preview и атомарные откаты проходят; revoke/deactivate блокируют следующий запрос; заполненные данные этапов 1–6 читаются/изменяются без миграции |
| M5. Чат | HTML/history/polling/send/close/hide/original на FastAPI, прежний JS/JSON контракт | Pagination и скрытие старого сообщения не теряются; current groups, completion/revoke, XSS/plain text, hidden-original, readonly, replay, per-event/total quotas и два процесса проходят |
| M6. Полное переключение и приемка | Все пользовательские HTTP routes принадлежат FastAPI; удалить fallback/legacy HTTP entrypoints, обновить актуальную архитектуру/README/STATUS, проверить переключение на заполненной БД и обратно | Все business/HTTP/concurrency/browser checks прошли через нужный backend, frozen-file/schema checks чистые; runtime permissions/health/SMTP/static подтверждены, документирован rollback к abb3577 без изменения БД |

Каждый этап заканчивается отдельным проверяемым результатом, коммитом и записью в STATUS с фактическими командами/числами/ограничениями. Следующий этап не начинается с неисследованной ошибкой предыдущего. До завершения M6 приложение не объявляется полностью переведенным на FastAPI.

## 7. План проверок

**Исходная регрессия:** опубликованный abb3577 имеет 162 прошедших PostgreSQL tests, 8 browser chat cases и 8 browser regression cases на 360/1280 px; это результаты прежнего Django запуска, не FastAPI. На M1 зафиксировать повторяемый baseline в новой изолированной БД.

**Направление HTTP tests:** Django test.Client по умолчанию вызывает Django handler и не доказывает работу FastAPI. Перевести HTTP fixtures на новый app/WSGI entrypoint или ASGI client с существующим session форматом; явно проверить владельца маршрута. Набор business/service TestCase оставить синхронным. HTTP tests с отдельным event-loop/thread требуют committed PostgreSQL fixtures (TransactionTestCase или отдельная test DB): транзакция TestCase в одном потоке не видна другому. Production WSGI path дополнительно проверять реальными внешними HTTP smoke.

**Совместимость заполненной БД:** создать синтетический baseline с назначениями/marks/completion/revocations, archived/closed событиями, visible/hidden сообщениями, receipts, аудитом, sessions и использованными/живыми auth grants. Сохранить контрольные количества/ids/состояния и signatures schema/migration files. Запустить FastAPI с той же БД, прочитать данные и выполнить поддерживаемые операции; затем загрузить прежний abb3577 на той же БД и проверить чтение и легальность добавленных записей. Не выполнять destructive reset/fake migrations.

**Конкурентность:** все сценарии на PostgreSQL с независимыми соединениями; добавляется проверка thread/connection ownership нового HTTP bridge. Покрыть send/revoke, complete/revoke/edit, idempotency same/different input, последний администратор, activation одноразовой ссылки, shared quota разных процессов и rollback при отказе аудита.

**Браузер:** прежние сценарии stages 5/6 с реальным Mailpit, CSRF и отдельными cookie contexts на 360/1280 px; keyboard/focus/labels/contrast, сохранение ошибочного ввода, no-JS формы и чат. Проверки продолжают использовать новые пустые изолированные Compose projects, а не основную DB.

Существующие инфраструктурные команды должны сохраниться:

```bash
python3 scripts/init_dev_env.py
BUILDX_CONFIG="$PWD/.cache/buildx" docker compose -f compose.yaml -f compose.cloud.yaml build web
docker compose up -d --wait db mailpit
docker compose run --rm migrate
docker compose up -d --wait web
docker compose exec -T web python manage.py check --database default
docker compose run --rm migrate python manage.py makemigrations --check --dry-run
docker compose exec -T web python -m pip check
docker compose run --rm tests
docker compose exec -T web python manage.py collectstatic --noinput
python3 scripts/smoke.py
docker compose exec -T web python scripts/check_production.py
```

Accounts/workitems/workflow HTTP scripts и browser_stage5.py/browser_stage6.py/smoke_chat_workers.py запускаются по [README.md](README.md) в отдельных projects. Добавленные adapter/HTTP tests включаются в ту же tests-команду через Python test runner; Compose не получает новый service. Сохранять exit status, актуальный run_id, passed/failed/skipped/unrun отдельно; численность будущих тестов определяется после их переноса.

Для frozen files на каждом этапе сравнивать с базой:

```bash
git diff --exit-code abb3577 -- Dockerfile 'compose*.yaml' docker .env.example scripts/init_dev_env.py scripts/check_health.py manage.py 'apps/*/migrations/*'
git diff --check
```

Файлового diff недостаточно для БД: сверить django_migrations и schema-only представление constraints/indexes/triggers/functions до/после переключения в изолированной БД. Внешний production HTTPS/SMTP/proxy и полноценный backup/restore остаются отдельной приемкой этапа 7, не выдаются за проверенные этим переносом.

## 8. Допущения и точки остановки

- Перенос означает новый HTTP/backend layer на FastAPI с сохраненным ORM/миграционным инструментом Django. Полное удаление Django-зависимости потребовало бы другого объема проектирования; текущий план на него не опирается.
- Запуск, инфраструктурные файлы и схема заморожены; ASGI→WSGI adapter подлежит реальной проверке M1. Ограничения по lifecycle/потокам/cookies должны быть доказаны до дальнейшего переноса.
- UI/URLs/session/signatures остаются совместимыми; потеря авторизации, новая schema migration, замена session на JWT или изменение transport errors считаются регрессиями.
- Не устанавливать новые packages/не менять code в рамках M0. Для начала реализации нужен следующий запрос пользователя с выбранным этапом плана. Блокирующих вопросов для подготовки этого плана нет.

## 9. Результат текущего шага

Созданы ветка от опубликованного abb3577 и этот план; проверены точки связи с Django ORM/transactions, auth/session/CSRF, Forms/templates, middleware и WSGI запуском. Проверка назначения ASGIMiddleware по README — документальная. FastAPI, adapter, перенос бизнес-операций и его тесты **не запускались**. Реальные проверки документации и frozen files записываются в STATUS.
