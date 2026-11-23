# OttoRestart — полный технический аудит

**Дата среза:** 10.11.2026 (Asia/Qyzylorda)
**Область:** `OTTO restart project/` — приложение, backend, миграции, фоновые задачи, Docker-конфигурация, тесты и эксплуатационные документы. Исходный срез охватывал 0001–0017; позднее миграции 0018 и 0019 применены по разрешению пользователя.
**Статус:** описывает код и последние сохранённые эксплуатационные сведения; это не разрешение на публикацию и не утверждение, что все флаги или сервисы были заново проверены во время подготовки отчёта.

> OttoFlow в рамках аудита читался только для контекста конфигурации учётных данных. Никакие файлы OttoFlow этим документом не предлагается изменять. GPT API key не читался, не проверялся и не использовался; никаких запросов к OpenAI не выполнялось.

> **Последующее обновление 10.11.2026:** карточка сохраняет правки на сервере в отдельной таблице `restart_product_edits`; локальные черновики переносятся только по выбору пользователя. Пагинация и фильтры каталога работают через сервер, предпросмотр EAN не обрезается до 100 строк. По последнему решению пользователя ценовая база — максимум подтверждённых OTTO/Aftercool, скидка JV 10 € / XL 20 €, затем `,99`; для Direct OTTO используется OTTO. Сохранённые правки ещё не преобразуются в проверенную operation revision и блокируют QC до реализации этого перехода. Публикация остаётся закрытой.

## 1. Итог для оператора

OttoRestart — отдельное Next.js-приложение с собственной FastAPI-службой, PostgreSQL-схемой, BFF-прокси и изолированными Docker-сервисами. В коде есть настоящий импортный контур 3M/12M: импорт списка EAN, чтение OTTO и Aftercool по заданным кандидатам, классификация периода, сохранение исходных записей и проекции по фабрикам. JV и XL проходят через account-scoped контракты.

Импорт не равен подготовке новой карточки или публикации. Реализация сервиса подготовки умеет сохранять source snapshots и цену, но не строит OTTO target payload. API `/operations/{id}/prepare` теперь возвращает `409 OTTO_TARGET_PAYLOAD_NOT_BUILT` **до** OTTO/Aftercool reads и до резервирования EAN. Поэтому текущий полный production-путь останавливается до валидного target payload, QC approval и Drip Feed submission. В UI одновременно существуют production-backed импорты/каталоги, отдельные DEMO-страницы и ещё не подключённые операции.

**Текущий вывод:** OttoRestart умеет импортировать и исследовать ограниченный набор товаров, но сквозное создание и безопасная публикация нового товара пока не готовы. Production publish должен оставаться закрытым.

## 2. Инвентаризация исследованных слоёв

### 2.1. Репозиторий и изоляция

- OttoRestart расположен в отдельном каталоге `OTTO restart project/`; собственные инструкции — [`AGENTS.md`](AGENTS.md).
- Frontend имеет `basePath: /restart`; backend доступен через Next.js BFF. Compose определяет отдельные Restart API, PostgreSQL, Redis, worker, dispatcher и Beat. Исполняемые точки сборки и запуска находятся в [`docker-compose.yml`](docker-compose.yml), [`next.config.ts`](next.config.ts) и [`backend/app.py`](backend/app.py).
- OttoRestart не должен использовать БД, Redis, очередь или внутренние UI/store OttoFlow. Это также зафиксировано в [`README.md`](README.md) и [`docs/OPS_RUNBOOK.md`](docs/OPS_RUNBOOK.md).

### 2.2. Frontend-маршруты

| Экран | Реальный путь | Текущая основа |
|---|---|---|
| Обзор | `app/page.tsx` | Демо-метрики и состояние workspace |
| Afterbuy/фабрики/Direct OTTO | `app/afterbuy/`, `app/direct-otto/` | Реальные account-scoped каталоги там, где подключён API |
| Обнаружение 3M/12M | `app/discover/` | Реальный upload/paste import и просмотр прогресса |
| Партии и детали | `app/batches/`, `app/batches/[batchId]/` | Production/test routes подключают API-backed batch workspaces; demo оставлен отдельным режимом |
| Проверка | `app/review/`, `app/quality-control/` | Production/test показывают account-scoped source candidates и позволяют создать локальный DRAFT; это ещё не QC/approval workflow |
| Пул EAN | `app/ean-pool/` | Production/test workspace подключён к API пула; demo оставлен отдельным режимом |
| История | `app/history/` | Production/test workspace подключён к API событий/операций; demo оставлен отдельным режимом |
| Очередь | `app/queue/` | Production mode показывает persisted batch schedules и read-only список durable background jobs; scheduler/workers остаются закрыты |
| Настройки | `app/settings/` | Production/test читают безопасный backend AI status; ключ не вводится и GPT-запрос не делается |
| Каталог/библиотека | `app/library/` и каталоги | Реальные API-клиенты для части функций; отдельные действия имеют ограничения |

Страницы с DEMO-компонентами не следует считать полноценным производственным workflow. Утверждение «все шесть страниц буквально обёрнуты `RuntimeModeGate`» неточно: некоторые route-файлы напрямую рендерят demo-workspace, другие используют demo review client. Практический результат одинаков: эти страницы не являются production-источником состояния.

### 2.3. Frontend API и BFF

- `features/api/client.ts` содержит account-aware клиент для Restart API, ручной синхронизации `syncProduct`, статуса AI и локального создания operation DRAFT; prepare/QC typed methods есть, но production UI их не запускает.
- Методы старого UI для `/products/assign-ean`, `/products/generate-titles` и product PATCH соответствуют API-пути, которые backend намеренно отвергает с `409 NOT_IMPLEMENTED`.
- Production UI вызывает только безопасное создание DRAFT. Подготовка, manual revision и QC пока не соединены с действием оператора. Backend API подготовки закрыт ранним fail-closed ответом до source GET и EAN reservation, пока target payload builder не готов.
- `app/api/backend/` проксирует запросы к отдельному backend; `app/api/demo/` обслуживает отдельные in-memory demo sessions. Они не должны смешиваться.

### 2.4. Backend и HTTP API

FastAPI-точка входа — [`backend/app.py`](backend/app.py). Основные группы маршрутов:

| Группа | Маршруты (контракт) | Фактическое назначение |
|---|---|---|
| Состояние/каталог | `/health`, `/overview`, `/products`, `/products/{id}`, `/products/{id}/sync`, `/categories` | Диагностика, кандидаты, read-only синхронизация источников |
| Устаревшие продуктовые записи | `/products/assign-ean`, `/products/generate-titles`, `PATCH /products/{id}` | Отклоняются guard-функцией `NOT_IMPLEMENTED` |
| Импорт | `/imports/preview`, `/imports/upload`, `/imports/paste`, `/imports/runs*` | Dry preview, durable run, строки и результаты импорта |
| Партии Drip | `/batches*` | Хранение/управление партиями; их наличие не означает наличие готового product payload |
| EAN | `/ean-pool`, `/ean-pool/import`, `/library/assign-ean`, `/library/ean-pool` | Production API пула/резервирования и отдельная библиотека lineage |
| Операции | `/operations`, `/operations/{id}/prepare`, `/manual-revision`, `/qc`, `/cancel` | Серверный lifecycle подготовки, ревизий, контроля и отмены |
| GTIN и аудит | `/gtin-evidence*`, `/events`, `/legacy/*` | Evidence, операторская проверка, аудит и legacy-диагностика |
| Очередь | `/queue` | Account-scoped read-only projection из `background_jobs`; не отправляет jobs и не возвращает payload |
| Discovery | router-модули `backend/routers/` | Ограниченные scans, enrichment, baseline и schedule-контракты |

