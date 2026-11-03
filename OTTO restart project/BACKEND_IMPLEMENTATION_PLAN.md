# OttoRestart — окончательный backend implementation plan

Дата: 2026-10-30. Статус: архитектура согласована; реализация этого плана не начата.
Исполнитель: Terra Medium, по одному этапу за рабочую сессию.

## 1. Основание и обязательные решения

План основан на фактическом read-only аудите, а не на прежних отметках «COMPLETE». В Restart уже работают отдельные FastAPI/PostgreSQL и часть Library. Каталоги, AI, QC, Drip Feed и выявление во многом являются симуляцией. При аудите путь `get_status()`, используемый импортом, дал 53 несовпадения на сохранённых 100 ответах OTTO. В БД найдены 3 выделенных EAN, совпадающих с EAN других исходных товаров.

Принятые пользователем решения имеют приоритет:

1. Все существующие записи сохраняются как `LEGACY_UNVERIFIED`. Они не участвуют в production без отдельной проверки.
2. `PUBLISHED` требует подтверждённой обработки конкретного SKU в OTTO **и** `marketplace-status=ONLINE` того же SKU в том же аккаунте. HTTP 202 означает только принятие запроса.
3. QC автоматический при прохождении обязательных детерминированных проверок. Ошибки, противоречия и неподтверждённые AI-факты отправляются на ручную проверку. Ручное решение не обходит обязательные правила GTIN.
4. Следующий бизнес-Restart исходного товара требует ручного просмотра истории и отдельного разрешения. Технический retry продолжает ту же операцию с тем же GTIN и payload.
5. Aftercool получает только авторизованный read-only API связей. Удаление и деактивация OTTO не входят в v1.
6. OttoFlow нельзя изменять. Разрешены чтение и копирование отдельных проверенных решений в Restart; runtime-импорты его БД, моделей, очереди, Redis и настроек запрещены.
7. Максимально сохраняются существующие таблицы и модули Restart. Готовность интерфейса не считается доказательством работоспособности backend.

Этот документ разрешает проектирование. Он не является разрешением на применение миграций к рабочей БД, подключение live API, отправку товаров или включение расписания.

## 2. Конечная архитектура

```text
Next.js /restart — интерфейс и BFF с авторизацией
             |
       FastAPI Restart — DTO, права, бизнес-сервисы
             |
       PostgreSQL Restart — источник состояния и истории
             |
       background_jobs — транзакционные задания/outbox
             |
       собственные Celery + Redis + dispatcher/beat
             |
       отдельные адаптеры OTTO / Aftercool / OpenAI
```

- Вся реализация и собственная конфигурация находятся внутри `OTTO restart project/`.
- PostgreSQL — источник состояния операций, очереди, расписания и результатов. Celery/Redis доставляют задания; потеря брокера не должна терять бизнес-задачи.
- Товарные данные читаются через backend. Клиент не вычисляет производственные статусы и не создаёт GTIN.
- Режимы: `LEGACY_READ_ONLY`, `DEMO`, `PRODUCTION`, `TEST`. DEMO/TEST используют отдельные БД/процессы и явные fixture-adapters. Production не импортирует mock-модули.
- Нынешняя БД сохраняется. Её существующие записи получают `data_scope=LEGACY_UNVERIFIED`; нормальные production-запросы выбирают только `PRODUCTION`. Смена scope возможна только через проверяемую административную операцию с аудитом.
- Все сетевые флаги по умолчанию выключены: `OTTO_READ_ENABLED`, `AFTERCOOL_READ_ENABLED`, `OPENAI_ENABLED`, `OTTO_PRODUCT_WRITE_ENABLED`, `OTTO_AVAILABILITY_WRITE_ENABLED`, `DISCOVERY_SCHEDULE_ENABLED`.
- Никаких сетевых вызовов из startup, миграций, GET каталога или локального preview. Авторизация внешних систем выполняется только внутри разрешённого адаптера.
- HTTP-роуты размещаются в `backend/routers/`, DTO — `backend/schemas/`, SQL-доступ — `backend/repositories/`, правила — `backend/services/`, исполнители — `backend/jobs/`, внешние клиенты — `backend/integrations/`. `app.py` постепенно остаётся точкой сборки; отдельный большой рефакторинг не требуется.

## 3. Контракт данных и состояний

### 3.1. Что переиспользуем

| Существующая таблица | Production-роль и необходимые расширения |
|---|---|
| `source_products` | Стабильная идентичность исходного товара, связь с Aftercool/фабрикой, подтверждённые SKU/EAN/MOIN и метаданные источника. Не новый полный каталог ERP. Добавить scope, canonical identity, verification/provenance и версии источника. |
| `ean_pool` | Единственный реестр новых GTIN. Добавить происхождение, evidence, допустимость, владельца резерва и версию. PK EAN остаётся глобальным внутри Restart: один номер нельзя выделить JV и XL одновременно. |
| `ean_links` | Неизменяемая lineage исходный товар → новый SKU/EAN → операция. Разделять назначение идентификатора, отправку и подтверждённую публикацию. |
| `restart_operations` | Одна бизнес-попытка Restart. Добавить номер попытки, episode, версию, разрешение повторной попытки и ссылки на подтверждения. Старые READY/PUBLISHED не переносятся как доказанные факты. |
| `restart_batches`, `restart_items` | Партия и постоянные элементы Drip Feed, выбранные операции/ревизии, schedule, lease и статусы. Отдельные дублирующие таблицы планов не создавать. |
| `import_runs` | История запусков `FILE_IMPORT`, `INCREMENTAL_SCAN`, `KNOWN_SKU_RECONCILIATION`; параметры, coverage, cutoff, статистика и ошибки. Для API filename становится необязательным. |
| `restart_factories` | Проекция актуального состояния, generation набора кандидатов, время подтверждения актуальности и последнего завершения. |
| `factory_completion_history` | События завершения по generation с набором episode/operation IDs и подтверждениями. Повторный пересчёт не добавляет одинаковое завершение. |
| `restart_events` | Append-only аудит: actor, account, scope, сущность, версия, причина, correlation ID и ссылки на evidence. |
| `restart_idempotency_keys` | Атомарные ключи `(account, principal, action, key)` и hash запроса, включая корректную обработку параллельного первого обращения. |
| `restart_ean_registry`, `otto_library` | Сохранённые legacy-данные. После перехода не являются самостоятельными production-хранилищами; старые endpoint-адаптеры обращаются к новым сервисам. |

