# OttoFlow: одноразовая настройка после Drip Feed

Реализация подготовлена в отдельном worktree `/tmp/ottoflow-stock-change-20261030`. Она не перенесена в основной checkout, не развёрнута и не подключалась к рабочей БД или OTTO. До отдельного согласованного cutover работающие процессы продолжают исполнять старую конфигурацию Celery.

## Как будет работать Celery после внедрения

Периодические обновления цен и остатков отключены: `tasks_availability_sync.sync_published_availability` удалена из Beat schedule, а функция и одноимённая Celery-задача оставлены как no-op для совместимости со старыми сообщениями. Старые сообщения `tasks.recover_incomplete_availability` также становятся no-op. Старый dispatch helper больше не ставит availability-задачи в очередь.

Остаются три существующих периодических задания:

- `tasks.process_scheduled_publish_tick` — раз в минуту публикует готовые элементы активных Drip Feed планов и проверяет постоянный PostgreSQL outbox первоначальной настройки;
- `tasks.poll_otto_processing_items` — раз в две минуты проверяет результаты асинхронной обработки Drip Feed в OTTO;
- `tasks.sync_order_stats` — раз в день обновляет статистику заказов.

После подтверждённой новой публикации для нового OTTO API queue item создаётся одна постоянная запись с отдельными шагами stock и delivery. Stock отправляется со значением `20`; параметры delivery фиксируются для этой операции. Повторное выполнение Celery-сообщения не повторяет уже завершённый или неопределённый шаг. Ранее опубликованные товары и BATCH_FILE-планы не получают такую запись. Существующие товары автоматически не догоняются.

Повтор допускается только после явного ответа OTTO об отказе, который безопасно повторить: HTTP 429 или подтверждённый отказ «unknown SKU». Backoff хранится в PostgreSQL; лимит — до 10 автоматических попыток. Timeout, сетевой сбой после отправки и HTTP 5xx имеют неопределённый исход и не отправляются повторно автоматически. HTTP 202 сохраняется как `accepted`, а не как подтверждение завершения. Такие случаи требуют проверки в OTTO и аудируемого ручного решения.

Вкладка «Фабрики Drip Feed» читает `/pipeline/drip-factories` с явным account `JV`/`XL`. Она показывает сохранённые маркеры отправки и состояние одноразовой настройки. Это не доказательство текущего `ONLINE`; история до установки новых маркеров явно обозначена неполной.

## Ручная сверка

Ручные команды доступны только при заданных `AVAILABILITY_REVIEW_TOKEN` и `AVAILABILITY_REVIEWER_ID`. Токен передаётся в заголовке, не хранится в браузере и не включается в репозиторий. Повтор разрешается только для шага `failed` и требует причины и ссылки на evidence. Неопределённый исход можно отметить `APPLIED`, `NOT_APPLIED` или `UNRESOLVED`; каждое решение сохраняет actor, account, товар, причину, ссылку и время. Исправление профиля доставки ограничено профилями, разрешёнными для выбранного аккаунта.

Доступны read-only список `/pipeline/initial-availability` и история `/pipeline/initial-availability/{state_id}/history`. Старые queue items без нового durable `state_id` ничего не отправляют.

## Cutover, только после отдельного разрешения

1. Зафиксировать текущие версии API/worker/beat, активные планы и незавершённые задачи. Не очищать Redis и не удалять очередь.
2. Снять резервную копию БД и проверить текущий Alembic head. Проверенная migration `0006_initial_availability` добавляет таблицы состояния/попыток/решений и nullable-маркер попытки product submission. Её нельзя применять без отдельного разрешения на рабочую БД.
3. Остановить старый Beat и дать уже выполняющимся старым задачам штатно завершиться. Обновить все worker-процессы до версии с no-op обработчиками; не оставлять старые workers рядом с новым Beat. После этого старые отложенные availability-сообщения безопасно подтвердить без сетевых вызовов.
4. Развернуть согласованную версию API, всех workers и Beat. Убедиться, что цена/stock sync и recovery отсутствуют в Beat schedule, а Drip tick и OTTO processing poller остались.
5. Проверить новую страницу и API только на согласованном read-only объёме. Не включать массовое повторное обновление и не отправлять старые товары.

Не запускать миграцию, deploy, worker restart, Celery purge или OTTO-запросы в рамках этой подготовки. Перед cutover отдельно оценить старые записи `Availability sync incomplete` и старые отложенные сообщения; автоматической повторной отправки для них нет. Downgrade migration запрещён, если уже появились записи первоначальной настройки, аудит или маркеры попыток публикации.

## Проверки изолированной реализации

- Backend suite: PostgreSQL 15, `python -m unittest discover tests` — 61 тест прошёл.
- Правила: `python test_furniture_rules.py`, `python test_category_rules.py` — прошли.
- `python test_availability_sync.py` — 4 теста прошли; 5 выбранных legacy-тестов для retired dispatch/task/client/retry прошли.
- Node 20 production build (`NEXT_PUBLIC_API_URL=/api`, `API_URL=http://localhost:8001`) — прошёл, включая TypeScript и маршрут `/dashboard/drip-factories`.
- Migration `0006_initial_availability`: upgrade → downgrade → upgrade на disposable PostgreSQL с head `0005_ai_attributes` — прошёл.
- Проверены account/EAN guards, конкурентный claim, duplicate delivery, старые сообщения, неопределённые ответы, retry bounds, аудит ручного решения и отсутствие availability Beat jobs.
- Дополнительный полный запуск `python test_otto_enhancements.py` не прошёл: один неизменённый formatter-тест не очищает запрещённый атрибут; legacy twin-тест требует отсутствующие Aftercool credentials, а transfer-тест после их отсутствия падает на существующем `NameError` (`logger` не определён). Этот файл не входит в текущий CI suite; live credentials/API не использовались.
- Полное применение старой migration-цепочки на пустой БД блокируется предшествующей migration `0001_composite_ean`, которая пытается создать concurrent index до таблицы. Сама новая `0006_initial_availability` отдельно прошла upgrade/downgrade/upgrade на disposable PostgreSQL со схемой, зафиксированной на `0005_ai_attributes`.

Эти проверки не являются разрешением на live read, миграцию или внедрение. Изменения можно переносить в основной checkout только после review и явного approval пользователя.
