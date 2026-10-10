# Состояние проекта

Обновлено: 10 октября 2026, 18:50 UTC (21:50 Москва).

Проектирование и этапы 1–2 приняты пользователем. Этап 1 — коммит **34b12bb**, принятый этап 2 зафиксирован коммитом **f34e6b1** (`Implement employee access and secure authentication`), без push. По последней команде пользователя **этап 3 реализован полностью**; его изменения оставлены в рабочем дереве для приемки. Этапы 4–7 не начинались. SPEC.md, принятая PERMISSIONS.md, зависимости и digest базовых образов не изменены.

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

## Конфигурация среды и ограничения

Через cloud-environment-onboarding обновлен и сохранен черновик **start_skill** для этапа 3: существующий checkout, build/start/migrate/readiness, PostgreSQL tests, изолированные HTTP/SMTP сценарии, сохранение .env/volumes, безопасный bootstrap. install_script, repositories, allowlist и secret bindings не менялись. Для будущих задач проверьте/сохраните настройки среды и опубликуйте их; сохранение черновика само по себе не публикует среду. Публикация и fresh-task restoration **не выполнены**.

- Этапы 4–7 не реализованы: отметки/выполнение/возврат, редактирование/отзыв/восстановление/архив/статистика, полный интерфейс и чат. Деактивация сейчас запрещает чтение/сессии; перевод Assignment в revoked и отсутствие автоматического восстановления при реактивации будут этапом 4.
- Ручной сценарий человеком, браузер/JS engine и 360/1280 px, внешние HTTPS/SMTP/proxy, backup/restore и нагрузка **не проверены**. Runtime grants и автоматический HTTP smoke не заменяют production-приемку. W021 не скрыт.
- Обычная сборка без cloud overlay за пределами этой среды и другие CPU/OS не проверены. Основные db/web/Mailpit работают; одноразовые smoke projects остановлены без удаления volumes. Основная БД не удалялась и не заполнялась тестовыми сотрудниками.

Точные команды запуска/проверок, bootstrap и короткие ручные сценарии — [README.md](README.md). Production-параметры — [DECISIONS.md](DECISIONS.md).

## История этапа 2 (коммит f34e6b1)

Ниже сохранены результаты предыдущего этапа; они не заменяют текущие результаты этапа 3.

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
