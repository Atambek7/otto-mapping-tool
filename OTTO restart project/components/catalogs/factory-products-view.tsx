"use client";

import Link from "next/link";
import { ArrowLeft, Check, RefreshCw } from "lucide-react";
import { catalogDemoService } from "@/features/catalogs/service";
import { ApiState } from "@/components/api-state";
import { CatalogProductsTable } from "./catalog-products-table";
import { useCatalogProducts } from "@/features/catalogs/use-catalog-products";
import { useTranslation } from "@/features/i18n";

export function FactoryProductsView({ factoryId }: { factoryId: string }) {
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
  } = useCatalogProducts({ factoryId, source: "AFTERBUY" });

  const factory = isDemo ? catalogDemoService.getFactoryById(factoryId, account) : undefined;
  const factoryName = factory ? factory.name : items[0]?.factoryName || `Фабрика #${factoryId}`;

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
      <nav className="catalog-breadcrumb" aria-label={t("products.breadcrumbFactories")}>
        <Link href="/afterbuy">{t("products.breadcrumbFactories")}</Link>
        <span className="breadcrumb-separator">/</span>
        <span className="current">{factoryName}</span>
      </nav>

      {/* Header with back button */}
      <div className="factory-header">
        <div className="factory-header-left">
          <Link
            href="/afterbuy"
            className="back-circle-btn"
            title={t("common.back")}
            aria-label={t("common.back")}
          >
            <ArrowLeft size={18} />
          </Link>
          <div className="factory-title-block">
            <h1>{factoryName}</h1>
            <p className="tabular-nums">
              {metrics.total} {t("factories.products312M")} ({t("topbar.account")} {account})
            </p>
          </div>
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
        sourceType="AFTERBUY"
        factoryId={factoryId}
        factoryName={factoryName}
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
