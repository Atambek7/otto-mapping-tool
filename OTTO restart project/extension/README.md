# OttoRestart Portal Media Bridge (Chrome MV3 Extension)

Расширение автоматически подтягивает URL изображений (`https://i.otto.de/i/otto/{assetId}`) для товаров `OTTO_DIRECT`, у которых официальный `/v5/products/{sku}` API OTTO возвращает только `filename` без ссылки.

## Установка (Load unpacked)

1. Откройте в Chrome / Yandex / Edge страницу `chrome://extensions`.
2. Включите **Режим разработчика** (**Developer mode**).
3. Нажмите **Загрузить распакованное расширение** (**Load unpacked**).
4. Выберите папку `OTTO restart project/extension`.

## Использование

1. Откройте авторизованную вкладку **OTTO Partner Connect** (`https://portal.otto.market`, нужный аккаунт `JV` или `XL`).
2. Откройте вкладку **OttoRestart** → **Товары напрямую из OTTO** (`https://omt.automatonsoft.de/restart/direct-otto`).
3. Нажмите кнопку **«Забрать фото из OTTO Portal»** (либо запустите синхронизацию из иконки расширения в панели браузера).
4. Расширение:
   - получит список `OTTO_DIRECT` товаров без фото из текущей сессии OttoRestart;
   - найдёт `assetId` изображений в вашей открытой сессии OTTO Partner Connect (с задержкой `300 мс` между запросами);
   - проверит принадлежность аккаунту (`brandId`) и сохранит до 10 URL `https://i.otto.de/i/otto/{assetId}` обратно в OttoRestart.
