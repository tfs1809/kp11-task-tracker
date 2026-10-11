# Трек задач КП11

FastAPI-приложение для задач и событий сотрудников. Внешний интерфейс, русский UI, карточки задач, чек-листы и чат событий обслуживаются нативными FastAPI handlers. Данные читаются из PostgreSQL через синхронный access layer.

## Запуск

```bash
python3 scripts/init_dev_env.py
export BUILDX_CONFIG="$PWD/.cache/buildx"
docker compose -f compose.yaml -f compose.cloud.yaml build web migrate tests
docker compose up -d --wait db mailpit
docker compose run --rm migrate
docker compose up -d --wait web
python3 scripts/smoke.py
docker compose exec -T web python scripts/smoke_auth.py
docker compose exec -T web python scripts/smoke_admin.py
```

Откройте http://127.0.0.1:8000/. Mailpit доступен на http://127.0.0.1:8025/.

## Проверки

```bash
docker compose run --rm tests
docker compose exec -T web python -m pip check
docker compose exec -T web python scripts/check_health.py
python3 -m compileall -q fastapi_app config scripts
git diff --check
```

Health endpoints закрыты ключом `HEALTHCHECK_TOKEN`: без ключа возвращается 404, с ключом — `ok`.