Минимальные новые таблицы: `import_rows`, `otto_status_observations`, `candidate_episodes`, `discovery_cursors`, `background_jobs`, `gtin_evidence`, `operation_revisions`, `qc_results`, `restart_authorizations`, `submission_attempts`.

Состояния run: `PENDING`, `RUNNING`, `STAGED`, `APPLYING`, `SUCCEEDED`, `PARTIAL`, `FAILED`, `CANCELLED`. Импорт может завершиться PARTIAL с достоверными успешными строками и отдельно сохранёнными ошибками. Скан применяет состояние только при полном SUCCEEDED; частичные результаты не меняют активное состояние и watermark. Для одного аккаунта одновременно работает один scan/apply; запуск с новым idempotency key при уже активном scan возвращает 409 со ссылкой на существующий run.

Обязательные ограничения: account/scope в составных FK; уникальная canonical identity production-товара; одна открытая episode на `(account, observed_sku)`; одна незавершённая операция на исходный товар; один владелец GTIN; один активный элемент очереди для ревизии операции. Новые ограничения не требуют удаления конфликтных legacy-строк: конфликты сохраняются в отчёте и остаются вне production.

### 3.2. Обнаружение и идентичность

- Основные признаки: полные нормализованные коды `602016 - Nonseller` → 3M и `602016 - LOW_PERFORMER` → 12M. Допускается только нормализация Unicode, регистра и пробелов вокруг разделителя; поиск подстроки/общего `602016` запрещён.
- Проверять **все** `errors[]`. Полные фразы про отсутствие Nettoumsatz — дополнительная проверка и отдельный `TITLE_ONLY` fallback. «Drei Monaten» без причины Nettoumsatz недостаточно.
- Результаты: `EXACT_CODE`, `TITLE_ONLY`, `AMBIGUOUS`, `NONE`, `UNCHECKABLE`. Противоположные признаки в одном/разных errors дают `AMBIGUOUS`; неизвестные коды сохраняются. `TITLE_ONLY` требует ручного подтверждения классификации перед production-подготовкой.
- Не требовать INACTIVE. HTTP/JSON-ошибка, несовпадение SKU и отсутствие необходимых полей — `UNCHECKABLE`, не снятие ограничения.
- SKU, исходный EAN и новый GTIN — разные поля. Для EAN-импорта можно попробовать запрос по этому идентификатору как предполагаемому SKU, но связь подтверждается ответом product API с правильным EAN. 404 такого запроса не доказывает отсутствие товара с иным SKU; результат остаётся `IDENTITY_UNRESOLVED`.
- В Aftercool identity строится по подтверждённому product ID и фабрике; прямой OTTO-товар — по исходному SKU выбранного аккаунта. Неоднозначный EAN не склеивается по первой найденной строке.
- Начальный импорт создаёт `BASELINE` episodes и не увеличивает «Новые N». Последующий импорт явно выбирает `BASELINE_EXTEND` либо `INCREMENTAL_IMPORT`; файл является источником идентификаторов, не доказательством текущего ограничения.
- Повторное наблюдение ничего не дублирует. Смена 3M → 12M при непрерывном ограничении сохраняется в истории той же episode. Новая episode возникает после подтверждённого снятия и последующего нового ограничения.
- Новое ограничение нового OTTO EAN связывается через `ean_links` с тем же исходным товаром. История Restart проверяется по всему этому lineage, а не только по текущему SKU.
- Совпадение с legacy lineage/операцией не доказывает прошлую публикацию, но создаёт `HISTORY_UNVERIFIED`. Исключение legacy из production-метрик не разрешает автоматически считать такую попытку первой. Перед подготовкой нужен отдельный аудируемый разбор истории: подтверждённый тест, подтверждённая рабочая попытка или недостаточность доказательств.

### 3.3. Операции, GTIN и QC

Основной путь операции:

```text
DRAFT → PREPARING → QC_PENDING → READY → QUEUED → SUBMITTING
      → OTTO_PROCESSING → AWAITING_ONLINE → PUBLISHED
```

Дополнительные состояния: `NEEDS_REVIEW`, `RECONCILIATION_REQUIRED`, `FAILED`, `CANCELLED`. Ошибка задания и номер HTTP-повтора хранятся отдельно от номера бизнес-попытки.

- GTIN: `AVAILABLE → RESERVED → USED`; `QUARANTINED` блокирует использование. USED означает подтверждённое принятие/использование GTIN для target SKU, а не ONLINE. При неопределённом результате отправки резерв блокируется, а не возвращается в AVAILABLE.
- Резерв связан с operation ID, а не browser session. После рестарта процесса резерв не теряется. Переход обратно в AVAILABLE разрешён только для никогда не отправленного GTIN после проверяемой отмены и освобождения ссылок; сомнительные коды — QUARANTINED.
- Проверки GTIN: формат/check digit, источник/право использования, назначение именно данному физическому товару, допустимость сценария OTTO, отсутствие конфликтов в обоих аккаунтах, legacy и lineage. Свободный номер и ответ GET 404 не доказывают допустимость нового GTIN.
- Evidence имеет источник, hash/защищённую ссылку на документ, проверившего actor, account/product/GTIN scope, основание решения и срок действия либо явную отметку бессрочности. Общего checkbox «пул проверен» недостаточно для назначения GTIN неизменённому товару.
- Не предполагается универсальный API разрешения нового GTIN: если доказательств недостаточно, состояние `GTIN_REVIEW_REQUIRED` блокирует публикацию. Ручное утверждение QC не заменяет отдельное подтверждение допустимости GTIN.
- Согласованная цена сохраняется: текущий `standardPrice.amount` старого OTTO SKU в нужном аккаунте, JV − 1000 cents, XL − 1500 cents. Без Aftercool/Excel/salePrice fallback и повторного вычитания скидки. Неположительная/неподтверждённая база блокирует операцию. Редактирование цены создаёт новую ревизию; v1 production не обходит утверждённую формулу скрытым override.
- Контент, категория, атрибуты, изображения, цена и fulfillment сохраняются ревизиями. Существенное изменение инвалидирует QC и старый queue item. Payload в очереди неизменяем.
- AI предлагает данные по проверенным источникам и schema категории. Размеры, материалы, сертификаты и прочие факты без источника не придумываются. Собственный confidence модели не является доказательством достоверности.
- QC проверяет GTIN/evidence, связь SKU/EAN/account, актуальное ограничение, разрешение попытки, категорию и обязательные/LEGAL атрибуты, допустимые значения, название ≤70 символов и его изменение, изображения, цену, stock/shipping и отсутствие дублирующей отправки.
- `PASSED_AUTO` возможен только при полном прохождении правил. Недостающие факты/неоднозначности → `MANUAL_REQUIRED`; нарушение обязательного правила → `BLOCKED`. После ручного исправления выполняется тот же детерминированный QC.
- Перед первой фактической отправкой заново прочитать старую OTTO standardPrice и проверить актуальность evidence/источника. Изменившаяся база или версия блокирует отправку старого payload; никаких тихих правок снимка.

