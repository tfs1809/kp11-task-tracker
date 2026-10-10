# Трекер задач колледжа

Внутренний веб-сервис для сотрудников колледжа. Завершен этап 1: Django-каркас, PostgreSQL, Mailpit, начальные миграции, настройки и проверки инфраструктуры. Приглашение/вход, роли, выдача задач и чат еще не реализованы. Страница входа прямо сообщает об этом; тестового обхода авторизации нет.

## Запуск development

Нужны Docker Engine с Compose v2 и Python 3 для генерации локального `.env`. Проверено на Linux amd64, Docker 28.4.0/Compose 2.40.3. Python приложения находится в закрепленном Docker-образе; Node, локальный PostgreSQL и активация host-venv не нужны.

Из каталога репозитория:

```bash
python3 scripts/init_dev_env.py
docker compose build web
docker compose up -d --wait db mailpit
docker compose run --rm migrate
docker compose up -d --wait web
python3 scripts/smoke.py
```

`.env` создается со случайными локальными development-секретами и правами 0600, не выводится и не перезаписывается. Если уже существует свой `.env`, заполните пустые значения из `.env.example` самостоятельно. Не храните этот файл в Git. Compose не читает значения из `.env.example` автоматически.

**В текущей cloud-среде** home недоступен для записи Buildx, а TLS proxy требует системный CA. Вместо обычной команды сборки выполните:

```bash
export BUILDX_CONFIG="$PWD/.cache/buildx"
docker compose -f compose.yaml -f compose.cloud.yaml build web
```

Остальные команды те же. Overlay использует `/etc/ssl/certs/ca-certificates.crt` как временный BuildKit secret только при установке зависимостей; CA не копируется в образ и репозиторий. TLS и `--require-hashes` остаются включены. Обычная сборка без overlay в среде без этого proxy здесь не проверялась.

Web доступен локально на порту 8000, Mailpit — 8025; обе привязки только к `127.0.0.1`. PostgreSQL не публикует порт. Порты меняются через `WEB_PORT`/`MAILPIT_PORT` в `.env`; передайте соответствующие `--web-port`/`--mailpit-port` в smoke.py. Это локальные адреса для ручной проверки, не опубликованный cloud-preview.

Остановка и повторный запуск:

```bash
docker compose down
docker compose up -d --wait db mailpit
docker compose run --rm migrate
docker compose up -d --wait web
```

`down` сохраняет named volumes. Не используйте `down -v` для данных, которые нужно сохранить. Не меняйте DB-пароли в `.env` после инициализации volume без отдельной смены пароля роли в PostgreSQL: init-скрипт запускается только на пустом volume. Web не запускает миграции автоматически. После изменения кода пересоберите web и выполните явную команду migrate перед `up`.

## Проверки этапа 1

```bash
docker compose exec -T web python manage.py check --database default
docker compose run --rm migrate python manage.py makemigrations --check --dry-run
docker compose exec -T web python -m pip check
docker compose run --rm tests
python3 scripts/smoke.py
docker compose exec -T web python scripts/check_production.py
docker compose exec -T web python manage.py collectstatic --noinput
```

`tests` создает и удаляет отдельную `test_kp11`, использует миграционную учетную запись, затем проверяет ограничения runtime-роли и настоящие PostgreSQL row locks. Тестовые сотрудники существуют только в тестовой БД. Это тесты каркаса, не бизнес-приемка еще не реализованных заданий. `smoke.py` проверяет реальные HTTP HTML/CSS, закрытый health, запрос к БД и получение нового SMTP-письма именно текущего запуска через Mailpit API.

`check_production.py` загружает production settings с несекретными примерами домена/SMTP и запускает `check --deploy --fail-level ERROR`. Это проверка конфигурации, без подключения к SMTP/HTTPS. Остается явное предупреждение `security.W021`: HSTS preload выключен до решения для реального production-домена. `--fail-level WARNING` возвращает 1 из-за этого предупреждения; оно не скрыто и не обозначено как отсутствие замечаний. Остальные ошибки должны приводить к ненулевому коду.

