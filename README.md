# Трек задач КП11

FastAPI-приложение для задач и событий сотрудников. Внешний интерфейс, русский UI, карточки задач, чек-листы и чат событий обслуживаются нативными FastAPI handlers. Данные читаются из PostgreSQL через синхронный access layer. `scripts/prepare_database.py` создаёт нативную PostgreSQL-схему и базовый каталог ролей/прав.

## Запуск

```bash
python3 scripts/init_dev_env.py
export BUILDX_CONFIG="$PWD/.cache/buildx"
docker compose -f compose.yaml -f compose.cloud.yaml build web migrate tests
docker compose up -d --wait db mailpit
docker compose run --rm migrate
docker compose up -d --wait web
docker compose exec web python scripts/bootstrap_admin.py --email egorovasv@kp11.ru --full-name "Егорова СВ админ"
python3 scripts/smoke.py
docker compose exec -T web python scripts/smoke_auth.py
docker compose exec -T web python scripts/smoke_admin.py
```

Откройте http://127.0.0.1:8000/. Mailpit доступен на http://127.0.0.1:8025/.

При первом запуске bootstrap отправляет приглашение администратора в Mailpit. Откройте письмо, перейдите по ссылке и задайте пароль. Повторный запуск после активации запрещен.

## Проверки

```bash
docker compose run --rm tests
docker compose exec -T web python -m pip check
docker compose exec -T web python scripts/check_health.py
python3 -m compileall -q fastapi_app config scripts
git diff --check
```

Health endpoints закрыты ключом `HEALTHCHECK_TOKEN`: без ключа возвращается 404, с ключом — `ok`.