### 3.4. Фабрики и счётчик новых

- `NOT_COMPLETE`: есть известные актуальные кандидаты, требующие обработки/разбора, либо незавершённые необходимые операции.
- `UNKNOWN`: нет известной незавершённой работы, но полнота/актуальность coverage не подтверждена. Пустой ответ либо отсутствие кандидатов в текущем UI не доказывают завершение.
- `COMPLETE`: coverage подтверждён и актуален; все необходимые текущие кандидаты имеют подтверждённый успешный Restart, либо ограничение явно снято и зафиксировано отсутствие необходимости в Restart.
- Без достоверного начального coverage автоматический COMPLETE запрещён. Coverage относится к объявленному набору 3M/12M, а не автоматически ко всему каталогу фабрики. Полный baseline можно подтвердить проверенным полным OTTO-экспортом; полный API-скан этим планом не разрешён.
- Актуальность production coverage по умолчанию — 16 суток: 14-дневный период плюс 48 часов допуска. Неудачный скан не обновляет её.
- «Новые N» — episodes с origin NEW, которые ещё требуют обработки. Уже опубликованные, явно разрешённые или закрытые episodes не входят в N; история их обнаружений сохраняется.
- Появление новых кандидатов атомарно увеличивает factory generation и снимает COMPLETE; прежний `last_completed_at` сохраняется. Новое завершение создаёт одно событие для новой generation.
- Импорт/применение результатов и завершение используют одну блокировку строки фабрики. Событие завершения не должно потерять кандидатов, добавленных параллельно.

## 4. Production API

Все normal endpoints работают с PRODUCTION и обязательным account. Legacy доступен отдельно только на чтение. DTO типизированы; ошибки имеют `{code, message, requestId, retryable, details}` без секретов.

| Группа | Контракт |
|---|---|
| Импорт | Сохранить `/imports/preview`, `/imports/upload`, `/imports/paste`, `/imports/runs`; добавить run detail и paginated rows. Commit возвращает HTTP 202 + run ID, preview не пишет в БД и не вызывает API. |
| Товары | `GET /products`, `GET /products/{id}`: source, factory, period, status, query, pagination. Редактирование только draft текущей operation через optimistic version. |
| Операции | Создание из candidate IDs, prepare, draft patch, QC, ручная фиксация review, история ревизий и отдельное разрешение повторного Restart. Все команды идемпотентны. |
| Фабрики | Сохранить list/history. `/factories/{id}/complete` становится запросом проверяемого пересчёта; принудительно выставить COMPLETE нельзя. |
| EAN/Library | Единственные list/import/reserve/quarantine сервисы на `ean_pool`; links/history/lookup. Прямой запрос клиента не может назначить произвольный новый EAN. |
| Drip Feed | `/batches` и `/batches/{id}/items`, preview расписания, start/pause/resume/cancel/retry; `/queue` читает реальные элементы и попытки. |
| Обнаружение | `/scans` create/list/detail с явным fromDate/ограниченным scope; серверные флаги и права проверяются до создания задания. |
| Интеграция Aftercool | `/library/lookup` с отдельным M2M principal и allowed accounts; только GET. Возвращать linked/online EAN отдельно и evidence времени проверки, не называть резерв ONLINE. |

Старые write-форматы, обходящие новые проверки, в production отклоняются с явным кодом. URLs страниц сохраняются; frontend DTO/client обновляются вместе с соответствующим контрактом. Multipart прокси передаёт без преобразования в text, разрешённые служебные заголовки — явно.

Авторизация: BFF формирует подписанный internal principal с TTL 60 секунд, audience Restart API, user ID, allowed accounts, permissions и request ID. FastAPI проверяет подпись и права на каждом запросе. В v1 достаточно одного настроенного оператора, но его actor ID записывается во все решения; «ручное разрешение» означает отдельную аудируемую команду, а не обязательно второго сотрудника. M2M-ключ Aftercool отдельный, secret provider хранит hash и allowed accounts, scope только `library:read`; ротация/отзыв — сменой доверенного серверного key set. Клиентские account/actor-заголовки без подписи не доверяются. Cookie mutations дополнительно требуют CSRF/Origin; production не запускается с известными default password/secret.

## 5. Правила фонового исполнения и интеграций

