"use client";

import Link from "next/link";
import { Check, RefreshCw } from "lucide-react";
import { ApiState } from "@/components/api-state";
import { useTranslation } from "@/features/i18n";
import { CatalogProductsTable } from "./catalog-products-table";
import { useCatalogProducts } from "@/features/catalogs/use-catalog-products";

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
            onClick={onRefresh}
            title={t("common.refresh")}
          >
            <RefreshCw size={14} />
            <span>{t("common.refresh")}</span>
          </button>
        </div>
      </div>

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
        factoryId="direct-otto"
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
    </div>
  );
}