## Короткая ручная проверка

1. После запуска откройте в своем браузере `http://127.0.0.1:8000/`: должен быть переход на русскую страницу входа с объяснением, что вход появится на следующем этапе. Пароль и регистрация не предлагаются. CSS загружается; кнопки/ссылки имеют видимый focus. `/admin/` отсутствует. Health без внутреннего ключа возвращает 404.
2. Выполните `docker compose exec -T web python manage.py send_test_email`. В локальном Mailpit (`http://127.0.0.1:8025/`) найдите «KP11: проверка среды» и текст о доставке Django через SMTP. Автоматический `smoke.py` подтверждает получение уникального нового письма.
3. Проверьте сохранность development-маркера без создания аккаунтов:

```bash
docker compose exec -T web python manage.py environment_probe --marker manual-persist
docker compose restart db web
docker compose up -d --wait db web
docker compose exec -T web python manage.py environment_probe --marker manual-persist --check-only
```

Последняя команда должна сообщить `OK`; если записи нет, завершится ошибкой. Маркер — одна идемпотентная системная запись аудита без данных сотрудника. `environment_probe` и `send_test_email` запрещены production-настройками.

## Учетные записи БД и production-граница

На новом development volume init-скрипт создает `kp11_owner` (только bootstrap PostgreSQL), `kp11_migrator` (владелец схемы, DDL, CREATEDB для тестовой БД) и `kp11_app` (runtime, без SUPERUSER/CREATEDB/CREATEROLE/CREATE SCHEMA). Web получает только пароль `kp11_app`; bootstrap/migration-пароли в его окружение не передаются.

Сервис `migrate` запускает `prepare_database`: обычные Django migrations, затем `grant_runtime_access`. Эта повторяемая операция выдает DML/sequence-права, но запрещает runtime UPDATE/DELETE/TRUNCATE аудита, INSERT/DELETE guard и изменение django_migrations. Дополнительный DB-trigger запрещает изменение/удаление строк аудита даже через прямой SQL владельца. Миграционная учетная запись — доверенная инфраструктурная власть, способная менять схему; она не используется HTTP-приложением.

Production settings находятся в `config/settings/production.py`: DEBUG=False, реальные SMTP/HTTPS hosts из окружения, Secure cookies, CSRF origins, STARTTLS, HSTS и безопасные заголовки. Они отклоняют очевидную тестовую почту, wildcard hosts, попытку включить DEBUG и тестовый email backend. `Dockerfile` по умолчанию запускает Gunicorn с двумя workers от пользователя 10001; development Compose явно переопределяет это на runserver.

Production Compose/proxy, реальный SMTP, внешний HTTPS, browser-приемка и backup/restore выполняются на этапе 7; сейчас они **не проверены**. Development Compose/.env нельзя считать production-развертыванием. В production миграционную роль создают без CREATEDB, пользователь приложения не получает bootstrap/migration-секретов; реальные домены, DB/SMTP credentials и ключи задаются через защищенные настройки. Резервные копии и восстановление описаны как проектное решение в ARCHITECTURE, не как уже выполненная операция.

## Документация

| Документ | Содержание |
| --- | --- |
| [SPEC.md](SPEC.md) | Исходное техническое задание пользователя |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Стек, модули, схема данных, транзакции, история, запуск и безопасность |
| [PERMISSIONS.md](PERMISSIONS.md) | Каталог разрешений, начальные роли и объектная матрица доступа |
| [PLAN.md](PLAN.md) | Этапы реализации и проверяемые критерии приемки |
| [DECISIONS.md](DECISIONS.md) | Допущения, блокирующие вопросы и будущие production-параметры |
| [STATUS.md](STATUS.md) | Что сделано и что проверено на самом деле |
| [AGENTS.md](AGENTS.md) | Краткие правила работы с репозиторием |

Результаты реально выполненных проверок и ограничения — в STATUS. Следующий этап 2 начнется после отдельного указания пользователя.