- Задание создаётся в одной PostgreSQL-транзакции с изменением сущности. Dispatcher отправляет только ID; дубликат доставки безопасен. Есть lease, heartbeat, число попыток, next_run_at и причина ошибки.
- Длительные сетевые операции выполняются вне DB-транзакций. Перезапуск worker не создаёт новую operation/новый GTIN. Блокировки берутся в постоянном порядке: account apply lock для импортов/сканов → фабрики по ID → исходные товары → операции → пул.
- Обычный импорт: максимум 20 MB / 10 000 строк; обработка chunks ≤500, файл/артефакт приватный. Лимит размера проверяется потоком; большой каталог в память не загружается.
- Чтение OTTO: OAuth/token cache отдельно по аккаунтам; один refresh после 401; ограниченные повторы GET на 429/5xx/network, Retry-After имеет приоритет. Долгое ожидание оформляется next_run_at, не sleep worker.
- Начальный лимит OTTO — не более 2 GET/sec суммарно, при этом не превышать актуальные server headers/документированную квоту. После измерений меняется конфигурацией. Aftercool/OpenAI имеют свои limiter и bounded concurrency.
- Aftercool возвращает `FOUND`, `NOT_FOUND_CONFIRMED`, `AMBIGUOUS`, `LOOKUP_ERROR`. NOT_FOUND возможен только после успешного полного ответа/поиска в правильном аккаунте; ошибка, неполная пагинация и отсутствие fixture не означают Direct OTTO.
- Price/display из Aftercool использует только Startpreis; изображение — подтверждённый GalleryURL/источник. Эти данные не заменяют OTTO pricing snapshot для Restart.
- Не передавать Bearer на произвольный href: разрешён только ожидаемый OTTO host/path. Секреты, cookies, Authorization и полные приватные документы не попадают в события/логи.
- POST `/v5/products` разрешён только для allowlisted операций с valid QC и GTIN. На пилоте — один SKU на submission. Заранее записывается attempt и payload hash; после ответа сохраняются task/self/succeeded/failed/unchanged links.
- Не применять общий автоматический retry POST при timeout/неопределённом 5xx: запрос мог быть принят. Такое состояние → `RECONCILIATION_REQUIRED`, GET-проверки и ручной разбор, если нет однозначного доказательства результата. Неподтверждённую идемпотентность OTTO не предполагать.
- HTTP 202 обрабатывается poller: дождаться завершения задачи, разобрать все страницы per-SKU succeeded/failed/unchanged. Общий state=done и отсутствие SKU в failed недостаточны без результата этого SKU. `unchanged` без предыдущего подтверждённого успеха не даёт PUBLISHED.
- Availability/delivery для **нового** SKU — отдельные подтверждаемые шаги. Stock, processing time и shipping profile берутся из подтверждённых данных/настроек аккаунта; фиксированный stock=20 из OttoFlow не копировать.
- ONLINE подтверждается отдельным свежим marketplace-status того же target SKU/account после обработки и необходимых fulfillment-шагов. Поздняя потеря ONLINE сохраняется как новое наблюдение и проблема текущей актуальности; исторический успех не стирается.

Границы копирования готового кода:

| Источник | Что допустимо перенести в Restart | Что не переносить |
|---|---|---|
| OttoFlow `backend/services/otto_client.py` | OAuth, разбор task URLs и HTTP DTO после account/error hardening | Присвоение Published по HTTP-коду, подавление ошибок failed-details как пустого массива |
| OttoFlow `aftercool_service.py`, `factory_scope.py` | Price parsing, извлечение источника, страницы ≤500, отказ при неполной выборке | Root DB/Redis/session globals, account fallback, мутации ProductMapping и pricing-политику OttoFlow |
| OttoFlow poller/executor | Протокол обработки OTTO task и per-SKU результатов | Его очередь, статусы, автоматический stock=20, availability расширение на старые товары |
| Restart demo review | Идею version/hash, invalidation и immutable snapshot | Fixtures, process-memory store и demo-успех как production evidence |

## 6. Инкрементальное обнаружение

1. Watermark отдельный для JV/XL; нельзя запускать запрос без fromDate как скрытый full scan.
2. Начальная точка — зафиксированный cutoff до начала подтверждённого baseline-импорта либо отдельно разрешённого ограниченного запуска. Исторические fixtures не создают актуальный production watermark.
3. `fromDate = last_successful_cutoff − 48h`. При успехе cutoff продвигается до времени начала запуска, не до максимального lastModified из ответа.
4. Обрабатывать все страницы, включая статусы без 3M/12M; фильтр только REJECTED не использовать, иначе можно потерять снятие ограничения. Ограничители страниц/времени при достижении означают PARTIAL, а не SUCCESS.
5. Результаты страниц сохраняются как staged observations и не меняют текущие episodes. После всех страниц reducer применяет результаты и watermark **одной DB-транзакцией**, читая staging порциями ≤500 без промежуточного commit и без сетевых вызовов. Начальные защитные лимиты: 50 000 записей/run и 120 секунд финальной DB-транзакции. Превышение/сбой → rollback, PARTIAL и прежний watermark; лимиты настраиваются после измерений, а не обходятся скрытым partial-success. Staging сохраняется для диагностики и повторного применения.
6. Из ответа явно следует отсутствие ограничения — закрыть текущую episode. SKU отсутствует в delta — ничего не заключать о снятии.
7. Дубликаты перекрытия и старые lastModified не откатывают новое состояние. Разные тела с одинаковым timestamp сохраняются как конфликт и требуют сверки; произвольного выбора по порядку нет.
8. Технические дубли не создают задания; смена причины не уничтожает историю. Первая operation создаётся только для подтверждённого кандидата по явной команде подготовки; повторная требует restart authorization.
9. Сверка известных активных/ожидающих проверки SKU — отдельный bounded job. Рекомендованный период после включения — 30 суток; эта сверка не является проходом по полному каталогу.
10. Расписание обнаружения — 14 суток, отключено до отдельного включения. Redis outage/повтор beat не создаёт два одинаковых run.

Ограничения: fromDate не является журналом всех событий. Ограничение, появившееся и исчезнувшее между запусками, может быть не видно. Пагинация меняющегося каталога и поздняя индексация не дают формальной гарантии snapshot; overlap и сверка известных SKU снижают риск. Они не обнаруживают пропуски старого неполного baseline. Полный каталог/полная сверка всего каталога требуют отдельного разрешения и сюда не входят.

## 7. Этапы для Terra Medium

Каждый этап — отдельная проверяемая задача. Зависимость означает готовность контракта/схемы, а не необходимость перечитать весь проект. За одну сессию выполнять один этап; не объединять соседние миграции или интеграции ради скорости.

Ориентир размера: один сервис/контракт и до 3–5 implementation-файлов плюс необходимые тесты. Если этап выходит за пределы, разделить его на A/B с сохранением того же результата и зависимостей. Общие архитектурные решения этого документа не пересматривать без нового факта.

### S00. Безопасные режимы и явные ошибки

