# Django: запуск в Docker Desktop на Windows

Инструкция для локального development-запуска Django-версии из `main`. Все команды выполняются в **PowerShell**. PostgreSQL, Mailpit и Python приложения работают в Linux-контейнерах. Локальные PostgreSQL, Node и виртуальное окружение приложения не нужны. Compose использует development settings и Django runserver; production-развертывание описывается отдельно.

## 1. Подготовка

Установите Git for Windows, Python 3 с Python Launcher (`py`) и Docker Desktop. В Docker Desktop включите **Use the WSL 2 based engine** и режим **Linux containers**. Дождитесь запуска Docker Desktop.

```powershell
git --version
py -3 --version
docker version
docker compose version
docker info --format '{{.OSType}}'
```

Последняя команда должна вывести `linux`; `docker version` должен показывать Client и Server. Нужен Compose v2 с поддержкой `up --wait`. После установки Python откройте новое окно PowerShell, если `py` не найден. Python на Windows используется для вспомогательных скриптов; Python приложения закреплен в Dockerfile.

## 2. Получение проекта

В каталоге, где хотите разместить проект:

```powershell
git clone -c core.autocrlf=false --branch main https://github.com/tfs1809/kp11-task-tracker.git
Set-Location .\kp11-task-tracker
```

`core.autocrlf=false` сохраняет LF при checkout. Это необходимо для `docker/postgres/init.sh`, который выполняется в Linux-контейнере. В редакторе сохраняйте этот файл с LF.

Для существующего репозитория сначала проверьте `git status --short` и сохраните свои изменения, затем переключитесь на Django-ветку `main` и обновите ее через `git pull --ff-only origin main`. Для этой инструкции нужна Django-версия. Настройка `git config core.autocrlf false` действует на будущие checkout; уже записанный `init.sh` с CRLF нужно отдельно сохранить с LF в редакторе.

Все дальнейшие команды выполняются из корня репозитория, рядом с `compose.yaml`.

## 3. Первый запуск

```powershell
py -3 -X utf8 scripts/init_dev_env.py
docker compose config --quiet
docker compose build web
docker compose up -d --wait db mailpit
docker compose run --rm migrate
docker compose up -d --wait web
docker compose ps
py -3 -X utf8 scripts/smoke.py
```

Выполняйте команды по очереди. При ненулевом коде (`$LASTEXITCODE`) остановитесь и устраните причину. Первая сборка скачивает закрепленные образы и зависимости; нужен доступ к registry и PyPI.

`-X utf8` обеспечивает чтение файлов с русским текстом на Windows. Генератор создает `.env` со случайными development-секретами и сохраняет существующий файл без изменений. Не копируйте `.env.example` вместо генерации: обязательные секреты в нем пустые. `.env` исключен из Git. Сообщение генератора о правах `0600` относится к POSIX; на Windows доступ определяется NTFS ACL, и ограничение ACL этим скриптом не проверено.

`migrate` применяет Django-миграции и выдает runtime-роли PostgreSQL необходимые права. Web автоматически миграции не выполняет. Для Windows используется обычный `compose.yaml`. Overlay `compose.cloud.yaml` содержит путь к сертификатам текущей Linux cloud-среды и здесь не нужен.

После запуска:

- Приложение: `http://localhost:8000/` — форма входа, доступ по приглашению.
- Mailpit: `http://localhost:8025/` — просмотр отправленных писем.
- `db` и `web` в `docker compose ps` должны быть `healthy`; `mailpit` — `running`.
- Smoke должен вывести `HTTP: ... — OK` и `SMTP: ... — OK`: входная страница, CSS, закрытый health, БД и новое SMTP-письмо. Сотрудников эта проверка не создает.

Порты опубликованы только на `127.0.0.1`; PostgreSQL не публикует порт на Windows. Приглашения доставляются в Mailpit, внешняя корпоративная почта в этом запуске не используется.

## 4. Первый администратор

На новой БД администратор автоматически не создается. Введите свой корпоративный адрес в домене `corp.kp11.ru` и ФИО:

```powershell
$bootstrapEmail = Read-Host 'Корпоративная почта первого администратора'
$bootstrapName = Read-Host 'ФИО первого администратора'
docker compose exec -T web python manage.py bootstrap_admin --email "$bootstrapEmail" --full-name "$bootstrapName"
```

Откройте Mailpit, найдите приглашение на этот адрес, перейдите по ссылке и задайте пароль минимум из 12 символов. Затем войдите в приложение. Команда не выводит пароль или ссылку активации в терминал. После активации повторный bootstrap запрещен. Для БД с активным администратором используйте его учетную запись.

