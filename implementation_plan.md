# Итоговый утвержденный план: Полная очистка и перезаливка каталога Kaufland

Этот документ содержит все подтвержденные детали, структуры данных и точную последовательность выполнения для аккаунтов **JV** и **XL**.

---

## 1. Зафиксированные правила на основе ваших ответов

1. **Идентификация фабрики и Китая (`CN`):**
   * В ответе Aftercool поле `"source_file"` содержит имя файла фабрики (например: `"source_file": "MerryFashion__CN__498019.csv"`).
   * Если в `source_file` есть подстрока `_CN_` или `__CN__` или `(CN)` ➔ это **Китай**.
   * Все фабрики без метки Китая (Турция `_TR_`, Польша `_PL_`, Италия `_IT_` и др.) ➔ **Не-Китай**.

2. **Правила сроков доставки (`handling_time`):**
   * **Китай (`CN`):** `handling_time = 60` (окно 8–12 недель / 60 дней).
   * **Не-Китай:** `handling_time = 30` (30 рабочих дней).

3. **Очистка каталога (Purge):**
   * Полное удаление всех офферов на **обоих аккаунтах (`JV` и `XL`)** по **всем 9 странам** (`de, cz, sk, pl, at, fr, it, nl, es`), включая статусы `ACTIVE`, `INACTIVE` и `Offer data is missing`.

4. **Выгрузка товаров (`JV` и `XL`):**
   * Обработка идет раздельно для `account="JV"` и `account="XL"` (со своими EAN).

---

## 2. Пошаговый порядок действий

### 🟢 ЭТАП 1: Разработка и запуск скрипта полной очистки
Файл: `scripts/purge_kaufland_units.py`

1. **Тестовый прогон (`--dry-run`):**
   ```bash
   docker compose exec -T django python scripts/purge_kaufland_units.py --dry-run
   ```
   *Скрипт посчитает общее число офферов на JV и XL по всем 9 странам и выведет статистику.*

2. **Боевое удаление:**
   ```bash
   docker compose exec -T django python scripts/purge_kaufland_units.py --execute
   ```
   *По завершении в Seller Portal проверяем: `Offers: 0`.*

---

### 🟢 ЭТАП 2: Разработка скрипта выгрузки товаров Aftercool ➔ Kaufland
Файл: `scripts/reupload_aftercool_catalog.py`

1. Интеграция с API Aftercool (`dataset="lister"`, `account="JV"` и `"XL"`).
2. Определение Китая по `source_file`:
   ```python
   def get_handling_time(source_file: str) -> int:
       if "_CN_" in source_file.upper() or "__CN__" in source_file.upper() or "(CN)" in source_file.upper():
           return 60  # 8-12 недель
       return 30      # 30 рабочих дней
   ```
3. Выгрузка карточек (`PUT /v2/product-data`) с локалями `de-DE, nl-NL, es-ES...` и создание офферов (`POST /v2/units`) с правильными ценами и `handling_time`.

---

### 🟢 ЭТАП 3: Проверка и релиз
1. **Пробный запуск (5 товаров):**
   ```bash
   docker compose exec -T django python scripts/reupload_aftercool_catalog.py --limit=5
   ```
   *Проверяем в Seller Portal, что китайский товар встал с 60 днями, а европейский/турецкий с 30 днями.*
2. **Массовый запуск полной выгрузки.**