**Зависимости:** нет.
**Работа:** добавить mode/feature flags; default LEGACY_READ_ONLY. Заблокировать commit/assign/complete/submit в неподходящем режиме. Удалить runtime-generated periods и синтетический routing из production service paths; вернуть явный NOT_IMPLEMENTED/UNVERIFIED. Catalog/Library fallback не должен сообщать успех записи или молча переключаться на mock. Не подключённый production экран показывает unavailable; demo явно отдельный.
**Проверка:** ошибка backend/401/409 не создаёт локальный EAN и не даёт success toast; вызов live adapter при выключенном флаге невозможен.
**Готово:** никакой существующий mock не может выдать production-успех или записать неподтверждённый результат.

### S01. Надёжный мигратор и тестовый стенд

**Зависимости:** S00.
**Работа:** выделить migration CLI. Включить только PostgreSQL baseline `0001_postgres_initial.sql` и следующие версии; SQLite `0001_initial.sql` исключить. Исправить autobegin/transaction handling, добавить migration advisory lock/checksums. Startup только проверяет schema head. Подготовить disposable PostgreSQL 15 и pytest-конфигурацию с запретом рабочей DSN.
**Проверка:** upgrade пустой БД, upgrade legacy fixture, повторный запуск и два параллельных migrator. Новые revisions имеют down/проверку обратимости; опасный downgrade непустых production-данных отклоняется.
**Готово:** воспроизводимая схема без DDL при обычном запуске API.

### S02. Изоляция LEGACY_UNVERIFIED

**Зависимости:** S01.
**Работа:** migration 0004: scope существующих сущностей, production indexes/FK, служебные provenance-поля. Все прежние строки сохраняются как legacy; никакие старые USED/READY/PUBLISHED не объявляются подтверждёнными. Создать read-only legacy query API и отчёт конфликтов без исправления/удаления строк. Предусмотреть проверяемую promotion-команду, по умолчанию disabled.
**Проверка:** сохранены counts/идентификаторы/история fixture; production reads/allocator не видят legacy; promotion без evidence отклоняется.
**Готово:** production набор пуст и безопасен; существующие данные доступны для аудита.

### S03. Авторизация backend и BFF

**Зависимости:** S02.
**Работа:** internal signed principal и account permissions; отдельный read-only M2M principal; CSRF/Origin; production secret validation. Исправить BFF forwarding bytes и allowlisted headers, request ID и error DTO. Ввести FastAPI dependencies для всех production routes.
**Проверка:** прямой неавторизованный API-доступ, forged actor/account, истёкшая подпись, JV→XL, CSRF, binary upload и повтор idempotency headers.
**Готово:** доверенная identity доходит до сервиса и аудита; possession cookie не заменяет backend-проверку.

### S04. Чистый classifier 3M/12M

**Зависимости:** S00; этап можно выполнить до S01–S03.
**Работа:** переписать `otto_status_service.py` как чистый анализ errors/response, без fixtures и сетевых вызовов. Типизированный результат по §3.2; неизвестные коды и все ошибки сохраняются.
**Проверка:** сохранённые 100 JSON → 50/50; дополнительная 301006 не мешает 3M; пустые/посторонние errors, общий prefix, ложная подстрока, противоположные code/title, оба периода, missing/mismatched SKU, некорректный JSON. Fixtures только в тестах.
**Готово:** именно production classifier проходит весь набор; отдельного синтетического get_status пути нет.

### S05. Наблюдения, episodes и run-контракты

**Зависимости:** S02, S04.
**Работа:** migration 0005: расширить import_runs; добавить import_rows, observations, candidate_episodes, discovery_cursors; canonical identity и account/scope guards. Реализовать ingestion/reducer для заранее переданных ответов без сети. Записать OpenAPI/DTO для import/run/product read.
**Проверка:** baseline не новый; одинаковое наблюдение идемпотентно; смена причины и снятие сохраняют историю; account isolation; старый ответ не откатывает новое состояние.
**Готово:** историю можно воспроизвести из сохранённых ответов без создания Restart операций.

### S06. OTTO OAuth и read adapter

**Зависимости:** S03, S04.
**Работа:** собственный Restart adapter; скопировать узкие проверенные части OAuth из OttoFlow, не импортировать его код/AccountSettings. Реализовать GET product/prices/status/list/update-task results/categories/shipping metadata. Credentials — отдельный secret provider JV/XL; редактируемых секретов в браузере нет. Host allowlist, timeouts, typed errors, token refresh и rate limits.
**Проверка:** только подменённый transport; 401 refresh, 429 Retry-After, 5xx, pagination, wrong SKU/account, неразрешённый task href, отсутствие цены.
**Готово:** можно передать реальные read-responses в reducer; выключенный live flag остаётся запретом сети.

### S07. Aftercool lookup adapter

**Зависимости:** S03.
**Работа:** заменить fixture service типизированным read adapter. Скопировать robust parsing/paging из OttoFlow с сохранением account/factory scope; own credentials/cache. Проверять product ID/EAN, дубли, Startpreis/GalleryURL и полноту ответа. Factory hint — фильтр, не доказательство совпадения.
**Проверка:** clean absence, timeout, auth error, partial pages, два conflicting EAN, wrong account/factory и валидная найденная строка.
**Готово:** только NOT_FOUND_CONFIRMED приводит к OTTO_DIRECT; никакого вымышленного title/price/category.

### S08. Постоянные фоновые задания

**Зависимости:** S03, S05.
**Работа:** migration 0006: background_jobs и transactional enqueue. Dispatcher, job leases, retry policy и dead-letter/status API; Celery transport принимает ID задания. Внешние адаптеры пока fake. Не запускать постоянный worker/beat на рабочем сервере.
**Проверка:** rollback не оставляет job, crash до/после broker publish, повтор delivery, истёкший lease, dead job/manual replay, Redis outage.
**Готово:** enqueue надёжен независимо от брокера, повтор не создаёт новый бизнес-Restart.

### S09. Настоящий первоначальный импорт

**Зависимости:** S05–S08.
**Работа:** переписать import_pipeline поверх parser → rows → jobs → OTTO identity/status → classifier → Aftercool → transactional persistence. TXT/CSV/XLSX, strings/leading zeros, hash/idempotency, 20 MB/10 000, chunks. Preview локальный; commit HTTP 202. Добавить row errors и run endpoints. Источник/период из файла — hints, не фиктивные OTTO-коды.
**Проверка:** baseline/new counts, duplicate files/rows/concurrent imports, malformed identifiers, mixed errors, failure resume, найденный Aftercool/direct/error/ambiguous. is_candidate=false не проходит дальше.
**Готово:** fixture import формирует достоверные candidates/историю в PostgreSQL и не затрагивает EAN-пул.