Если SMTP-доставка не подтвердилась или нужно заменить приглашение до активации, устраните проблему и повторите для того же адреса:

```powershell
docker compose exec -T web python manage.py bootstrap_admin --email "$bootstrapEmail" --full-name "$bootstrapName" --resend
```

Прежняя ссылка будет отозвана.

## 5. Проверки и ручной сценарий

```powershell
docker compose exec -T web python manage.py check --database default
docker compose run --rm migrate python manage.py makemigrations --check --dry-run
docker compose exec -T web python -m pip check
docker compose run --rm tests
```

`tests` создает и удаляет отдельную PostgreSQL-БД `test_kp11`; рабочая БД сохраняется. Последний подтвержденный результат на Django-коде: **162 теста прошли в Linux cloud-среде**. Запуск в Docker Desktop Windows пока не проверен; проверяйте итог и код завершения своего запуска. Browser runners из README проверялись на Linux с отдельным Chromium/venv; их установка на Windows этой инструкцией не подтверждается.

Ручной сценарий после активации администратора:

1. Войдите, откройте «Администрирование», пригласите сотрудника. В карточке назначьте роль «Преподаватель» и подходящую группу. Активируйте приглашение через Mailpit; сотрудником войдите в отдельном приватном окне браузера.
2. Администратором выдайте сотруднику задачу с пунктом чек-листа через превью и подтверждение. Сотрудником откройте «Мои задачи», отметьте пункт и завершите задачу. Администратором проверьте прогресс и историю.
3. Администратором выдайте сотруднику событие. Откройте чат в обоих окнах, отправьте сообщение сотрудником и дождитесь появления у администратора (polling примерно раз в 10 секунд). Закройте чат администратором; после обновления у сотрудника отправка должна быть недоступна.

Это сценарий для пользователя, а не запись о выполненном ручном тестировании.

## 6. Остановка, повторный запуск и обновление

Остановка с сохранением БД и писем:

```powershell
docker compose down
```

Повторный запуск:

```powershell
docker compose up -d --wait db mailpit
docker compose run --rm migrate
docker compose up -d --wait web
```

Данные хранятся в named volumes `postgres_data` и `mailpit_data` проекта Compose. Сохраняйте `.env` вместе с ними: после инициализации БД смена паролей только в `.env` не меняет пароли ролей PostgreSQL. Не используйте `docker compose down -v` или удаление volumes в Docker Desktop, если данные нужно сохранить. Обычный `down` и перезапуск Docker Desktop их сохраняют.

Обновление чистого checkout на `main`:

```powershell
git pull --ff-only origin main
docker compose build web
docker compose up -d --wait db mailpit
docker compose run --rm migrate
docker compose up -d --wait web
py -3 -X utf8 scripts/smoke.py
```

Исходники копируются в образ при сборке, поэтому изменения кода требуют `build web`.

## 7. Диагностика

```powershell
docker compose ps -a
docker compose logs --tail 100 db
docker compose logs --tail 100 web
docker compose logs --tail 100 mailpit
```

- Docker daemon недоступен: запустите Docker Desktop и проверьте Linux containers / WSL 2.
- Пустые обязательные переменные: выполните генератор `.env`; существующий файл он не исправляет и не перезаписывает.
- Ошибка `init.sh` с `\r` / `bad interpreter`: проверьте LF у `docker/postgres/init.sh`. После прерванной первичной инициализации повторный старт существующего volume не выполняет init-скрипт заново; сначала выясните состояние БД, не удаляйте данные автоматически.
- Порты заняты: измените, например, `WEB_PORT=18000`, `MAILPIT_PORT=18025` и `PUBLIC_BASE_URL=http://localhost:18000` в `.env`; повторите `docker compose up -d --wait web mailpit`. Открывайте соответствующие адреса и запускайте smoke так: `py -3 -X utf8 scripts/smoke.py --web-port 18000 --mailpit-port 18025`.
- PostgreSQL отвергает пароль после изменения `.env`: верните сохраненные значения либо выполните отдельную смену паролей ролей. Повторная миграция это не исправит.

Не публикуйте `.env`, секретные ссылки приглашений и пароли при передаче диагностики.

## Пределы проверки

Инструкция сверена с текущими `compose.yaml`, Dockerfile, генератором `.env` и bootstrap-командой. Реальные Linux-проверки и их ограничения записаны в [STATUS.md](STATUS.md). Запуск в Windows / Docker Desktop и ручной сценарий выше **не выполнялись в текущей Linux cloud-среде**.
