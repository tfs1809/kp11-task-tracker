# Состояние проекта

Обновлено: 9 октября 2026, Europe/Moscow.

## Выполнено

Этап 0 принят пользователем. Исходный SPEC сохранен без изменений, целевая архитектура/матрица прав/план и допущения документированы. По отдельной команде пользователя **этап 1 выполнен**. Этапы 2–7 не начаты.

Реализовано:

- Django-проект с шестью предметными apps и техническим core; dev/test/migration/production settings, русский базовый layout и ошибки, CSS, закрытая страница основного раздела. Вход пока честная информационная страница, без тестового пароля или обхода авторизации.
- Кастомный User до первой миграции (корпоративная нормализованная почта, уникальность и CHECK), AccessGuard с seed-строкой, AuditEntry с FK/индексами и append-only DB-trigger. Другие предметные модели еще не реализованы.
- Каркас policy/selector/service: deny-by-default, текущий пользователь/epoch сессии, shared/exclusive guard, проверка write-контекста аудита и запрет повышения shared lock. Универсальный Django Admin CRUD отсутствует.
- requirements.in/полный requirements.lock с хешами, Dockerfile (непривилегированный runtime), Compose web/db/Mailpit и явные migrate/tests, PostgreSQL/Mailpit named volumes, private health/readiness, .env.example и генератор локальных development-секретов.
- Разделение bootstrap/migration/runtime DB-учетных записей, повторяемая выдача минимальных DML-прав, отсутствие migration/bootstrap-паролей в окружении web.
- 28 инфраструктурных тестов на PostgreSQL; реальные HTTP/SMTP smoke и команды ручной проверки в README.

## Реально выполненные проверки

Среда: Linux amd64, Docker Engine 28.4.0, Compose 2.40.3. Приложение: Python 3.12.15, Django 5.2.18, psycopg 3.3.6, Gunicorn 26.2.0; PostgreSQL 17.11, Mailpit 1.31.4. Теги и digest закреплены. Начальная установка зависимостей внутри образа прошла с TLS и `--require-hashes`; host-venv тоже установлен по lock с хешами, но поддерживаемый запуск — Compose.

| Проверка / реально запущенная команда | Результат |
| --- | --- |
| `BUILDX_CONFIG="$PWD/.cache/buildx" docker compose -f compose.yaml -f compose.cloud.yaml build web` | Установка всех 7 lock-пакетов и сборка успешны; последующие сборки использовали валидный cache. TLS/hashes не отключались |
| `docker compose up -d --wait db mailpit`, `docker compose run --rm migrate`, `docker compose up -d --wait web` | Начальная схема создана, runtime-права выданы, настоящий HTTP-сервер запущен; ready выполняет запрос к PostgreSQL и проверяет seed guard |
| Те же startup/migration-команды в отдельном `-p kp11-stage1-clean` с новыми volumes и портами 18000/18025 | Успешная чистая инициализация ролей и всех миграций; настоящий HTML/CSS и закрытый health проверены HTTP-запросами. После проверки этот вспомогательный проект остановлен, volumes сохранены |
| Повторный `docker compose run --rm migrate` | `No migrations to apply`; повторное применение runtime grants успешно |
| `docker compose exec -T web python manage.py check --database default` | Exit 0, `no issues (0 silenced)` |
| `docker compose run --rm migrate python manage.py makemigrations --check --dry-run` | Exit 0, `No changes detected` |
| `docker compose exec -T web python -m pip check` | Exit 0, `No broken requirements found` |
| `docker compose run --rm tests` на финальном коде | **28 тестов, 28 passed, 0 failed, 0 skipped**, 4.361 секунды; свежая test_kp11 создана миграциями и удалена после тестов |
| `python3 scripts/smoke.py` | Exit 0: root → login, русский HTML, CSS 200, no-store, health без ключа 404, private DB readiness 200; отправлено новое письмо с уникальным маркером и проверены его Subject/Text через Mailpit API |
| `environment_probe --marker stage1-persist`, `docker compose restart db web`, ожидание healthy и `environment_probe --marker stage1-persist --check-only` | Exit 0: та же запись существует после перезапуска БД/web и после пересоздания web с новым образом; это проверка named-volume persistence в текущей машине |
| `docker compose exec -T web python manage.py collectstatic --noinput` | Exit 0, 1 CSS-файл собран непривилегированным пользователем |
| `docker compose exec -T web python scripts/check_production.py` | Exit 0 с **одним явным предупреждением security.W021** о выключенном HSTS preload. Скрипт использует примерные hosts/SMTP только для проверки settings; к внешним SMTP/HTTPS не подключается |
| Прямая строгая проверка production `check --deploy --fail-level WARNING` | Exit 1 только из-за W021. Диагностирована как advisory, не скрыта. Проверка с `--fail-level ERROR` дает exit 0 с тем же явным предупреждением |
| Отдельный временный Gunicorn (2 workers) с production settings и примерными hosts/SMTP | Настоящий HTTP: 301 на HTTPS, русский login HTML через эмуляцию доверенного proxy HTTPS-header, HSTS-header. Это не проверка внешнего TLS/SMTP; контейнер после проверки остановлен |
| Runtime SQL/version/окружение и содержимое образа | Подключение к kp11 как kp11_app, сервер 17.11; UID 10001; owner/migrator-паролей в web нет; .env и временный build CA в образе отсутствуют |
| Повтор `init_dev_env.py`, Compose config --quiet, Python syntax, локальные Markdown-ссылки и git diff --check | Успешно; существующий .env не перезаписан, .env/.venv/.cache игнорируются Git |