### S10. Единая модель EAN-пула

**Зависимости:** S02, S03, S08 (schema head 0006); независимо от включения live adapters.
**Работа:** migration 0007: ean_pool ownership/verification/reservation metadata, composite constraints; legacy registry facade. Импорт только в ean_pool. Allocation service с row locks/SKIP LOCKED, привязкой к operation и retry-safe reuse. Проверка пересечения с source/target/legacy по обоим аккаунтам; конфликтный код блокируется, не удаляется.
**Проверка:** параллельный reserve, один operation retry, same code JV/XL, дефицит, rollback, прежние 3 конфликтных связи остаются в legacy; произвольный EAN клиента не принимается.
**Готово:** один источник пула; назначение и расход не расходятся между таблицами.

### S11. Evidence и допустимость GTIN

**Зависимости:** S03, S10.
**Работа:** migration 0008: gtin_evidence и validator; приватные evidence artifacts, account/product binding, actor и expiry. Реализовать admin review/promotion/quarantine, без автоматического одобрения по checksum или GET 404. При отсутствии установленного законного сценария — blocked.
**Проверка:** только ownership без product assignment, чужой бренд/account, expired evidence, конфликт, корректное полное evidence, запрет обхода через manual QC.
**Готово:** allocator/QC/publisher используют один validator; legacy codes не становятся допустимыми автоматически.

### S12. Операции, ревизии и повторный Restart

**Зависимости:** S05, S08, S10, S11.
**Работа:** migration 0009: расширить restart_operations; operation_revisions, qc_results, restart_authorizations; связи episodes/GTIN. В revision предусмотреть immutable source/price/fulfillment snapshots и evidence refs, чтобы S13–S15 не меняли уже применённую миграцию. В operation заранее определить processing evidence event ID и online observation ID с проверкой account/target SKU: S17 тестирует этот контракт без запуска S22. Создание первой операции идемпотентно, последующей — только с одноразовым scope-bound разрешением; legacy overlap требует history review. optimistic version и immutable payload hashes; отмена/освобождение безопасного резерва.
**Проверка:** concurrent create, технический retry, новый EAN с ограничением того же source, reused/expired authorization, version conflict, cancel после неопределённой отправки.
**Готово:** попытки нумеруются по исходному товару; повторный Restart не возникает из retry/сканирования.

### S13. Подготовка данных и pricing snapshot

**Зависимости:** S06, S07, S12.
**Работа:** подготовка из подтверждённых OTTO/Aftercool source snapshots, категория/brand/images, текущая standardPrice и формула JV/XL. Сохранить attributes/schema/source refs, fulfillment inputs и новую ревизию. OTTO_DIRECT получает данные из OTTO; нет дефолтных выдуманных значений. Подготовка резервирует только допустимый GTIN.
**Проверка:** cents/Decimal, отсутствующая/нулевая цена, salePrice и Startpreis не подменяют базу, скидка не вычитается дважды, account mismatch, категория/источник изменились.
**Готово:** подготовленная версия воспроизводима и содержит provenance каждого обязательного факта.

### S14. OpenAI adapter с проверяемыми предложениями

**Зависимости:** S08, S13.
**Работа:** отдельный provider с configurable model, structured result schema, timeout/budget, prompt/version/input hash. Title ≤70, изменение исходного названия и сохранение фактов. Атрибуты только из разрешённой taxonomy и с source refs; неподтверждённые предложения помечать для review. Ключ хранится на сервере, UI показывает только состояние настройки.
**Проверка:** длинный/идентичный title, неверный enum, выдуманный размер/сертификат, invalid JSON, отказ, timeout, повтор одного input hash.
**Готово:** AI не может сам присвоить READY/PUBLISHED или разрешить GTIN.

### S15. Автоматический QC и ручной разбор

**Зависимости:** S11–S14.
**Работа:** единый deterministic validator и policy version; PASSED_AUTO/MANUAL_REQUIRED/BLOCKED. Ручные исправления/подтверждение фактов → новая ревизия и повтор QC. Immutable approval привязан к payload hash, source/price/GTIN evidence versions. Удалить зависимость production ready от браузерной эвристики «есть один атрибут».
**Проверка:** golden valid auto-pass, missing LEGAL attrs, ambiguous AI/source, GTIN hard block, changed price/evidence/revision, неподтверждённый повторный Restart.
**Готово:** только конкретная актуальная ревизия может попасть в очередь.

### S16. API каталогов и подключение существующего UI

**Зависимости:** S09, S12, S15.
**Работа:** products list/detail/draft endpoints, серверные фильтры/пагинация/счётчики; обновить DTO/client и оба CatalogProductsTable consumers. Scope по signed account, update по expectedVersion. Сохранить маршруты/визуальную структуру. Убрать production mocks, synth-products, локальные AI/sync/EAN mutators и локальные расчёты статусов.
**Проверка:** импортированный товар появился в правильном каталоге; reload сохраняет edit; 401/409/503 показаны явно; переключение JV/XL отменяет старый запрос и не применяет чужой ответ.
**Готово:** каталоги отображают тот же набор, на котором работает backend.

### S17. Фабрики, «Новые N» и история завершений

**Зависимости:** S09, S12. Контракт доказательств определён в S12; реальные подтверждения появятся в S22, сейчас они задаются только тестовыми fixtures.
**Работа:** migration 0010: coverage/generation/confirmed-at и generation-key истории. Переработать factory_service по §3.4; production метрики из episodes/operations. Пересчёт в транзакциях reducer/confirmation, не при GET. History с UNIQUE(account, factory, generation); прежняя completion date сохраняется. Старый complete endpoint только проверяет/пересчитывает.
**Проверка:** COMPLETE → 13 новых → N=13 → обработано 5 → N=8 → COMPLETE с новой датой; прежняя история цела. Stale coverage, partial scan, clear restriction, повтор complete и concurrent import/complete.
**Готово:** UI и DB согласованы; локального status=PUBLISHED без evidence недостаточно.