Наличие endpoint не означает, что его вызывает интерфейс, он включён worker-allowlist или он допускает публикацию.

### 2.5. Backend-сервисы

Слой `backend/services/` содержит импорт и маршрутизацию источников, OTTO/Aftercool read adapters, factory/candidate projections, library/EAN, operation revisions, подготовку, QC, submission/Drip, категории, бренды и AI title helper. Отдельные adapters требуют включённых режимных флагов и credentials; target-payload builder не реализован, а HTTP prepare route закрыт без внешних reads и резервирования EAN. Актуальный официальный контракт `POST /v5/products` требует, среди прочего, `productDescription`, `mediaAssets`, `pricing` и `compliance`; рекомендации OTTO также требуют обязательные identity/category/brand поля, Legal attributes и соблюдение требований к изображениям. В коде OttoRestart нет подтверждённого полного mapping этих полей, поэтому сборку payload по догадке не выполняем ([OTTO Products API](https://api.otto.market/docs/api-docs/), [OTTO Best Practices](https://api.otto.market/docs/about-the-api/best-practices/)). Подробные интерфейсы и правила перечислены в [`BACKEND_IMPLEMENTATION_PLAN.md`](BACKEND_IMPLEMENTATION_PLAN.md) и [`docs/S00_CHECKPOINT.md`](docs/S00_CHECKPOINT.md); executable-код имеет приоритет над планом.

### 2.6. Фоновые задачи

- Долговечные jobs хранятся в `background_jobs` и `background_job_attempts`, отправка выполняется outbox dispatcher через Redis/Celery.
- Текущий `FIRST_RELEASE_JOB_ALLOWLIST` содержит только `import.process`. Celery регистрирует для работы только import executor; остальные executor/job handlers не проходят общий worker gate.
- Для dispatcher, worker и Beat в production обязателен `OTTORESTART_MANUAL_IMPORTS_AFTER`, который ограничивает выдачу задач временем создания. Compose теперь передаёт cutoff также в `restart_beat`; Beat намеренно не запускался и remains disabled.
- Последнее сохранённое состояние на 10.11.2026: Redis, один worker и dispatcher были активированы с import-only cutoff; Beat оставался остановлен. Три более ранних import jobs и одна discovery job оставались в очереди. Это сохранённое состояние не перепроверялось в живых контейнерах при подготовке аудита.

Источники: [`backend/jobs/dispatcher.py`](backend/jobs/dispatcher.py), [`backend/jobs/celery_app.py`](backend/jobs/celery_app.py), [`docker-compose.yml`](docker-compose.yml#L126).

### 2.7. База данных и миграции

В SQL-миграциях 0001–0017 объявлено 28 таблиц. Все имена и группировка приведены в разделе 7. Migration 0018 adds no tables; it adjusts scope-aware uniqueness and reservation lineage. На момент исходного аудита она была только подготовлена; последующее операционное обновление в разделе 18 фиксирует preflight и применение к выбранной активной БД. Migration runner and version ledger are in `backend/migration_runner.py` and `backend/migrations/`.

### 2.8. Внешние интеграции

- OTTO используется для чтения статуса/карточки/цены/атрибутов и ограниченного импорта кандидатов; производственные write-флаги product/availability остаются отдельными.
- Aftercool используется для account/factory-scoped поиска источника и чтения подтверждённых атрибутов/цен. Для дубликатов есть исключения защищённых фабрик и provenance в текущем import path.
- Backend-only `OTTORESTART_OPENAI_API_KEY` и `OTTORESTART_OPENAI_ENABLED` управляют генерацией. Production operations UI может отправить только исходное название на `/ai/title-suggestion` после явного нажатия оператора; ответ ограничен терминами из исходного названия, требует ручной проверки и не сохраняется. AI-атрибуты и автоматическая подготовка operation revision не подключены.
- Пользователь сообщил, что ключ OttoRestart задан в ожидаемой переменной окружения. Значение не читалось и не проверялось внешним запросом; доступность функции также зависит от feature flag и model config.

### 2.9. Авторизация и безопасность

Реальный backend имеет principal/account scope, origin/CSRF-проверки для local write endpoints, internal-auth для BFF и idempotency/version checks в критичных операциях. Production/deployment boundary требует проверки отдельно; локальный demo-session store не является RBAC или распределённым хранилищем. Не считать клиентский account switcher авторизацией: сервер должен повторно ограничивать account.

### 2.10. Тесты

- `backend/tests/` включает unit и PostgreSQL integration suites; frontend имеет Node tests и типизацию.
- Последний checkpoint 10.11.2026 сообщает: 86 целевых backend/job/import тестов, 9 Node API-client тестов, TypeScript и Python compilation прошли на disposable PostgreSQL 15/Redis; контейнеры удалены. Это история предыдущего шага, не повторный запуск для данного отчёта.
- Неподтверждённые или недостижимые paths нельзя считать покрытыми только потому, что для сервиса существует отдельный unit-test.

### 2.11. Документация и конфигурация

Текущие документы включают [`README.md`](README.md), [`docs/OPS_RUNBOOK.md`](docs/OPS_RUNBOOK.md), [`docs/RELEASE_CHECKLIST.md`](docs/RELEASE_CHECKLIST.md), [`docs/S00_CHECKPOINT.md`](docs/S00_CHECKPOINT.md), demo-гайды и API contracts. README описывает worker profiles как остановленные по умолчанию; Runbook также фиксирует более позднюю активацию worker/dispatcher для ручного импорта. Эти утверждения относятся к разным уровням: дефолт Compose и последнее сохранённое runtime-состояние.

## 3. Реальная схема работы сейчас

```text
оператор передаёт старые EAN + выбирает JV/XL
           │
           ▼
preview / durable import run / row-level progress
           │
           ├── account-scoped OTTO reads: status, SKU, standardPrice, product data
           ├── Aftercool exact-EAN lookup (с правилами фабрик и provenance)
           └── routing/classification: Aftercool source или подтверждённый Direct OTTO
           │
           ▼
source_products + observations + candidate_episodes + factory projections
           │
           └── возможна ручная source sync и operation snapshot
                    │
                    ▼
        STOP: readinessGaps=[OTTO_TARGET_PAYLOAD_NOT_BUILT]
                    │
                    ├── QC не принимает такую ревизию
                    ├── Drip eligibility не считает её готовой
                    └── worker не имеет publish executor в allowlist
```

Импорт сохраняет и классифицирует source-кандидата. Он не генерирует GTIN, не подменяет старый товар, не доказывает право на повторное размещение и сам по себе не подготавливает OTTO target listing.

## 4. Единственный завершённый сквозной production-сценарий

### Ручной import кандидата 3M/12M

1. Оператор выбирает JV или XL и передаёт коды через доступный import UI (paste/upload по поддерживаемому формату).
2. Preview показывает валидацию входа до постановки durable job.
3. Backend ставит идемпотентную import job; UI хранит run ID и восстанавливает прогресс/результат по строкам.
4. Import executor для каждой ограниченной строки делает account-scoped reads OTTO и source lookup Aftercool, применяет routing и классификацию периода.
5. Результат фиксируется в OttoRestart PostgreSQL; повторный вызов ограничен idempotency и import history.
6. UI показывает завершённые строки/ошибки и фабричные projections.

Последний сохранённый фактический пример в `.codex/CURRENT_STATE.md`: импорт одного JV EAN `4260606059116` создал source product для фабрики `409886`; позже reducer/source linkage был исправлен, и projection вернул 1 кандидата 3M. Это демонстрирует адресный import path, не доказывает массовую пропускную способность и не означает подготовку/публикацию.

## 5. Матрица UI → backend → фактический результат

| UI surface | Действие / клиент | Backend | Что реально происходит |
|---|---|---|---|
| Discover | upload/paste, preview, start run, poll run | `/imports/*`, `/imports/runs*`, durable job | Рабочий ограниченный import path |
| Factory/catalog | list/detail, account-scoped selection | `/factories`, `/products`; sync `/products/{id}/sync` | Чтение, projection и выбранная source sync; sync не делает target payload |
| Product drawer | legacy EAN/title/edit actions hidden or disabled outside demo; selected source sync | legacy write paths rejected; `/products/{id}/sync` | Production sync is an explicit account-scoped source read/snapshot update; no target payload or listing write |
| Operation UI | candidate workspace; `Создать DRAFT` | POST `/operations` | Creates persisted local DRAFT only; no GPT/OTTO/Aftercool call and no EAN reservation |
| Prepare/QC | typed client methods only; no production action exposed | `/operations/{id}/prepare`, `/qc`, `/manual-revision` | Still not in operator flow; prepare returns `409 OTTO_TARGET_PAYLOAD_NOT_BUILT` before source reads/EAN reservation |
| Settings | safe AI status read | GET `/ai/status` | Displays configured/enabled/model state only; never returns key and does not call GPT |
| Review/QC | production candidate workspace; demo review client remains in demo | `/products`, POST `/operations` | Candidate listing and optional local DRAFT creation; no QC/approval |
| EAN pool | API-backed workspace in production/test; demo separately | `/ean-pool*`, `/library/ean-pool` | Reads/imports against production API by account; eligibility/release semantics remain subject to backend and migration state |
| Queue/Drip | Production page reads durable job metadata and saved schedules | GET `/queue`; batch APIs | Read-only account-scoped projection excludes payload; import remains the only allowlisted executor |
| Batches/history | API-backed workspaces in production/test; demo separately | batch/import/history routes | Uses persisted backend state where endpoints exist; not synthetic demo state in production mode |

## 6. Реестр существенных заглушек и незавершённых связей

| ID | Экран/контрол | Клиент/handler | HTTP method/path | Backend поведение | Персистентное состояние | Внешний side effect | Симптом оператору | Severity | Статус |
|---|---|---|---|---|---|---|---|---|---|
| S-01 | Preparation | `prepare_operation_revision` | POST `/operations/{id}/prepare` | Внутренний сервис пока не строит payload; HTTP route возвращает `409 OTTO_TARGET_PAYLOAD_NOT_BUILT` до внешних reads и резерва EAN | DRAFT не меняется этим запросом | Нет для заблокированного HTTP вызова | Оператор получает явный blocker без траты EAN | External side-effect risk closed; payload blocker remains | Изменено fail-closed gate |
| S-02 | QC/Drip | QC/eligibility services | POST `/operations/{id}/qc`; внутренний Drip gate | Readiness gap блокирует QC/eligibility | QC result может сохранить причину отказа | Submit не проходит gate | READY/approved не достигается | Blocker | Подтверждено кодом |
| S-03 | Старые UI write-кнопки | Legacy handlers hidden/disabled вне demo; handler guard | Старые POST/PATCH paths | Backend отклоняет `NOT_IMPLEMENTED`; production UI их больше не предлагает | Новая revision не создаётся этими путями | Нет | Старые действия недоступны | Resolved for UI, backend endpoints remain retired |
| S-04 | Canonical operation flow | Typed client + production candidate workspace | POST `/operations`; typed prepare/QC methods | UI поддерживает только локальное создание DRAFT; prepare/QC/revision не открыты оператору | DRAFT persisted, без внешних calls и EAN reservation | Нет для DRAFT | Доступна начальная стадия, не полный lifecycle | Partial |
| S-05 | Settings AI | Production Settings workspace | GET `/ai/status` | Показывает серверную конфигурацию без раскрытия ключа; GPT request не выполняется | Только server configuration | Нет | Честный status, без ввода/хранения ключа в браузере | Resolved for status UI; AI remains disabled |
| S-06 | Auto category | `handleAutoAiCategory` | Нет | Regex выбирает category по title | Только frontend state | AI/network call нет | Эвристика выглядит как AI action | High | Подтверждено кодом |
| S-07 | AI Vision attrs | `handleTriggerAiAttributes` | Нет | Таймер выставляет demo values (`Eiche Natur`, `Massivholz`, размеры) | Только frontend state | AI/network call нет | В карточке появляются выдуманные атрибуты | Blocker для карточки | Подтверждено кодом |
| S-08 | Sync в drawer | Explicit `onSyncProduct` passed by catalog caller | POST `/products/{id}/sync` | Account-scoped source sync updates snapshot/revision | Persisted source snapshot/revision | Source reads only | Кнопка подключена; sync не формирует target payload | Implemented, not integration-verified |
| S-09 | Review/batches/pool/history | Production workspaces by mode | `/api/restart/*` APIs | Batch, EAN-pool and history screens use backend; review shows candidates + local DRAFT | Durable DB state where API exists | No external call for DRAFT | No production QC/approval yet | Partial |
| S-10 | Durable queue projection | `getQueue` + production page panel | GET `/queue` | Account-scoped read-only projection of persisted jobs; excludes payload/idempotency key | Job metadata only | None | Shows real counts, empty/error and job states | Implemented, DB integration not verified |

В `RuntimeModeGate` есть fail-closed режим для страниц без проверенного источника. Разные route-файлы подключают либо gate, либо demo workspace напрямую; поэтому здесь зафиксирован фактический источник состояния, а не утверждение, что все экраны буквально обёрнуты одной React-компонентой.

## 7. Инвентаризация 28 таблиц (миграции 0001–0017)

Список получен по SQL `CREATE TABLE` в миграциях OttoRestart. Назначение — группировка по именам/ссылкам; это не замена проверке физической схемы БД и содержимого строк.

| Домен | Таблицы |
|---|---|
| Импорт, источники, discovery и фабрики | `source_products`, `candidate_episodes`, `otto_status_observations`, `import_runs`, `import_rows`, `restart_factories`, `factory_completion_history`, `discovery_cursors` |
| Библиотека и EAN/GTIN | `otto_library`, `restart_ean_registry`, `ean_pool`, `ean_links`, `gtin_evidence` |
| Партии и операции | `restart_batches`, `restart_items`, `restart_operations`, `operation_revisions`, `qc_results`, `restart_authorizations`, `restart_events`, `restart_idempotency_keys` |
| Фоновые/внешние попытки и подтверждение | `background_jobs`, `background_job_attempts`, `submission_attempts`, `availability_attempts`, `otto_processing_polls`, `otto_online_polls` |
| Версии схемы | `schema_migrations` |

Всего: 8 + 5 + 8 + 6 + 1 = 28 таблиц. Значения вроде `PUBLISHED`, `ONLINE`, `ELIGIBLE` и `PRODUCTION` имеют разные источники/семантику; нельзя сводить их к одному булевому статусу.

### Схемные замечания

- Factory identity collision is already addressed by migration 0010, which changes the primary key to `(account,id,data_scope)`; it is not a current schema blocker.
- The natural `source_products` key and `otto_library(account,old_ean)` still ignore `data_scope`. New migration 0018 adds scope to those source identities. `otto_library(account,new_ean)` and EAN ownership in `ean_links` stay unique per account across scopes so one target GTIN cannot be assigned twice. For `ean_links`, 0018 excludes only `DEACTIVATED` links, allowing a safely released, never-submitted reservation to be allocated again.
- EAN reservation creates an `ACTIVE` row in `ean_links`. `cancel_operation()` now deactivates that lineage and releases the pool item only for a confirmed never-submitted operation; uncertain/submitted states remain reserved/quarantined.
- Сообщение исходного аудита, что импорт `LEGACY_UNVERIFIED` обязательно нарушает `ck_ean_pool_verified_has_actor_time`, кодом не подтверждается: импорт заполняет `verified_by` и `verified_at`, поэтому check удовлетворяется. Семантический дефект исправлен: legacy/unverified entries теперь получают `UNVERIFIED`, не `ELIGIBLE`.

Источники: [`0010_factory_coverage_generation.sql`](backend/migrations/0010_factory_coverage_generation.sql), [`0004_legacy_unverified_scope.sql`](backend/migrations/0004_legacy_unverified_scope.sql), [`0018_restart_scope_indexes.sql`](backend/migrations/0018_restart_scope_indexes.sql), [`0007_ean_pool_unified.sql`](backend/migrations/0007_ean_pool_unified.sql), [`ean_pool_service.py`](backend/services/ean_pool_service.py), [`operation_service.py`](backend/services/operation_service.py).

## 8. Celery, очередь и Drip Feed

**Кодовый gate:** `FIRST_RELEASE_JOB_ALLOWLIST = ("import.process",)`. Celery executor map default-регистрирует только `import.process`. `discovery_schedule_tick` зарегистрирован отдельно, но не расширяет allowlist продуктовых/submit job handlers. `otto.submit_product`, availability writes, process polling и online confirmation нельзя считать исполняемыми только потому, что для них есть сервисы или таблицы.

**Текущий production-like режим по checkpoint:** единственный активированный worker path — bounded manual import. Отправка продукта, изменение доступности и scheduler/Beat остаются вне активного scope. Compose теперь передаёт обязательный `OTTORESTART_MANUAL_IMPORTS_AFTER` для Beat, но Beat не запускался; worker allowlist не расширялась.

**Очередь интерфейса:** `/queue` теперь читает `background_jobs` по аккаунту и показывает безопасные метаданные. Это не очередь публикации и не доказательство ONLINE; import остаётся единственным allowlisted executor, другие типы jobs могут оставаться ожидающими.

## 9. Цены, идентификаторы, внешние запросы и секреты

- Текущий код для ручной синхронизации источника рассчитывает целевую цену из максимума OTTO `standardPrice` и подтверждённого Aftercool `Startpreis` для Aftercool-source, затем вычитает JV €10 / XL €20 и нормализует цену до окончания в `,99`. Для подтверждённого Direct OTTO используется OTTO. Это более позднее правило, чем базовый план, где заявлена только OTTO `standardPrice`.
- Исходные price/category/attribute snapshots сохраняются с provenance. Sync обновляет выбранные строки, не публикует их и не делает их автоматически `READY`.
- Текущие new identity defaults в code path требуют отдельных GTIN/ownership/business проверок. Присвоение идентификатора не является разрешением переиспользовать GTIN для неизменённого товара.
- Credentials JV/XL — backend-only configuration. Не переносить значения в документы, frontend, тестовые фикстуры или Git.
- Backend использует `OTTORESTART_OPENAI_API_KEY` и `OTTORESTART_OPENAI_ENABLED`; Compose сохраняет совместимость с alias `GPT_API_KEY`. Значение `.env` не читалось. Feature flag по умолчанию выключен; этот code tranche GPT-запрос не отправлял. При включённой конфигурации есть только явное title suggestion action, без автоматического вызова и сохранения результата.
- В рамках текущего отчёта ни одна внешняя API операция не выполнялась.

## 10. Пять критических разрывов до публикации

| № | Разрыв | Почему блокирует | Что должно закрыть |
|---|---|---|---|
| 1 | Нет target payload: только source snapshot и readiness gap | Нет валидированного DTO по обязательной OTTO schema и category/account rules | Валидированный builder из подтверждённых источников и актуальной схемы OTTO |
| 2 | UI создаёт DRAFT, но не даёт пройти manual revision/QC; prepare закрыт payload gate | Нельзя перевести operation в проверенный READY workflow | Manual revision/QC UI после полного content payload contract; сохранить fail-closed prepare gate до этого |
| 3 | Worker/dispatcher разрешает только `import.process` | Prepare/QC/submission job types не исполняются | Пошаговая регистрация local-only handlers; write jobs остаются закрытыми отдельным gate |
| 4 | Beat cutoff теперь передаётся в Compose, но Beat остаётся выключен и startup config отдельно не проверен | Планировщик нельзя считать действующим или проверенным | Сохранить Beat выключенным; проверить конфигурацию только в изолированном окружении перед отдельным разрешением запуска |
| 5 | EAN cancellation/import fixes готовы, migration 0018 authored; фактическая схема/данные не проверены | Миграция и конкурентное освобождение/резервирование пока не подтверждены PostgreSQL evidence | Disposable-schema preflight/up/down checks до применения 0018; не менять рабочую БД этим аудитом |

Для payload blocker появился безопасный API gate, но целевой payload, валидатор и подтверждённый schema mapping всё ещё отсутствуют. Даже после закрытия этих пунктов требуется отдельное разрешение на реальный publish; текущий audit не даёт такого разрешения.


### Шесть проверяемых сценариев

| № | Сценарий | Текущий результат | Ожидаемое поведение при завершении |
|---|---|---|---|
| 1 | Импорт кандидата, OTTO подтверждён, Aftercool exact match найден | Import job сохраняет source, account, period и factory projection | Сохранять результат идемпотентно; source provenance доступен в UI |
| 2 | Import Direct OTTO | Допустим только после подтверждённого отсутствия Aftercool и корректного source result | Ambiguous/ошибка Aftercool не превращается в Direct OTTO fallback |
| 3 | Несколько Aftercool matches, часть фабрик защищена | Policy пропускает защищённые matches и пишет provenance; остаточная неоднозначность блокируется | Никакого выбора по порядку ответа; показать unresolved reason |
| 4 | Выбранная source product синхронизируется | Account-scoped sync читает OTTO/Aftercool и обновляет source snapshot/несланную revision | Sync не считает товар подготовленным и не создаёт operation без явного действия |
| 5 | Operation prepare → QC | Preparation сохраняет source, но payload readiness gap блокирует QC | После реализации payload validator/QC принимает только immutable revision |
| 6 | Drip/queue пытается показать или отправить approved item | Durable jobs projection теперь read-only; worker allowlist только import; продуктовой отправки нет | Submit остаётся выключен до валидированного payload, reconciliation и отдельного допуска |

## 11. Дополнительные блокеры и ограничения

1. **AI подключён частично.** Settings показывает backend status без раскрытия ключа; явный запрос черновика названия доступен при включённом backend flag. Он не пишет данные и не создаёт operation revision.
2. **AI-атрибуты и AI-категория не production-функции.** Regex/category и статические vision values остаются только в demo; AI title suggestion передаёт только исходное название и требует ручной проверки.
3. **Queue/Drip не является очередью публикации.** Production экран показывает durable job metadata и локальные schedules; он не подтверждает отправку товара или ONLINE.
4. **Нет proof `ONLINE`.** Нельзя считать HTTP 202, локальную `PUBLISHED`-проекцию или наличие задачи подтверждением доступности товара на marketplace.
5. **Аудит не включал live data scan этой сессии.** Содержимое БД, фактические counts и актуальный статус контейнеров не перепроверялись.
6. **В корневом checkout есть другие незакоммиченные изменения.** Этот отчёт не является подтверждением чистоты всего Git дерева; изменения за рамками этих новых audit-файлов не трогались.

## 12. Проверки, покрытие и пределы доказательств

Последние сохранённые результаты кода: 86 backend тестов для job/import path, 9 Node API-client тестов, TypeScript и Python compilation; disposable PostgreSQL/Redis были удалены. Нет оснований переносить этот pass на невызванные UI operation endpoints, миграционный upgrade всего набора, production payload builder или реальный publish workflow.

В этом документальном шаге тесты, миграции, production database, Compose services, OTTO/Aftercool/OpenAI API и credentials не запускались/не читались. Это аудит исходников и ранее сохранённых verification notes, не повторная operational certification.

## 13. Ответы на девять ключевых вопросов

1. **Работает ли OttoRestart сейчас?** Да, частично: ограниченный ручной импорт и некоторые catalog/read/sync функции существуют; весь продукт нельзя считать готовым к публикации.
2. **Можно ли из файла провести EAN до source/factory результата?** Да, через импортный pipeline для поддерживаемого списка и выбранного аккаунта; результат зависит от OTTO/Aftercool read success.
3. **Создаётся ли готовая новая OTTO карточка?** Нет. Prepare API пока возвращает `409 OTTO_TARGET_PAYLOAD_NOT_BUILT` до source reads и резервирования EAN; полного DTO mapping нет.
4. **Можно ли пройти production QC из UI?** Нет. UI показывает кандидатов и позволяет создать local DRAFT; prepare/QC/approval flow ещё не открыт.
5. **Работают ли AI-названия/атрибуты?** Нет как production AI flow. Demo-only controls скрыты в production; Settings показывает backend status без запроса GPT. Ключ не читался и не использовался.
6. **Работает ли реальная Drip очередь?** Production UI читает schedules и durable job metadata; исполняется только import. Product submit не реализован.
7. **Может ли операция опубликовать товар в OTTO?** Текущий гарантированный путь закрыт: нет payload, нет worker submission allowlist и publish gate выключен. Текущие доказательства не дают основания называть publish работающим.
8. **Есть ли риск потерять зарезервированный EAN при отмене?** Код отмены теперь деактивирует lineage и освобождает только подтверждённую never-submitted reservation; результат пока не проверен интеграционно на PostgreSQL.
9. **Можно ли использовать `GPT_API_KEY`?** Compose умеет передать его в backend под `OTTORESTART_OPENAI_API_KEY`, но значение `.env` не инспектировалось, feature flag выключен по умолчанию, GPT-вызовов не было.


### Текущая готовность по подсистемам

| Подсистема | Статус | Основание |
|---|---|---|
| Manual import 3M/12M | `WORKING · LIMITED` | Durable job/read adapter, account-scoped input, bounded worker allowlist |
| OTTO/Aftercool source sync | `WORKING · LIMITED` | Выбранный товар; source sync не равен operation readiness |
| Factory/candidate projection | `WORKING · LIMITED` | Production projection; зависит от route/source evidence |
| Operation creation/prepare | `PARTIAL · BLOCKED` | Backend endpoint есть, target payload builder отсутствует |
| QC backend | `PARTIAL · BLOCKED` | Validator есть, readiness gap не закрывается |
| Review/QC UI | `PARTIAL · DRAFT ONLY` | Production candidate list + explicit local DRAFT; no QC/approval |
| EAN pool backend | `PARTIAL` | Eligibility/cancel fixes and missing-assignment fail-closed guard in source; read-only migration preflight authored, but database/integration checks not run |
| EAN pool UI | `API-BACKED · LIMITED` | Production/test routes use account-scoped pool API |
| Settings/AI | `STATUS API · DISABLED` | Safe backend status; no key in browser; GPT disabled |
| Queue/Drip UI/API | `READ-ONLY PROJECTION · LIMITED` | Durable job metadata shown; import is only enabled executor |
| OTTO product publish | `DISABLED · NOT READY` | Нет complete payload и enabled submission worker gate |
| OTTO ONLINE confirmation | `PARTIAL · NOT PROVEN E2E` | Polling/status components существуют, но нет подтверждённого end-to-end submit path |

## 14. Решение и безопасная последовательность

OttoRestart в текущем состоянии следует описывать как **production import/read subsystem с API-backed партиями, EAN pool, историей и read-only durable queue view**, но не как готовую систему подготовки/QC/публикации.

Следующая реализация должна начинаться с контрактов payload, revision/QC и data-scope/EAN defects. Сначала закрываются local-only preparation и UI/API wiring; затем проверяются миграции и concurrency в disposable PostgreSQL. Любые OTTO product writes, availability writes, scheduler activation и production pilot остаются отдельными gates с письменным разрешением и бизнес-подтверждением GTIN/прав повторного размещения.

Пошаговая задача OR-001…OR-028, контракты, миграции и уровни проверок описаны в [`OTTORESTART_COMPLETION_ROADMAP.md`](OTTORESTART_COMPLETION_ROADMAP.md).

## 15. Уточнение состояния реализации — 10.11.2026

После первоначального среза внесены изменения в исходники OttoRestart, но production-контейнеры и схема БД не обновлялись:

- Production routes подключены к API-backed batch, EAN-pool и history workspaces. Review/quality routes показывают source candidates; оператор может явно создать только локальный DRAFT.
- `/queue` подключён к read-only account-scoped projection `background_jobs`; UI не получает payload и не меняет состояния job.
- Legacy product writes остаются отвергнутыми на backend и скрыты/заблокированы в production UI. Demo-only defaults, AI title/category/attribute mocks и Drip buttons не показываются в production. Product source sync остаётся отдельным явным действием.
- Settings читает `/ai/status`; UI не принимает и не сохраняет ключ. `GPT_API_KEY` не читался и ни одного GPT-запроса не выполнялось. AI feature remains disabled by default.
- На момент code tranche migration 0018 была создана для source-scope uniqueness и безопасного повторного использования только deactivated EAN links и ещё не применялась. Позднее она применена к выбранной активной БД; см. раздел 18. No Celery, Beat, external API, or production publish operation was run during that code tranche.
- Static TypeScript check and Python syntax compilation passed; broader test suites were not run. Queue projection code is added but DB integration has not been exercised; validated OTTO target payload remains unresolved, so publication stays blocked.

## 16. Уточнение после fail-closed remediation — 10.11.2026

- `POST /operations/{id}/prepare` проверяет статический gate `TARGET_PAYLOAD_BUILDER_READY=false` после проверки write authorization/idempotency key, но **до** открытия DB connection и до OTTO/Aftercool adapters. Ответ: `409 OTTO_TARGET_PAYLOAD_NOT_BUILT`, с `sourceReadsPerformed=false` и `eanReserved=false`. Внутренний preparation service не используется HTTP-маршрутом до реализации builder.
- Следовательно, описанный в раннем срезе риск «prepare резервирует EAN и затем сохраняет revision с постоянным gap» закрыт для доступного HTTP API. Сам payload, подготовка content, target identity allocation, category-schema validator, manual revision UI и QC остаются незавершёнными.
- Контракт `POST /v5/products` в официальном OTTO API требует `productDescription`, `mediaAssets`, `pricing` и `compliance`; официальные best practices отдельно указывают на SKU/EAN/category/brand ID, legal attributes, форматы атрибутов и требования к изображениям. Код OttoRestart пока не содержит валидированного полного mapping из account-scoped source snapshot в этот DTO. Поэтому builder не включён и не собирает payload по догадке ([Products API](https://api.otto.market/docs/api-docs/), [Best Practices](https://api.otto.market/docs/about-the-api/best-practices/)).
- Проверены только официальные документационные страницы, не API аккаунтов. GPT API, OTTO, Aftercool, БД, миграции, тестовые suites, контейнеры, Beat и production writes не запускались. Значение GPT key не читалось.
- Python syntax compilation и `npx tsc --noEmit` проходили на предыдущем срезе; после текущего небольшого route guard повторно выполнена Python syntax compilation. Docker Compose validation не прошла: установленный Docker CLI не имеет Compose subcommand.
- Для текущего remediation `git diff --name-status` ограничен OttoRestart и `.codex`; рабочие изменения вне этого task не очищались и не staged/committed.

## 17. Scope migration preflight tooling — 10.11.2026

- Added [`backend/scripts/restart_scope_preflight.py`](backend/scripts/restart_scope_preflight.py) to inspect migration-0018 conflicts at schema head 0017 and rollback conflicts at head 0018. It also reports duplicate production operation identities, account/scope orphan references, and inconsistent EAN verification/reservation states.
- The script requires an explicit `OTTORESTART_PREFLIGHT_DATABASE_URL`; it does not load `.env` or fall back to application/migration DSNs. Its PostgreSQL transaction is repeatable-read/read-only, with aggregate JSON output only. It has no migration/apply behavior and excludes EAN/product identifiers, URLs, and secrets.
- Cancellation now also refuses to mark an operation `CANCELLED` when `new_ean` is missing but an active EAN lineage or pool reservation still belongs to that operation; the inconsistent identity must be reconciled first.
- Python source syntax compiled. The script was not run against any database; therefore it provides no evidence about actual conflicts or migration safety. Disposable schema-0017 and post-0018 snapshots remain required before an operator considers applying or rolling back 0018.
- No GPT, OTTO, Aftercool, database, migration, container, Beat, production operation, or test-suite call occurred. The GPT key was not inspected.

## 18. Операционное обновление schema 0018 — 10.11.2026

> Срезы разделов 1–18 исторические. Текущее состояние на 11.11.2026 описано в разделе 19 ниже и имеет приоритет при расхождениях.

- По явному запросу пользователя выполнен read-only preflight migration 0018 на выбранной OttoRestart БД со schema head 0017: 0 migration-blocking groups и 0 rollback-blocking groups. Отдельно найдено 5 legacy JV EAN pool rows в RESERVED/USED без reservation owner; эти строки не менялись.
- Через `python /app/migrate.py upgrade` применена только migration 0018. `python /app/migrate.py check` подтвердил schema head `0018/0018` и valid checksums. SQL меняет scoped unique indexes/constraint; новых таблиц нет.
- Перезапущен только `otto_restart_backend`; `GET /api/restart/health` вернул HTTP 200, Uvicorn сообщил о завершённом старте. BFF без browser session вернул ожидаемый 401; аутентифицированный экран отдельно не подтверждён — пользователь может обновить страницу в своей сессии.
- Disposable upgrade/downgrade/concurrency suite не выполнялась. Worker, dispatcher, Beat, GPT, OTTO и Aftercool не запускались/не вызывались; listing writes не делались.

## 19. Обновление forensic-аудита — 11.11.2026

### 19.1. Срез и ограничения

Повторно прочитаны актуальные OttoRestart frontend/BFF, backend services/jobs, custom migrations и относящиеся к ним offline-тесты. Это аудит исходников, а не подтверждение состояния запущенных контейнеров или рабочей БД. OttoFlow не изменялся. Не выполнялись OTTO/Aftercool/OpenAI запросы, production-записи, миграции, dispatch/worker/Beat, публикация или операции с EAN.

Запущены frontend unit-тесты с local fake dependencies: node --test tests/*.test.mjs — 28 passed, 0 failed на Node v24.19.0. CI настроен на Node 20; это не заменяет CI/build certification. Backend suite не запускался, поскольку disposable PostgreSQL не поднимался и tests нельзя направлять на пользовательскую БД. Runtime, брокер, OTTO payload acceptance и ONLINE не проверялись.

### 19.2. Актуальная карта

Next.js production UI → BFF/auth/CSRF → FastAPI → account-scoped services → PostgreSQL sources/candidates/edits/revisions/EAN/outbox → Celery. Штатный worker registry включает import.process, drip.release_item, otto.submit_product. Producer-ы также создают discovery, availability, OTTO task-poll и ONLINE jobs, не включённые в штатную allowlist/executor map.

Каталог теперь использует bounded pagination, optional/cached metrics и abort устаревших catalog fetch. Это исправляет часть прежних проблем каталога, но не account-switch race в отдельных списке партий и истории.

### 19.3. Текущий реестр дефектов

#### AUDIT-REFRESH-001 — OTTO post-submit poll/ONLINE jobs не исполняются

Severity HIGH; confidence Confirmed по коду. Местоположение: backend/jobs/dispatcher.py:19-27; backend/jobs/celery_app.py:132-171; backend/services/submission_service.py:955-963; backend/services/otto_processing_service.py:30-31.

Доказательство/root cause: accepted HTTP 202 создаёт otto.poll_product_task, затем otto.confirm_online; handlers существуют, но executor map и allowlist содержат только import.process, drip.release_item и otto.submit_product. Impact: принятая отправка может остаться в локальном OTTO_PROCESSING без подтверждения marketplace status; EAN/reconciliation остаются неопределёнными. Repro: accepted 202 через fake adapter в disposable PostgreSQL и проверить, что poll job не выбирается штатным dispatcher. Fix: закрыть producer→executor→allowlist путь с fencing/retry limits. Regression risk: повторная доставка может запустить параллельный polling или повторный state transition. Verification: isolated 202→poll→result→marketplace readback→ONLINE/terminal error.

#### AUDIT-REFRESH-002 — discovery/availability jobs без штатных исполнителей

Severity HIGH для scheduled discovery, MEDIUM для availability; confidence Confirmed по producers и allowlist. Местоположение: backend/services/discovery_scan_service.py:165; discovery_service.py:686; candidate_enrichment_service.py:73,155; availability_service.py:164-213; jobs/dispatcher.py:19-27; jobs/celery_app.py:132-142.

Доказательство/root cause: создаются discovery.scan, discovery.enrich/reconcile и availability jobs, но эти типы отсутствуют в executor map/allowlist. Periodic tick лишь ставит discovery jobs. Impact: автоматическая обработка может накопить pending outbox rows. Repro: producer/allowlist cross-reference либо synthetic job с fake broker в disposable среде. Fix: единый registry jobs и CI проверка producer coverage; внешние writes оставить под отдельным gate. Regression risk: добавление availability job может включить OTTO write. Verification: fake broker/DB test для разрешённых и запрещённых типов, retry и terminal states.

#### AUDIT-REFRESH-003 — payload builder включён без полного schema-контракта и source mapping

Severity HIGH; confidence Confirmed для расхождения кода, OTTO acceptance не проверялась. Местоположение: backend/services/preparation_service.py:28-30,354-378,391-418; qc_service.py:314-437; submission_service.py:115-182; app.py:1731-1748.

Доказательство/root cause: комментарий требует schema-validated DTO, но TARGET_PAYLOAD_BUILDER_READY=True. Builder вручную копирует поля, не валидирует целиком target schema. В предоставленном V5 примере mediaAssets содержит filename, а evidence извлекается только из url/imageUrl/href — добавляется IMAGES_MISSING. Там же shipping_profile_id, тогда как builder читает logistics — добавляется LOGISTICS_MISSING. Impact: этот source snapshot не становится готовым; это не доказательство фактического OTTO rejection. Repro: локальная fixture ответа пользователя, inspect readinessGaps/content.images/target JSON без POST. Fix: versioned target DTO по официальному контракту, явное преобразование source fields и fail-closed gate. Regression risk: чрезмерная схема заблокирует допустимые категории/медиа. Verification: JV/XL schema fixtures и negative cases.

#### AUDIT-REFRESH-004 — production UI не вызывает prepare/QC

Severity HIGH workflow blocker; confidence Confirmed по caller graph. Местоположение: components/production-operations-workspace.tsx:68-99,159-169; features/api/client.ts:270-304; app/review/page.tsx; app/quality-control/page.tsx.

Доказательство/root cause: UI создаёт локальный DRAFT и показывает ожидание payload; клиент имеет prepareOperation/runOperationQc, но production UI эти методы не вызывает. Одновременно backend flag TARGET_PAYLOAD_BUILDER_READY=True, поэтому текст UI не является серверным gate. Impact: оператор не может продолжить DRAFT через экран; прямой authorized API path отличается от UI. Repro: создать DRAFT через UI и проверить mock network calls. Fix: после schema/backend gates добавить revision→prepare→QC с version/idempotency. Regression risk: повторный вызов может создать revision или reserve EAN. Verification: isolated UI/API flow и stale-version tests.

#### AUDIT-REFRESH-005 — EAN-only import использует EAN как SKU

Severity MEDIUM/HIGH в зависимости от доли EAN!=SKU; confidence Confirmed при несовпадении. Местоположение: backend/services/import_pipeline.py:738-743,767-781,796-803.

Доказательство/root cause: запрос использует sku or ean or raw_identifier, затем status/product/prices должны подтвердить то же значение как SKU. Impact: EAN-only list не разрешает variation с отличным SKU; это не опровергает сообщённый успешный XL import. Repro: fake ответ с одним EAN и отличным SKU. Fix: типизировать identifier и разрешать EAN в единственный account-scoped canonical SKU. Regression risk: ambiguous lookup свяжет sibling variation. Verification: offline fixtures EAN=SKU, EAN!=SKU, zero/multiple matches.

#### AUDIT-REFRESH-006 — TITLE_ONLY выглядит валидным, но operation требует EXACT_CODE

Severity MEDIUM; confidence Confirmed поведение, решение о manual review зависит от политики. Местоположение: import_pipeline.py:767-770,861-867; discovery_service.py:641-667; operation_service.py:510-512; preparation_service.py:208-209.

Доказательство/root cause: import принимает TITLE_ONLY как VALID, discovery открывает/маршрутизирует episode, но operation/preparation требует EXACT_CODE. Impact: оператор видит кандидата, который не проходит workflow, и может принять title match за exact identity. Repro: local title-only fixture и eligibility checks. Fix: отдельный review-only статус; exact gate не ослаблять автоматически. Regression risk: ложная identity при ослаблении gate. Verification: fixtures import→discovery→operation.

#### AUDIT-REFRESH-007 — Aftercool identity diagnostic теряется

Severity MEDIUM; confidence Confirmed. Местоположение: product_sync_service.py:263-272,320-337,491-533.

Доказательство/root cause: aftercool_issue вычисляется, но aftercoolIssue получает price_review_reason. При валидной OTTO цене и неподтверждённой Aftercool identity причина может быть null. Impact: скрывается причина отсутствия подтверждённой Aftercool product/factory. Repro: fake valid OTTO price + identity mismatch. Fix: раздельные source identity и price diagnostics. Regression risk: клиент ошибочно блокирует sole-source pricing. Verification: fixtures fallback/mismatch/missing Startpreis/exact match.

#### AUDIT-REFRESH-008 — account-switch race в batches/history; history только 50 событий

Severity MEDIUM; confidence Confirmed. Местоположение: components/batch-import-workspace.tsx:31-47,165-171; components/restart-history-workspace.tsx:16-27,39-43.

Доказательство/root cause: JV/XL responses не отменяются и не сверяют request generation; поздний ответ может перезаписать список под текущим account label. History всегда page 1/pageSize 50 без pagination. Impact: вводящий в заблуждение account list; события после первых 50 недоступны в этом UI. Repro: задержать fake JV request дольше XL и вернуть 51 event. Fix: AbortController/generation guard, очищать при scope change, добавить pagination. Regression risk: отменённый запрос не должен очищать новый state. Verification: deterministic response order and page boundary tests.

#### AUDIT-REFRESH-009 — BFF write requests не имеют собственного timeout

Severity MEDIUM reliability; confidence Confirmed. Местоположение: app/api/backend/[...path]/route.ts:87-110,118-120.

Доказательство/root cause: AbortController timeout назначается только GET. Impact: зависший write оставляет UI в ожидании; при обрыве после backend commit результат браузеру может быть неизвестен. Repro: local stub backend с delayed POST. Fix: bounded timeout вместе со stable idempotency и status reconciliation; не делать слепой retry. Regression risk: abort BFF не отменяет backend transaction. Verification: fake slow POST до/после mock commit и recovery UX.

#### AUDIT-REFRESH-010 — factory primary key не включает data_scope

Severity MEDIUM при пересечении legacy/production IDs; confidence механизма Confirmed, rows рабочей БД не проверялись. Местоположение: migrations/0003_factory_completion_and_candidates.sql:4-15; 0004_legacy_unverified_scope.sql:56-57; services/factory_service.py:54-64.

Доказательство/root cause: PK остаётся (account,id), 0004 добавляет unique (id,account,data_scope), но не заменяет PK. INSERT использует ON CONFLICT (account,id,data_scope), поэтому совпадающий PK другого scope может вызвать конфликт. Impact: factory projection transaction откатится при совпадении; наличие такого ряда неизвестно. Repro: только disposable PostgreSQL с legacy row и production insert. Fix: согласовать PK/unique/FK с scope либо безопасно merge до insert. Regression risk: изменение ключа затрагивает FK/completion state. Verification: disposable migration upgrade/downgrade с двумя scopes.

#### AUDIT-REFRESH-011 — backend test expectations отстают от кода

Severity MEDIUM CI reliability; confidence Confirmed статически, backend tests здесь не запускались. Местоположение: backend/tests/test_migration_runner.py:41-44; backend/tests/test_celery_transport.py:64-80; migration 0020; jobs/dispatcher.py:19-27.

Доказательство/root cause: migration test ждёт версии 1..19, хотя есть 0020; Celery tests ожидают только import.process, а текущий allowlist имеет три типа. Impact: stale assertions могут валить CI и снижать сигнал новых регрессий. Repro: backend test suite только на CI-style disposable PostgreSQL/Redis. Fix: синхронизировать ожидания, сохранив проверки unsupported job producers. Regression risk: механически обновить assertion и скрыть lifecycle bug. Verification: полный suite и migration chain в disposable services.

#### AUDIT-REFRESH-012 — source salePrice может скопироваться в target pricing

Severity MEDIUM; confidence копирования при наличии поля Confirmed, наличие поля в актуальном product response и target schema не проверялись. Местоположение: preparation_service.py:403-408,415.

Доказательство/root cause: target pricing spread-ит всю source map с заменой standardPrice, поэтому дополнительные поля остаются. Impact: при наличии salePrice promotion может перейти без явного решения; реальный OTTO эффект неизвестен. Repro: mock pricing с standardPrice/msrp/salePrice. Fix: строить target pricing из allowlist schema. Regression risk: убрать допустимое поле конкретной категории. Verification: schema fixtures и assertion на promotion field.

### 19.4. Актуальные статусы реализации

| Функция | Статус и граница доказательств |
|---|---|
| Импорт 3M/12M | Account-scoped durable code; пользователь подтвердил успешный XL import. EAN-only EAN!=SKU остаётся conditional. Runtime не повторялся. |
| ProductDrawer edits | Есть account/version-scoped server save; live PostgreSQL persistence не проверялась этим аудитом. |
| Цена | Aftercool/Afterbuy сравнивает две положительные подтверждённые цены; при одной использует её. JV −€10, XL −€20, затем ,99. Direct OTTO использует OTTO standardPrice. |
| Большой каталог | Bounded pagination/уменьшение лишних metrics/details есть; production нагрузка 10k/50k не измерялась. |
| AI | Production UI подключает explicit suggestions с operator review; provider не вызывался. |
| Prepare/QC UI | Backend/client методы есть, production UI их не вызывает. |
| Target payload | Builder есть и flag True; полный schema contract не подтверждён. |
| Discovery/availability | Producer-ы есть, стандартный executor registry неполный. |
| OTTO submit | Submit разрешён; 202 создаёт poll job, который allowlist не исполняет. POST не выполнялся. |
| Demo queue/settings | Production использует API-backed workspace; in-memory queue остаётся demo-only. |

### 19.5. Top 10 наиболее опасных текущих проблем

1. Post-202 polling/ONLINE dead end (001). 2. Discovery/availability jobs без executor (002). 3. Payload без полного schema contract (003). 4. Нет UI prepare/QC пути (004). 5. EAN-only EAN!=SKU import (005). 6. Условная factory scope collision (010). 7. Aftercool diagnostic теряется (007). 8. TITLE_ONLY misleading state (006). 9. Account response race/history limit (008). 10. Нет BFF write deadline (009). Пункты 5–10 условны или средней severity и не равны первым четырём release blockers. Дополнительный salePrice риск — 012.

### 19.6. Десять архитектурных улучшений

1. Registry producer→job schema→executor→allowlist→retry/terminal state с CI проверкой полноты.
2. Замкнуть submit 202→task poll→result→marketplace status→ONLINE и reconciliation.
3. Versioned target DTO/schema с account/category constraints для builder, QC и submission.
4. Разделить source evidence, operator edits, target price, payload и marketplace state; не копировать произвольные JSON maps.
5. Типизировать SKU/EAN input и account-scoped canonical resolution.
6. Явно оставить TITLE_ONLY review-only до дополнительного подтверждения.
7. Общий AbortController/request-generation паттерн для account-scoped reads.
8. Связать UI actions с API readiness gates и version conflicts.
9. BFF write timeout вместе с idempotency/status reconciliation; без слепого retry.
10. Согласовать migration keys/scopes/FK и проверять upgrade/downgrade только на disposable PostgreSQL.

### 19.7. Production readiness и remediation

Вердикт: OttoRestart пока нельзя считать готовым к публикации товаров или доказанно выдерживающим десятки тысяч товаров end-to-end. Import, paginated catalog, server edits и часть explicit AI proposals реализованы; полный listing lifecycle и масштабная нагрузка не подтверждены. Основные blockers — target schema/payload, executor coverage, post-202 reconciliation, UI preparation/QC wiring и миграционные/scoped-key tests. По сохранённому checkpoint product-write flag ранее был включён по прямому запросу пользователя; в этом аудите runtime flags не перечитывались, handler не запускался и ни один job не ставился. Пользователь оставил ручной старт Drip Feed за собой. Это обновление не разрешает дополнительный запуск или создание job.

| Этап | Приоритет | Критерий готовности |
|---|---|---|
| R0 | P0 | До ручного старта Drip Feed сверить operator-controlled runtime gates с текущими blockers; в этом аудите job не запускать. |
| R1 | P0 | Полный target schema и media/logistics/category mapping; invalid DTO блокируется до reserve/write. |
| R2 | P0 после R1 | Poll/ONLINE, discovery, availability покрыты isolated DB/fake broker tests. |
| R3 | P0 после R1 | UI revision/prepare/QC проходит idempotency/version gates без shortcut к READY. |
| R4 | P1 | Исправлены EAN/SKU, TITLE_ONLY и Aftercool diagnostics с offline edge fixtures. |
| R5 | P1 | Factory scoped keys и backend tests проверены disposable PostgreSQL upgrade/downgrade. |
| R6 | P2 | Account races, history paging и неопределённые BFF writes имеют recovery UX. |
| R7 | P2 | Seeded 10k/50k isolated benchmark подтверждает latency, memory, SQL/API quotas и retry budget. |

### 19.8. Итоговый инженерный вердикт

- Реально есть: account-scoped import/source sync code, paginated catalog, server ProductDrawer edits, explicit AI suggestions с ручным review, durable outbox и production/demo separation.
- Частично: target payload, QC/submission, Drip release/submit, discovery scheduling и availability; producer-ы, handlers и UI не образуют полный end-to-end путь.
- Не завершено по текущей карте: post-202 polling/ONLINE confirmation, scheduled discovery/availability execution, production UI preparation/QC.
- Условные риски EAN!=SKU, legacy/production factory ID, salePrice propagation и зависший BFF write не доказывают уже повреждённые данные/карточки.
- Перед публикацией закрыть target schema, async reconciliation, job registry и UI/backend gate mismatch; затем выполнить isolated migration/key tests.
- Можно отложить косметику до закрытия lifecycle/schema.
- Source evidence, решение оператора и подтверждённый marketplace state должны оставаться различимыми; наличие job или HTTP 202 не доказывает ONLINE.