Тесты каркаса покрыли constraints почты, отсутствие функционального входа/CRUD, базовую CSRF-защиту и Host, отказ неактивному/неподтвержденному пользователю, отзыв epoch уже существующей тестовой сессии, собственный profile selector, health с моделируемым DB-сбоем, атомарный откат и неизменяемость аудита, PostgreSQL runtime grants и конкурентную блокировку записи чтением. HTTP session-тесты создают сессию доверенным test runner, не открытым маршрутом приложения. Это **не** проверки еще не реализованных заданий, ролей, приглашений или восстановления пароля.

## Исправленные проблемы

1. Buildx пытался писать в read-only home: его cache вынесен в `.cache/buildx` через BUILDX_CONFIG.
2. Python-образ не доверял сертификату cloud TLS proxy: `compose.cloud.yaml` подает доверенный системный CA как временный BuildKit secret. Сборка переисполнена с TLS verification и lock hashes, без trusted-host/отключения проверки.
3. Init-скрипт PostgreSQL первоначально имел права 0600 и не запускался postgres-пользователем. Исправлено на 0755, созданы роли в начальной пустой базе, затем отдельно проверена полностью новая база/volumes.
4. Зафиксированы запрет записи аудита без write guard и запрет shared→exclusive lock upgrade. Host проверяется даже для публичных views, строящих только относительные URL; сценарии добавлены в финальные тесты.

## Сохраненная конфигурация среды

Через cloud-environment-onboarding сохранен подтвержденный черновик `install_script` (init development env и Docker build с доверенным CA) и `start_skill` (существующий checkout, build/start/migrate/HTTP+SMTP readiness, проверки и ограничения). Repository membership, network allowlist и внешние secret bindings не изменялись. Сохранение черновика не применяет настройки и не публикует snapshot. Для будущих cloud-задач пользователь должен проверить/сохранить изменения в настройках среды и опубликовать среду. Публикация и восстановление в новой cloud-задаче не выполнялись и не проверены; сохранность Docker images/volumes через snapshot не предполагается.

## Ограничения и следующий шаг

- Функционального входа, bootstrap администратора, приглашений/ролей/групп, задач/событий/прогресса и чата еще нет — это этапы 2–6. В обычной development-БД реальные/тестовые сотрудники не создавались; сохранен только несекретный системный persistence-маркер.
- Проверены production **settings и локальный Gunicorn**, а не production-развертывание. Внешний HTTPS, реальный SMTP, proxy/static routing, backup/restore, браузерные mobile/desktop-сценарии и нагрузка не проверены. Они относятся к этапам 5–7. W021 остается объясненным ограничением до решения по production-домену; предупреждения не silenced.
- Обычная Docker-сборка без cloud CA overlay за пределами этой среды, другие CPU/OS и fresh-task restoration не проверены. Текущие web/db/Mailpit работают; вспомогательные процессы проверки остановлены.

Точные команды и короткий ручной сценарий — [README.md](README.md). Следующий этап 2 выполняется только по отдельной команде пользователя; production-параметры P1–P4 — [DECISIONS.md](DECISIONS.md).