### S18. Production Library и read-only Aftercool API

**Зависимости:** S03, S10–S12, S16.
**Работа:** Library использует только source identity/lineage/pool/operations/events; не объединяет demo и production. Lookup возвращает linked identifiers и подтверждённые online identifiers отдельно, history и checkedAt. M2M principal ограничен разрешёнными аккаунтами и GET. Пагинация/лимиты для factory lookup. Remote deactivation отсутствует; legacy local-deactivation не должна изображать изменение OTTO и в production выключена.
**Проверка:** правильный graph old→new→next attempt, account scope, lookup до/после публикации, unknown EAN, revoked M2M key, попытка write через M2M.
**Готово:** Aftercool может читать достоверные связи без доступа к подготовке/публикации.

### S19. Постоянный Drip Feed

**Зависимости:** S08, S12, S15, S17 (schema head 0010).
**Работа:** migration 0011: расширить restart_batches/items под расписание и immutable operation/revision/QC refs. Preview не пишет; start создаёт задания только для прошедших QC. Schedule требует дату/время и явную IANA timezone, хранится UTC; для пользовательских примеров — Asia/Qyzylorda. Pause останавливает новые claims, cancel не отменяет уже отправленное в OTTO.
**Проверка:** распределение, DST/timezone, double start, конкурентные workers, pause/resume, stale revision, mixed account/cohort, Redis outage.
**Готово:** refresh/перезапуск процесса не теряет план; frontend triggerNow больше не присваивает published.

### S20. Отправка product payload и история HTTP-попыток

**Зависимости:** S06, S08, S11, S15, S19.
**Работа:** migration 0012: submission_attempts; продуктовый write adapter, preflight и `SUBMITTING`. Сохранить request hash/attempt до сети, ответ и task links после неё. На пилоте один SKU на запрос. Только target SKU выбранной операции; исходный товар не изменять. Write флаги выключены.
**Проверка:** 202, per-SKU ошибки, 429 с явным отказом, timeout после принятия, 5xx с неопределённым результатом, crash вокруг отправки, duplicate job, stale QC/evidence/price.
**Готово:** неизвестный исход не приводит к слепому повтору/новому EAN; 202 не становится PUBLISHED.

### S21. Fulfillment нового SKU

**Зависимости:** S13, S20.
**Работа:** scoped availability quantities/product-delivery-information adapter и отдельные идемпотентные jobs для нового SKU после успешного product import. Shipping profile и количество подтверждены; нет выдуманного stock/срока. Все write actions имеют отдельный enable gate и audit.
**Проверка:** отсутствие stock/profile блокирует готовность; wrong account/old SKU запрещены; повтор результата безопасен; ошибки/async responses сохраняются и не отмечают успех ONLINE.
**Готово:** prerequisites нового listing обработаны без воздействия на старые товары.

### S22. Processing poller и подтверждение ONLINE

**Зависимости:** S06, S17, S20, S21.
**Работа:** job poll update-tasks и всех per-SKU result pages, затем marketplace-status target SKU. Сохранять два независимых доказательства успеха; счётчики операций/партий/фабрик обновлять одной транзакцией. Backoff/poll deadline; по истечении срока — review, не фиктивный FAILED/PUBLISHED. Для synchronous success сохранять явный per-SKU импортный результат; один HTTP 200 без него недостаточен.
**Проверка:** mixed succeeded/failed/unchanged, done без SKU, wrong SKU/account, accepted+REJECTED, ONLINE без доказательства обработки, успешный полный путь, повтор callback/poll, поздняя потеря ONLINE.
**Готово:** PUBLISHED создаётся только по согласованному 2Б и сохраняет историю доказательств.

### S23. Инкрементальный scanner

**Зависимости:** S05, S06, S08, S09, S17; не зависит от включения публикации.
**Работа:** bounded account-scoped scan, staged observations и атомарный финальный apply/watermark, overlap 48h, список всех изменённых статусов, known-SKU reconciliation. Сохранять ошибки, counts, page state, time window, late/conflicting observations. Full scan и scheduler остаются выключены.
**Проверка:** несколько страниц, пустое успешное окно, 429/partial/invalid page, duplicate pages/loops, restart во время apply, старые/одновременные timestamps, снятие ограничения, смена периода, повторное ограничение нового EAN.
**Готово:** новый confirmed episode попадает в inbox один раз; ни частичный запуск, ни retry не продвигают watermark.

### S24. Собственный runtime и эксплуатационные проверки

**Зависимости:** S03, S08, S19, S23.
**Работа:** только собственный compose: Redis, worker, dispatcher/beat profiles, health/readiness, persistent artifact storage, TLS/reverse-proxy инструкция, secret injection, graceful shutdown и backup/restore. API без reload, frontend production build/start. Worker/beat и все live-write flags по умолчанию disabled. Никаких изменений root compose/OttoFlow containers.
**Проверка:** disposable stack, separate networks/DB/queues, restart API/worker/Redis, readiness при schema mismatch, отсутствие credentials в logs.
**Готово:** подготовлена воспроизводимая конфигурация; запуск рабочей среды остаётся отдельным действием.

### S25. Сквозные проверки и release-пакет

**Зависимости:** соответствующие реализованные ветки; отдельные read/discovery и publish suites.
**Работа:** offline E2E: import → source routing → reserve → prepare → AI/review → QC → drip → 202 → task result → availability → ONLINE → factory completion → new candidates. Отдельно legacy-preservation, migrations и concurrency. Обновить OpenAPI, README, env example без значений и собственный CI/script, не меняя root CI без отдельного разрешения.
**Проверка:** PostgreSQL 15 + изолированный Redis, fake HTTP, backend suites; frontend TypeScript/build и targeted browser flows. Нет доступа к live endpoints из тестовой сети.
**Готово:** приложены команды/результаты, список миграций, rollback/forward-recovery, flags, известные ограничения. Приёмка не опирается на tsc или HTTP 200 страницы как на доказательство backend-функций.

### S26. Ограниченный live read pilot — отдельное разрешение

