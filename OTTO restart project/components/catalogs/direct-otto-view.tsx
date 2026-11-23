"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, Image as ImageIcon, Loader2, RefreshCw, Upload, X } from "lucide-react";
import { ApiState } from "@/components/api-state";
import { RestartApiClient, RestartApiError } from "@/features/api/client";
import { useTranslation } from "@/features/i18n";
import { CatalogProductsTable } from "./catalog-products-table";
import { useCatalogProducts } from "@/features/catalogs/use-catalog-products";

const UI_MESSAGE_SOURCE = "ottorestart-ui";
const EXTENSION_MESSAGE_SOURCE = "ottorestart-extension";

export function DirectOttoView() {
  const { t } = useTranslation();
  const {
    account,
    isDemo,
    hasLoaded,
    serverData,
    setServerPage,
    updateServerFilters,
    items,
    metrics,
    loading,
    error,
    toastMessage,
    showToast,
    onRefresh,
    handleAssignEans,
    handleApplyAiTitles,
    handleTitleSave,
    handlePriceSave,
    handleCategorySave,
    handleDrawerSave,
    handleSyncFromOtto,
  } = useCatalogProducts({ source: "OTTO_DIRECT" });

  const api = useMemo(() => new RestartApiClient(), []);
  const [extensionReady, setExtensionReady] = useState(false);
  const [portalBusy, setPortalBusy] = useState(false);
  const [portalStatusText, setPortalStatusText] = useState<string | null>(null);
  const [portalError, setPortalError] = useState<string | null>(null);
  const [isPortalModalOpen, setIsPortalModalOpen] = useState(false);
  const [csvText, setCsvText] = useState("");
  const [overwriteExisting, setOverwriteExisting] = useState(false);
  const [savingCsv, setSavingCsv] = useState(false);
  const activeRequestIdRef = useRef<string | null>(null);
  const handshakeTimerRef = useRef<number | null>(null);

  const clearHandshakeTimer = useCallback(() => {
    if (handshakeTimerRef.current !== null) {
      window.clearTimeout(handshakeTimerRef.current);
      handshakeTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      const data = event.data as Record<string, unknown> | null;
      if (!data || data.source !== EXTENSION_MESSAGE_SOURCE) return;

      if (data.type === "OTTO_RESTART_EXTENSION_PONG" || data.type === "OTTO_RESTART_EXTENSION_READY") {
        setExtensionReady(true);
        return;
      }

      const reqId = typeof data.requestId === "string" ? data.requestId : "";
      if (activeRequestIdRef.current && reqId && reqId !== activeRequestIdRef.current) return;

      if (data.type === "OTTO_RESTART_PORTAL_MEDIA_ACK" || data.type === "OTTO_RESTART_PORTAL_MEDIA_PROGRESS") {
        clearHandshakeTimer();
        setExtensionReady(true);
        setPortalBusy(true);
        if (typeof data.message === "string" && data.message) {
          setPortalStatusText(data.message);
        }
        return;
      }

      if (data.type === "OTTO_RESTART_PORTAL_MEDIA_RESULT") {
        clearHandshakeTimer();
        activeRequestIdRef.current = null;
        setPortalBusy(false);
        if (data.ok) {
          const updatedCount = Number(data.updatedCount ?? 0);
          const savedAssetsCount = Number(data.savedAssetsCount ?? 0);
          const totalMissing = Number(data.totalMissing ?? 0);
          setPortalError(null);
          setIsPortalModalOpen(false);
          if (totalMissing === 0 && updatedCount === 0) {
            setPortalStatusText("Нет товаров OTTO_DIRECT без фото для этого аккаунта.");
            showToast("Все прямые товары OTTO уже имеют фото.");
          } else {
            const msg = `Загружены фото из OTTO Portal: обновлено товаров ${updatedCount}, сохранено изображений ${savedAssetsCount}.`;
            setPortalStatusText(msg);
            showToast(msg, 4500);
            onRefresh();
          }
        } else {
          const errMsg = typeof data.error === "string" && data.error
            ? data.error
            : "Не удалось получить фото из OTTO Partner Connect.";
          setPortalError(errMsg);
          setPortalStatusText(null);
          setIsPortalModalOpen(true);
        }
      }
    };

    window.addEventListener("message", handleMessage);
    window.postMessage(
      { source: UI_MESSAGE_SOURCE, type: "OTTO_RESTART_EXTENSION_PING" },
      window.location.origin,
    );
    return () => {
      window.removeEventListener("message", handleMessage);
      clearHandshakeTimer();
    };
  }, [clearHandshakeTimer, onRefresh, showToast]);

  const handleFetchPortalMedia = useCallback(() => {
    if (isDemo) {
      showToast("В демо-режиме синхронизация с OTTO Portal отключена.");
      return;
    }
    clearHandshakeTimer();
    const requestId = `pm-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    activeRequestIdRef.current = requestId;
    setPortalError(null);
    setPortalStatusText("Ожидание ответа Chrome-расширения OttoRestart...");
    setPortalBusy(true);

    window.postMessage(
      {
        source: UI_MESSAGE_SOURCE,
        type: "OTTO_RESTART_FETCH_PORTAL_MEDIA",
        requestId,
        account,
      },
      window.location.origin,
    );

    handshakeTimerRef.current = window.setTimeout(() => {
      if (activeRequestIdRef.current !== requestId) return;
      activeRequestIdRef.current = null;
      setPortalBusy(false);
      setPortalStatusText(null);
      setPortalError(
        extensionReady
          ? "Расширение не ответило вовремя. Перезагрузите расширение в chrome://extensions и вкладку OttoRestart."
          : "Chrome-расширение OttoRestart Media Bridge не обнаружено в этом браузере.",
      );
      setIsPortalModalOpen(true);
    }, 900);
  }, [account, clearHandshakeTimer, extensionReady, isDemo, showToast]);

  const handleCsvFileChange = useCallback(async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      const content = await file.text();
      setCsvText(content);
      setPortalError(null);
    } catch {
      setPortalError("Не удалось прочитать CSV-файл.");
    } finally {
      event.target.value = "";
    }
  }, []);

  const handleSubmitCsv = useCallback(async () => {
    if (!csvText.trim()) return;
    setSavingCsv(true);
    setPortalError(null);
    try {
      const res = await api.savePortalMediaAssets(account, {
        csvText,
        overwrite: overwriteExisting,
      });
      setIsPortalModalOpen(false);
      setCsvText("");
      const msg = `Импорт фото завершён (${res.account}): обновлено товаров ${res.updatedCount}, сохранено изображений ${res.savedAssetsCount}.`;
      setPortalStatusText(msg);
      showToast(msg, 4500);
      onRefresh();
    } catch (err) {
      const message = err instanceof RestartApiError
        ? err.message
        : err instanceof Error
          ? err.message
          : "Ошибка сохранения фото из CSV";
      setPortalError(message);
    } finally {
      setSavingCsv(false);
    }
  }, [account, api, csvText, onRefresh, overwriteExisting, showToast]);

  if (loading && !hasLoaded) {
    return <ApiState kind="loading" label={t("common.loading")} />;
  }

  if (error && !hasLoaded) {
    return (
      <ApiState
        kind="error"
        title={t("common.error")}
        description={error}
        onRetry={onRefresh}
      />
    );
  }

  return (
    <div className="catalog-page">
      {/* Breadcrumb */}
      <nav className="catalog-breadcrumb" aria-label="Breadcrumb">
        <Link href="/afterbuy">{t("nav.sectionCatalogs")}</Link>
        <span className="breadcrumb-separator">/</span>
        <span className="current">{t("directOtto.title")}</span>
      </nav>

      {/* Header */}
      <div className="factory-header">
        <div className="factory-title-block">
          <h1>{t("directOtto.title")}</h1>
          <p className="tabular-nums">
            {t("directOtto.subtitle")} ({account})
          </p>
        </div>

        <div className="factory-header-actions">
          <button
            type="button"
            className="catalog-action-btn"
            onClick={handleFetchPortalMedia}
            disabled={portalBusy}
            title={extensionReady ? "Синхронизировать фото через Chrome-расширение" : t("directOtto.fetchPortalMediaBtn")}
          >
            {portalBusy ? <Loader2 size={14} className="spin" /> : <ImageIcon size={14} />}
            <span>{portalBusy ? t("directOtto.fetchingPortalMediaBtn") : t("directOtto.fetchPortalMediaBtn")}</span>
          </button>
          <button
            type="button"
            className="catalog-action-btn"
            onClick={onRefresh}
            title={t("common.refresh")}
          >
            <RefreshCw size={14} />
            <span>{t("common.refresh")}</span>
          </button>
        </div>
      </div>

      {portalStatusText && (
        <div className="portal-media-status-banner" role="status">
          {portalBusy ? <Loader2 size={14} className="spin" /> : <ImageIcon size={14} />}
          <span>{portalStatusText}</span>
        </div>
      )}

      {/* Toast Notification */}
      {toastMessage && (
        <div className="catalog-toast" role="status">
          <Check size={16} />
          <span>{toastMessage}</span>
        </div>
      )}

      {/* Unified Catalog Products Table */}
      {error && <ApiState kind="error" title={t("common.error")} description={error} onRetry={onRefresh} />}
      <CatalogProductsTable
        key={account}
        demoMode={isDemo}
        sourceType="DIRECT_OTTO"
        factoryId={isDemo ? "direct-otto" : undefined}
        factoryName={t("directOtto.title")}
        items={items}
        metrics={metrics}
        onRefresh={onRefresh}
        onTitleSave={handleTitleSave}
        onPriceSave={handlePriceSave}
        onCategorySave={handleCategorySave}
        onDrawerSave={handleDrawerSave}
        onAssignDemoEan={handleAssignEans}
        onApplyAiTitles={handleApplyAiTitles}
        onApplyAiAttributes={() => showToast("Атрибуты проверены по снимку OTTO.")}
        onApplyAiCategories={() => showToast("Категории проверены по каталогу OTTO.")}
        onSyncFromOtto={handleSyncFromOtto}
        showToast={showToast}
        serverPage={!isDemo && hasLoaded && serverData ? { ...serverData.page, over70Count: serverData.metrics?.over70Count ?? 0, categories: serverData.metrics?.categories ?? [] } : undefined}
        serverLoading={loading || Boolean(error)}
        onServerFiltersChange={updateServerFilters}
        onServerPageChange={setServerPage}
      />

      {isPortalModalOpen && (
        <div
          className="drip-modal-backdrop"
          onClick={() => {
            if (!savingCsv) setIsPortalModalOpen(false);
          }}
          role="dialog"
          aria-modal="true"
          aria-labelledby="portal-media-modal-title"
        >
          <div className="drip-modal-window" onClick={(e) => e.stopPropagation()}>
            <div className="drip-modal-header">
              <div className="drip-header-left">
                <div className="drip-icon-box">
                  <ImageIcon size={20} />
                </div>
                <div>
                  <div className="drip-title-row">
                    <h2 id="portal-media-modal-title" className="drip-title-h2">
                      {t("directOtto.portalModalTitle")}
                    </h2>
                    <span className="drip-account-chip">{account}</span>
                  </div>
                </div>
              </div>
              <button
                type="button"
                className="drip-close-btn"
                onClick={() => setIsPortalModalOpen(false)}
                disabled={savingCsv}
                aria-label={t("common.close")}
              >
                <X size={16} />
              </button>
            </div>

            <div className="drip-modal-body" style={{ padding: 20 }}>
              <p style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 12 }}>
                {t("directOtto.portalModalDesc")}
              </p>

              <div className="portal-media-help-box">
                <div>
                  <strong>1. Автоматический способ (Chrome Extension):</strong>{" "}
                  {extensionReady
                    ? "расширение обнаружено — убедитесь, что рядом открыта авторизованная вкладка OTTO Partner Connect (portal.otto.market), и нажмите «Повторить через расширение»."
                    : "установите распакованное расширение из папки OTTO restart project/extension в chrome://extensions (Developer mode → Load unpacked), откройте portal.otto.market и обновите эту страницу."}
                </div>
                <div style={{ marginTop: 6 }}>
                  <strong>2. Запасной способ (CSV):</strong>{" "}
                  загрузите CSV, выгруженный F12-скриптом из OTTO Partner Connect, или вставьте его содержимое ниже.
                </div>
              </div>

              {portalError && (
                <div
                  style={{
                    marginBottom: 12,
                    padding: "10px 12px",
                    borderRadius: 8,
                    background: "rgba(239, 68, 68, 0.12)",
                    border: "1px solid rgba(239, 68, 68, 0.35)",
                    color: "#fca5a5",
                    fontSize: 12,
                  }}
                >
                  {portalError}
                </div>
              )}

              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, marginBottom: 8, flexWrap: "wrap" }}>
                <label htmlFor="portal-media-csv-input" style={{ fontSize: 12, fontWeight: 600 }}>
                  {t("directOtto.portalPasteLabel")}
                </label>
                <label className="catalog-action-btn" style={{ cursor: "pointer" }}>
                  <Upload size={13} />
                  <span>Выбрать .csv</span>
                  <input
                    type="file"
                    accept=".csv,text/csv"
                    onChange={handleCsvFileChange}
                    style={{ display: "none" }}
                  />
                </label>
              </div>

              <textarea
                id="portal-media-csv-input"
                rows={7}
                value={csvText}
                onChange={(e) => setCsvText(e.target.value)}
                placeholder={'ean,productUuid,position,filename,assetId,url,originalUrl,status,error\n4062292451157,uuid,0,main.jpg,asset123,https://i.otto.de/i/otto/asset123,,ASSET_FOUND,'}
                style={{
                  width: "100%",
                  padding: 10,
                  borderRadius: 8,
                  border: "1px solid rgba(255, 255, 255, 0.14)",
                  background: "rgba(2, 6, 23, 0.65)",
                  color: "#f8fafc",
                  fontFamily: "monospace",
                  fontSize: 12,
                  boxSizing: "border-box",
                }}
              />

              <label style={{ display: "inline-flex", alignItems: "center", gap: 8, marginTop: 10, fontSize: 12, cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={overwriteExisting}
                  onChange={(e) => setOverwriteExisting(e.target.checked)}
                />
                <span>Перезаписывать уже сохранённое фото товара</span>
              </label>
            </div>

            <div className="drip-modal-footer" style={{ padding: "14px 20px", display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
              <button
                type="button"
                className="drip-btn-secondary"
                onClick={handleFetchPortalMedia}
                disabled={portalBusy || savingCsv}
              >
                {portalBusy ? t("directOtto.fetchingPortalMediaBtn") : "Повторить через расширение"}
              </button>
              <div style={{ display: "flex", gap: 10 }}>
                <button
                  type="button"
                  className="drip-btn-secondary"
                  onClick={() => setIsPortalModalOpen(false)}
                  disabled={savingCsv}
                >
                  {t("common.cancel")}
                </button>
                <button
                  type="button"
                  className="drip-btn-primary"
                  disabled={savingCsv || !csvText.trim()}
                  onClick={() => void handleSubmitCsv()}
                >
                  {savingCsv ? t("common.loading") : t("directOtto.portalSubmitBtn")}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
