# Otto Mapping Tool (Docker Setup)

## Быстрый запуск через Docker

1. Убедитесь, что у вас запущен **Docker Desktop**.
2. В терминале в папке `otto-mapping-tool` выполните:
```bash
docker compose up -d --build
```
3. Откройте в браузере:
- **Frontend (интерфейс):** [http://localhost:3000](http://localhost:3000)
- **Backend (API Docs):** [http://localhost:8001/docs](http://localhost:8001/docs)

## Остановка контейнеров
```bash
docker compose down
```

## Данные
База данных `mapping_tool.db` и файл `users.json` проброшены наружу через volumes, поэтому все ваши аккаунты, товары и настройки сохраняются при перезапуске контейнеров.