**Зависимости:** read/discovery часть S25 и S24.
**Работа после разрешения:** проверить отдельные credentials и согласованный список SKU/малое fromDate-окно. Начать с двух диагностированных XL SKU; JV — только по согласованной выборке. Сопоставить responses, account identity, pagination, Aftercool routing и локальный audit. Никаких product/availability writes и полного каталога.
**Готово:** реальный read-contract подтверждён, counts/лимиты измерены; затем отдельно разрешается расширять объём импорта.

### S27. Ограниченный publish pilot — отдельное разрешение

**Зависимости:** publish suite S25, S26, прошедший GTIN review.
**Работа после разрешения:** разрешить конкретные operation IDs/account, проверить evidence и свежий QC, выполнить маленькую партию. Подтвердить обработку, fulfillment и ONLINE, сопоставить Library/историю/фабрики. При неопределённом результате остановить intake; не повторять массово.
**Готово:** имеются реальные доказательства 2Б. Расширение лимитов — отдельное решение; глобальной кнопки «опубликовать всё» нет.

### S28. Включение обнаружения раз в 14 дней — отдельное разрешение

**Зависимости:** S23, S24, discovery suite S25, S26. Не требует включения публикации/S27.
**Работа после разрешения:** зафиксировать baseline/coverage/cutoff, аккаунты и лимиты; включить только discovery schedule. Настроить уведомление о missed/partial runs, lag watermark, age coverage и failed jobs. Known-SKU reconciliation включать отдельной настройкой с контролем объёма.
**Готово:** первый scheduled run имеет полный audit, watermark корректен; повтор schedule не создаёт дубликаты и не запускает публикацию.

## 8. Передача этапа исполнителю

Обязательное дробление крупных этапов на отдельные задачи Terra Medium:

| Задача | Небольшой самостоятельный результат |
|---|---|
| S08A → S08B | Сначала PostgreSQL jobs/lease/idempotency и fake executor; затем Celery transport/dispatcher и broker failure tests. |
| S16A → S16B | Сначала products API/DTO и server queries; затем существующие Afterbuy/Direct UI consumers. |
| S19A → S19B | Сначала постоянные batches/queue/schedule; затем переключение существующих Drip UI на API. |
| S24A → S24B | Сначала собственный disposable runtime; затем production-конфигурация и recovery/runbook без запуска на рабочем сервере. |
| S25R / S25P | Отдельные read/discovery и publish E2E-пакеты. S26 требует S25R; S27 требует оба. |

Целевые модули не нужно угадывать: существующие `otto_status_service.py`, `aftercool_lookup_service.py`, `import_pipeline.py`, `factory_service.py` сохраняют роль доменных entrypoints. Новые сервисы называются `ean_pool_service.py`, `gtin_service.py`, `operation_service.py`, `preparation_service.py`, `ai_service.py`, `qc_service.py`, `drip_service.py`, `discovery_service.py`. HTTP-клиенты — `integrations/otto_client.py`, `aftercool_client.py`, `openai_client.py`; репозитории/routers/schemas называются по той же сущности. Не создавать второй сервис с теми же правилами рядом с существующим.

Для каждой сессии Terra Medium достаточно этого шаблона:

```text
Реализуй только этап Sxx из BACKEND_IMPLEMENTATION_PLAN.md.
Прочитай AGENTS.md, короткий актуальный checkpoint и разделы плана,
на которые ссылается этап. Сверь только целевые файлы и зависимости.
Сохрани чужие изменения. OttoFlow разрешено только читать/копировать.
Рабочую БД, live API, publish и расписание не включать.
Реализуй этап, выполни его целевые offline-проверки и обнови checkpoint.
Отчёт: файлы, поведение, команды/результаты, критерии готовности,
ограничения и следующий разрешённый этап. Не создавать commit/push.
```

Порядок по умолчанию: S00 → S01 → S02 → S03 → S04 → S05 → S06 → S07 → S08 → S09, далее ветки EAN/подготовка/QC и discovery по указанным зависимостям. S04 можно вынести раньше: он чистый и проверяется на уже сохранённых ответах. S26–S28 — эксплуатационные gates, не обычные задачи реализации.

Владельцы новых миграций: 0004/S02 scope; 0005/S05 discovery; 0006/S08 jobs; 0007/S10 pool; 0008/S11 GTIN evidence; 0009/S12 operations/QC revisions; 0010/S17 factory projection; 0011/S19 batches/queue; 0012/S20 submissions. Эти schema-этапы выполняются в указанном порядке; параллельно можно разрабатывать чистые сервисы/contract tests, не применяя будущие миграции раньше предшествующих. Применённые файлы не дописывать задним числом.

Стандарт завершения этапа: целевые meaningful tests проходят; account/scope invariants проверены; нет новых скрытых fallback; нет changes вне Restart кроме коротких memory updates; нет запущенных live tasks; OpenAPI/DTO и checkpoint соответствуют коду. Незавершённая внешняя предпосылка обозначается явно, а не заменяется mock-успехом.

## 9. Источники и пределы подтверждения

- Локальная основа: `backend/app.py`, migrations 0001–0003, services, frontend catalog/Library/QC/Drip clients и сохранённая диагностика `diagnostics/otto_3m_12m_xl_100_20261016/`. Эти fixtures проверяют ответы 100 XL SKU, а не полноту каталога или текущие статусы.
- [OTTO Products](https://api.otto.market/docs/functional-interfaces/products/): асинхронные update-tasks, per-SKU succeeded/failed/unchanged и отдельная проверка Marketplace Status после импорта. Этим обосновано разделение обработки и ONLINE.
- [OTTO API Index](https://api.otto.market/docs/api-docs/) и [API Guidelines](https://api.otto.market/docs/about-the-api/api-guides/): источник контрактов адаптеров, параметров и ограничений; сохранённые примеры фиксируются в contract tests. Документация не заменяет разрешение на live-вызовы.
- [GS1 GTIN Management Standard](https://ref.gs1.org/standards/gtin-management/): решение о новом GTIN связано с идентификацией физического товара и основаниями изменения. Владение свободным номером и редактирование карточки не используются как автоматическое разрешение нового GTIN.

На этапе подготовки плана выполнены только чтение исходников/документации и создание этого документа. Реализация, новые тесты приложения, рабочие миграции, сетевые проверки аккаунтов, публикации и включение расписания не выполнялись.
