"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import {
  AlertCircle,
  ArrowRight,
  Boxes,
  CheckCircle2,
  Database,
  Factory,
  FileText,
  Globe,
  Loader2,
  Play,
  RotateCw,
  ScanSearch,
  Send,
  Sparkles,
  Upload,
} from "lucide-react";
import { useRestartAccount } from "@/features/state/restart-account";
import { catalogDemoService } from "@/features/catalogs/service";
import "./discover.css";

export function DiscoverView() {
  const { account } = useRestartAccount();
  const [scanning, setScanning] = useState(false);
  const [scanProgress, setScanProgress] = useState<{
    step: string;
    percent: number;
  } | null>(null);
  const [lastScanResult, setLastScanResult] = useState<string | null>(null);

  // Bulk Import State
  const [importText, setImportText] = useState("");
  const [dryRun, setDryRun] = useState(false);
  const [importLoading, setImportLoading] = useState(false);
  const [importResult, setImportResult] = useState<any | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Live catalog counts for currently selected account
  const directData = catalogDemoService.getDirectOttoProducts(account);
  const factoryData = catalogDemoService.getFactories(account);

  async function handleStartScan() {
    if (scanning) return;
    setScanning(true);
    setLastScanResult(null);

    // Step 1: Scan OTTO API
    setScanProgress({
      step: "1/3. Запрос в OTTO Marketplace API: выявление статусов REJECTED (код 602016)...",
      percent: 30,
    });
    await new Promise((r) => setTimeout(r, 600));

    // Step 2: Cross-check with Aftercool
    setScanProgress({
      step: "2/3. Сверка с Aftercool API (/api/products/by-ean и /api/products?dataset=Lister)...",
      percent: 70,
    });
    await new Promise((r) => setTimeout(r, 700));

    // Step 3: Distribution
    setScanProgress({
      step: "3/3. Распределение товаров: классификация на Afterbuy (по фабрикам) и Прямой OTTO...",
      percent: 100,
    });
    await new Promise((r) => setTimeout(r, 500));

    setScanning(false);
    setScanProgress(null);
    setLastScanResult(
      `✓ Сканирование завершено: выявлено ${directData.metrics.total} товаров напрямую из ОТТО и ${factoryData.metrics.totalCandidates} товаров Afterbuy по ${factoryData.metrics.totalFactories} фабрикам.`
    );
  }

  function handleFillSample() {
    setImportText(
      "4069424040821 3M\n4069424365436 12M\n4069424010022 3M\n4071489040630 3M"
    );
    setImportError(null);
  }

  async function handleImportSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!importText.trim() || importLoading) return;

    setImportLoading(true);
    setImportError(null);
    setImportResult(null);

    try {
      const res = await catalogDemoService.pasteImportContentAsync(
        account,
        importText,
        "paste_input.txt",
        dryRun
      );
      if (res.success) {
        setImportResult(res.summary);
      } else {
        setImportError(res.error || "Ошибка при импорте данных");
      }
    } catch (err: any) {
      setImportError(err?.message || "Ошибка соединения с сервером");
    } finally {
      setImportLoading(false);
    }
  }

  function handleFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (event) => {
      const text = event.target?.result;
      if (typeof text === "string") {
        setImportText(text);
        setImportError(null);
      }
    };
    reader.readAsText(file, "utf-8");
  }

  return (
    <div className="discover-page">
      {/* 1. Header */}
      <header className="discover-header">
        <div className="discover-header-title">
          <h1>Выявить новые 3/12</h1>
          <p>
            Массовый импорт списков, поиск товаров 3 и 12 месяцев через OTTO API и
            распределение по источникам
          </p>
        </div>
        <div className="discover-account-badge">
          <Database size={13} />
          <span>Аккаунт {account}</span>
        </div>
      </header>

      {/* 2. Celery Scanner Hub Card */}
      <section
        className="discover-scanner-card"
        aria-label="Панель управления сканером 3/12M"
      >
        <div className="discover-scanner-header">
          <div className="discover-scanner-intro">
            <div className="discover-scanner-icon">
              <ScanSearch size={24} />
            </div>
            <div className="discover-scanner-title">
              <h2>Celery Worker: Сканер новых 3/12M</h2>
              <div className="discover-scanner-status">
                <span className="discover-pulse-dot" />
                <span>Готов к сканированию · OTTO Marketplace API активен</span>
              </div>
            </div>
          </div>

          <button
            type="button"
            className="discover-trigger-btn"
            onClick={handleStartScan}
            disabled={scanning}
          >
            {scanning ? (
              <>
                <Loader2 size={16} className="discover-spinner" />
                <span>Сканирование каталога...</span>
              </>
            ) : (
              <>
                <Play size={16} />
                <span>Запустить выявление 3/12M</span>
              </>
            )}
          </button>
        </div>

        <p className="discover-scanner-desc">
          Периодическая фоновая задача выполняет опрос OTTO Marketplace API по коду ошибки{" "}
          <code>602016</code> (Nonseller для 3 месяцев, Low Performer для 12 месяцев).
          Обнаруженные карточки автоматически проверяются на принадлежность к Aftercool
          и распределяются в соответствующие каталоги.
        </p>

        {scanProgress && (
          <div className="discover-progress-block">
            <div className="discover-progress-label">
              <span>{scanProgress.step}</span>
              <span className="tabular-nums">{scanProgress.percent}%</span>
            </div>
            <div className="discover-progress-bar-track">
              <div
                className="discover-progress-bar-fill"
                style={{ width: `${scanProgress.percent}%` }}
              />
            </div>
          </div>
        )}

        {lastScanResult && (
          <div className="discover-success-banner">
            <CheckCircle2 size={16} />
            <span>{lastScanResult}</span>
          </div>
        )}
      </section>

      {/* 3. Bulk Import Section (Phase A) */}
      <section className="discover-import-card" aria-label="Первоначальный массовый импорт">
        <div className="discover-import-header">
          <div className="discover-import-icon">
            <FileText size={22} />
          </div>
          <div className="discover-import-title">
            <h3>Массовый импорт списков (TXT / CSV)</h3>
            <p>
              Загрузите список EAN/SKU существующих товаров. Система автоматически проверит статус 3M/12M,
              сверит с Aftercool и распределит товары по фабрикам Afterbuy или в прямой каталог OTTO.
            </p>
          </div>
        </div>

        <form onSubmit={handleImportSubmit} style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
          <textarea
            className="discover-import-textarea"
            placeholder="Вставьте список EAN или SKU (по одному в строке, либо CSV: ean;period;factory_id)..."
            value={importText}
            onChange={(e) => setImportText(e.target.value)}
            disabled={importLoading}
          />

          <input
            type="file"
            ref={fileInputRef}
            style={{ display: "none" }}
            accept=".txt,.csv"
            onChange={handleFileSelected}
          />

          <div className="discover-import-controls">
            <div className="discover-import-options">
              <label className="discover-checkbox-label">
                <input
                  type="checkbox"
                  checked={dryRun}
                  onChange={(e) => setDryRun(e.target.checked)}
                  disabled={importLoading}
                />
                <span>Только предпросмотр (Dry-Run без записи в БД)</span>
              </label>

              <button
                type="button"
                className="discover-sample-btn"
                onClick={() => fileInputRef.current?.click()}
                disabled={importLoading}
              >
                <Upload size={13} style={{ display: "inline", marginRight: "6px" }} />
                Выбрать файл (.txt, .csv)
              </button>

              <button
                type="button"
                className="discover-sample-btn"
                onClick={handleFillSample}
                disabled={importLoading}
              >
                Пример EAN
              </button>
            </div>

            <div className="discover-import-actions">
              <button
                type="submit"
                className="discover-submit-import-btn"
                disabled={!importText.trim() || importLoading}
              >
                {importLoading ? (
                  <>
                    <Loader2 size={14} className="discover-spinner" />
                    <span>Обработка...</span>
                  </>
                ) : (
                  <>
                    <Sparkles size={14} />
                    <span>Обработать и распределить ({importText.split("\n").filter((l) => l.trim()).length})</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </form>

        {importError && (
          <div className="discover-progress-block" style={{ borderLeft: "3px solid #ef4444" }}>
            <span style={{ color: "#ef4444", fontSize: "13px" }}>{importError}</span>
          </div>
        )}

        {importResult && (
          <div className="discover-import-results">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontWeight: 700, fontSize: "13px", color: "var(--color-ink)" }}>
                {importResult.dryRun ? "📋 Результат предпросмотра (Dry-Run)" : "✓ Импорт успешно выполнен и сохранён в PostgreSQL"}
              </span>
              <span style={{ fontSize: "12px", color: "var(--color-muted)" }}>
                Файл: {importResult.filename}
              </span>
            </div>

            <div className="discover-results-strip">
              <div className="discover-result-metric">
                <span>Всего распознано:</span>
                <strong>{importResult.uniqueValidCount}</strong>
              </div>
              <div className="discover-result-metric">
                <span>3 месяца (Nonseller):</span>
                <strong style={{ color: "#f59e0b" }}>{importResult.period3mCount}</strong>
              </div>
              <div className="discover-result-metric">
                <span>12 месяцев (Low Performer):</span>
                <strong style={{ color: "#f43f5e" }}>{importResult.period12mCount}</strong>
              </div>
              <div className="discover-result-metric">
                <span>Товары Afterbuy (фабрики):</span>
                <strong style={{ color: "var(--color-accent)" }}>{importResult.afterbuyCount}</strong>
              </div>
              <div className="discover-result-metric">
                <span>Напрямую из OTTO:</span>
                <strong style={{ color: "#38bdf8" }}>{importResult.directOttoCount}</strong>
              </div>
              {importResult.skippedExistingCount > 0 && (
                <div className="discover-result-metric">
                  <span>Пропущено дубликатов:</span>
                  <strong style={{ color: "var(--color-muted)" }}>{importResult.skippedExistingCount}</strong>
                </div>
              )}
            </div>

            {importResult.samplePreview && importResult.samplePreview.length > 0 && (
              <div style={{ marginTop: "8px", overflowX: "auto" }}>
                <table className="catalog-product-table" style={{ fontSize: "11px" }}>
                  <thead>
                    <tr>
                      <th>EAN / SKU</th>
                      <th>Период</th>
                      <th>Источник</th>
                      <th>Фабрика</th>
                      <th>Название</th>
                    </tr>
                  </thead>
                  <tbody>
                    {importResult.samplePreview.slice(0, 5).map((p: any, idx: number) => (
                      <tr key={idx}>
                        <td><code>{p.identifier}</code></td>
                        <td>
                          <span className={`period-pill ${p.period === "3M" ? "pill-3m" : "pill-12m"}`}>
                            {p.period}
                          </span>
                        </td>
                        <td>{p.source_type === "AFTERBUY" ? "Afterbuy" : "Прямой OTTO"}</td>
                        <td>{p.factory_name || "—"}</td>
                        <td style={{ maxWidth: "250px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {p.title}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </section>

      {/* 4. Two Core Distribution Cards (Options) */}
      <section
        className="discover-options-section"
        aria-label="Варианты распределения товаров 3/12M"
      >
        <div className="discover-section-heading">
          <h2>Два контура каталога Restart</h2>
          <p>
            Автоматическое распределение найденных товаров в зависимости от
            присутствия в системе Aftercool
          </p>
        </div>

        <div className="discover-options-grid">
          {/* Card 1: Direct OTTO */}
          <article className="discover-option-card">
            <div className="discover-option-top">
              <div className="discover-option-badge-row">
                <span className="discover-source-chip chip-direct">
                  Не найден в Aftercool
                </span>
                <span className="discover-option-number">Вариант 1</span>
              </div>

              <div className="discover-option-header-content">
                <div className="discover-option-icon-box icon-box-direct">
                  <Globe size={22} />
                </div>
                <div className="discover-option-title-wrap">
                  <h3>Товары напрямую из ОТТО</h3>
                  <p>Каталог прямых карточек маркетплейса</p>
                </div>
              </div>

              <p className="discover-option-body">
                Товары, которые были выставлены непосредственно через OTTO API или
                партнёрский портал и отсутствуют в Aftercool. Для них не
                требуется привязка к фабрике, но они подлежат полной подготовке к
                перезапуску (цена, ИИ-название, категория, атрибуты).
              </p>

              <div className="discover-option-metrics">
                <div className="discover-metric-pill">
                  <span>Кандидатов 3M:</span>
                  <strong className="tabular-nums">{directData.metrics.count3m}</strong>
                </div>
                <div className="discover-metric-pill">
                  <span>Кандидатов 12M:</span>
                  <strong className="tabular-nums">{directData.metrics.count12m}</strong>
                </div>
                <div className="discover-metric-pill">
                  <span>Всего:</span>
                  <strong className="tabular-nums">{directData.metrics.total}</strong>
                </div>
              </div>
            </div>

            <Link href="/direct-otto" className="discover-option-btn">
              <span>Открыть товары напрямую из ОТТО</span>
              <ArrowRight size={15} />
            </Link>
          </article>

          {/* Card 2: Afterbuy / Aftercool */}
          <article className="discover-option-card">
            <div className="discover-option-top">
              <div className="discover-option-badge-row">
                <span className="discover-source-chip chip-afterbuy">
                  Идентифицирован в Aftercool
                </span>
                <span className="discover-option-number">Вариант 2</span>
              </div>

              <div className="discover-option-header-content">
                <div className="discover-option-icon-box icon-box-afterbuy">
                  <Factory size={22} />
                </div>
                <div className="discover-option-title-wrap">
                  <h3>Товары из Afterbuy</h3>
                  <p>Распределение по фабрикам-производителям</p>
                </div>
              </div>

              <p className="discover-option-body">
                Позиции, найденные в базе Aftercool по EAN и артикулу Lister.
                Система автоматически привязывает их к фабрикам, отбирая только
                актуальные 3M/12M кандидаты (товары без отключения исключаются).
              </p>

              <div className="discover-option-metrics">
                <div className="discover-metric-pill">
                  <span>Фабрик с 3/12M:</span>
                  <strong className="tabular-nums">
                    {factoryData.metrics.totalFactories}
                  </strong>
                </div>
                <div className="discover-metric-pill">
                  <span>Кандидатов 3M:</span>
                  <strong className="tabular-nums">{factoryData.metrics.count3m}</strong>
                </div>
                <div className="discover-metric-pill">
                  <span>Всего товаров:</span>
                  <strong className="tabular-nums">
                    {factoryData.metrics.totalCandidates}
                  </strong>
                </div>
              </div>
            </div>

            <Link href="/afterbuy" className="discover-option-btn">
              <span>Открыть каталоги фабрик Afterbuy</span>
              <ArrowRight size={15} />
            </Link>
          </article>
        </div>
      </section>

      {/* 5. Architecture & Pipeline Card */}
      <section
        className="discover-architecture-card"
        aria-label="Схема работы алгоритма выявления"
      >
        <div className="discover-architecture-header">
          <Sparkles size={18} style={{ color: "var(--color-accent)" }} />
          <h3>Архитектура выявления и эндпоинты Aftercool</h3>
        </div>

        <div className="discover-steps-grid">
          <div className="discover-step-item">
            <div className="discover-step-head">
              <span className="discover-step-number">1</span>
              <span>Поиск в OTTO API</span>
            </div>
            <div className="discover-step-title">Выявление 3M/12M</div>
            <p className="discover-step-desc">
              Запрос статусов вариантов в OTTO Marketplace API. Поиск позиций с
              кодом <code>602016</code> (Nonseller 3 мес. или Low Performer 12 мес.).
            </p>
            <code className="discover-endpoint-code">
              GET /v5/products/&#123;sku&#125;/marketplace-status
            </code>
          </div>

          <div className="discover-step-item">
            <div className="discover-step-head">
              <span className="discover-step-number">2</span>
              <span>Сверка с Aftercool</span>
            </div>
            <div className="discover-step-title">Проверка принадлежности</div>
            <p className="discover-step-desc">
              Проверка исходного EAN в системе Равиля (Aftercool). Поиск по
              каталогу Lister и изображениям галереи.
            </p>
            <code className="discover-endpoint-code">
              GET /api/products/by-ean?ean=&#123;ean&#125;
            </code>
            <code className="discover-endpoint-code">
              GET /api/products?dataset=Lister
            </code>
          </div>

          <div className="discover-step-item">
            <div className="discover-step-head">
              <span className="discover-step-number">3</span>
              <span>Маршрутизация</span>
            </div>
            <div className="discover-step-title">Распределение по каталогам</div>
            <p className="discover-step-desc">
              <strong>Найден в Aftercool:</strong> отправляется в «Товары Afterbuy»
              с привязкой к <code>factory_id</code>.<br />
              <strong>Не найден:</strong> отправляется в «Товары напрямую из
              ОТТО».
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}
