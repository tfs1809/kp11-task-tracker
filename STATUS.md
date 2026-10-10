# Состояние проекта

Обновлено: 10 октября 2026, 19:29 UTC (22:29 Москва).

Проектирование и этапы 1–3 приняты пользователем. Коммиты: этап 1 **34b12bb**, этап 2 **f34e6b1**, принятый этап 3 **d3854b7** (`Implement task and event issuance with recipient snapshots`), без push. По последней команде пользователя **этап 4 реализован полностью**; изменения оставлены в рабочем дереве для приемки. Этапы 5–7 не начинались. SPEC.md, принятая PERMISSIONS.md, зависимости и digest базовых образов не изменены.

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
