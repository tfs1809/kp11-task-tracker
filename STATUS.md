# Состояние проекта

Обновлено: 10 октября 2026, UTC.

## Выполнено

Проектирование и этап 1 приняты пользователем. Принятый этап 1 зафиксирован коммитом **34b12bb** (`Prepare architecture and stage 1 development foundation`). По следующей команде пользователя **этап 2 реализован полностью**; изменения этапа 2 находятся в рабочем дереве для приемки. Этапы 3–7 не начинались. SPEC и принятая PERMISSIONS.md сохранены без изменений.

- Role/Permission/RolePermission/UserRole и AudienceGroup/Membership, UUID и уникальные связи, версии редактируемых справочников. Миграции создают четыре начальные роли, три группы и каталог из 12 разрешений. Права действующих ролей объединяются; членство в группе ничего не разрешает и автоматически не назначается по роли.
- Только системный администратор управляет сотрудниками, ролями/правами и группами через собственные формы/services. Permission-каталог недоступен для runtime DML. PostgreSQL triggers защищают key/system-флаг роли, неизменяемый email, административные разрешения и полный каталог прав системной роли. Универсального Django Admin CRUD нет.
- Корпоративная почта нормализуется trim/lowercase и допускает только corp.kp11.ru; уникальность включает отключенные аккаунты. Приглашения, повторная отправка, активация своим валидным паролем и одноразовый локальный bootstrap. Срок приглашения 48 часов; хранится только digest подписанного случайного токена. SMTP вызывается после commit, без удержания guard; ошибка доставки сохраняет приглашение и позволяет безопасную повторную отправку.
- EmailBackend, Django password hashers/validators (минимум 12 символов), login и POST logout, recovery/change password. Reset использует штатный PasswordResetTokenGenerator и отдельный одноразовый grant на час. Recovery дает одинаковые HTTP/текстовые ответы для неизвестного, отключенного и неактивированного адреса, включая ошибку SMTP. Равное время ответа не заявляется.
- DB sessions и auth_version; отключение отзывает сессии и неиспользованные auth-ссылки. Reset отзывает все старые сессии, смена пароля сохраняет текущую и отзывает остальные. Снятие/отключение роли проверяется на следующих запросах без кеша прав. Реактивация не возвращает старую сессию. Сами строки старых сессий могут храниться до expiry/clearsessions, но доступа уже не дают.
- PostgreSQL AccessGuard сериализует изменения. Actor/epoch/права перечитываются перед действием и перед показом защищенной POST-формы. Last-admin invariant проверяется после потенциального изменения, с атомарным откатом. Все административные изменения и успешные auth-действия записываются в неизменяемый аудит; пароли/токены/ссылки/cookie не входят в payload.
- PostgreSQL RateLimitBucket с HMAC ключа: вход 5/5 минут email+IP и 30/5 минут IP; recovery 3/час email и 20/час IP; приглашения 3/час адрес и 60/час администратор. Общие для соединений/workers, атомарное расходование нескольких лимитов, 429/Retry-After. Используется REMOTE_ADDR, произвольный X-Forwarded-For не принимается.
- Русские forms/страницы, CSRF для изменяющих запросов, no-store и no-referrer, безопасные ошибки без auth traceback/секретов. Фиксированный PUBLIC_BASE_URL для почтовых ссылок; production требует явный разрешенный HTTPS origin. «Мои задачи» доступен любому действующему сотруднику и показывает собственные ФИО/роли/группы и честное пустое состояние.

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

## Конфигурация среды и ограничения

Подтверждено сохранение обновленного черновика **start_skill** через cloud-environment-onboarding: актуальные build/start/migrate/readiness и правила этапа 2, изолированный HTTP/SMTP smoke, безопасный bootstrap, сохранение .env/volumes и ограничения. Ранее сохраненный install_script остается действующим. Membership репозиториев, allowlist и внешние secret bindings не менялись. Сохранение не публикует среду: для будущих задач нужно проверить/сохранить черновик в настройках среды, затем опубликовать среду. Публикация и fresh-task restoration не выполнялись и не проверены.

- WorkItem/Assignment, прогресс, чат и полная объектная матрица будущих заданий еще не реализованы. Отзыв Assignment при отключении пользователя относится к этапу 4; сейчас отзываются сессии и auth grants. Следующий этап 3 требует отдельной команды пользователя.
- Ручной сценарий человеком, браузерные проверки 360/1280 px, внешние HTTPS/SMTP и proxy logging/IP, production static routing, backup/restore и нагрузка **не проверены**. Автоматический HTTP smoke не запускает браузер/JS engine. W021 остается явным ограничением до выбора production-домена.
- Обычная сборка без cloud overlay за пределами этой среды и другие CPU/OS не проверены. Основные web/db/Mailpit работают; одноразовые smoke projects остановлены, их тестовые volumes сохранены. Удаления основной БД не было.

Точные команды запуска, первого приглашения, проверок и короткий ручной сценарий — [README.md](README.md). Production-параметры — [DECISIONS.md](DECISIONS.md).
