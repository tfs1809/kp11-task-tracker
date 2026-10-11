# Состояние проекта

## FastAPI rewrite

HTTP runtime перенесен на `fastapi_app.main:app`, ASGI entrypoint — `config.asgi:application`. Добавлены PostgreSQL access layer, session cookie, CSRF token, login/logout, recovery/reset, смена пароля, личные задачи, карточки, чек-листы, завершение, профиль, события и чат. Шаблоны находятся в `fastapi_app/templates`, статические CSS/JS сохранены.

Старые framework-specific runtime files, views, forms, settings, migration files и management commands удалены по прямому запросу пользователя. Схема существующей базы используется напрямую SQL-запросами; для нового runtime добавляется `app_sessions` через `scripts/prepare_database.py`.

### Проверено

- FastAPI образ собирается с hash-locked dependencies.
- `uvicorn` запускает приложение.
- `/accounts/login/` отдает русский HTML и прежнюю стилистику.
- `/` анонимному пользователю возвращает redirect на вход.
- health без ключа закрыт, с ключом отвечает `ok`.
- `python3 scripts/smoke.py`: HTTP redirect/login, CSS, private health, DB readiness и SMTP через Mailpit.
- `docker compose ... exec -T web python scripts/smoke_auth.py`: вход, смена пароля с отзывом сессий, recovery-запрос и одноразовый reset.
- `docker compose ... exec -T web python scripts/smoke_admin.py`: permission gate, создание задачи, вкладки активных/выполненных, приглашение и отключение учетной записи.
- `docker compose ... run --rm tests`: compileall для `fastapi_app` и `config`.
- `pip check`, `git diff --check` и поиск удаленных framework-следов завершились без ошибок.

### Не проверено

- полный browser regression suite;
- миграция старых данных и старых сессий;
- production HTTPS/SMTP;
- роли/группы и все расширенные административные операции;
- полный сценарий подзадач и блокировки/восстановления события пока не покрыт отдельным smoke-тестом;
- расширенные права, квоты, аудит и конкурентные конфликты.

## Следующий блок

Базовое администрирование сотрудников перенесено и проверено. Роли/группы, расширенные операции workflow, отдельная проверка подзадач, блокировки чата и аудит остаются следующим блоком; непроверенные сценарии не считаются готовыми.
