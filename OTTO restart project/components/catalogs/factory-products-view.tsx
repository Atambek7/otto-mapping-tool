"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Check, RefreshCw } from "lucide-react";
import { useRestartAccount } from "@/features/state/restart-account";
import { catalogDemoService, formatPriceEur } from "@/features/catalogs/service";
import type { CatalogProductItem } from "@/features/catalogs/types";
import { CatalogProductsTable } from "./catalog-products-table";

export function FactoryProductsView({ factoryId }: { factoryId: string }) {
  const { account } = useRestartAccount();
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  const factory = useMemo(
    () => catalogDemoService.getFactoryById(factoryId, account),
    [factoryId, account]
  );

  const { items, metrics } = useMemo(
    () =>
      catalogDemoService.getProductsByFactory(
        factoryId,
        account,
        "",
        "",
        ""
      ),
    [factoryId, account, refreshTrigger]
  );

  function showToast(msg: string) {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(null), 3500);
  }

  function handleApplyAiTitles(selectedIds?: string[]) {
    let targetIds: string[] = [];
    if (selectedIds && selectedIds.length > 0) {
      targetIds = selectedIds;
    } else {
      const longTitles = items.filter((p) => p.title.length > 70);
      targetIds = longTitles.map((p) => p.id);
    }

    if (targetIds.length === 0) {
      showToast("Все названия уже соответствуют стандарту OTTO (≤ 70 симв.)");
      return;
    }

    const count = catalogDemoService.applyAiTitles(targetIds);
    setRefreshTrigger((n) => n + 1);
    showToast(`✓ ИИ оптимизировал названия для ${count} товаров (≤ 70 символов)`);
  }

  function handleApplyAiAttributes(selectedIds?: string[]) {
    const targetIds = selectedIds && selectedIds.length > 0 ? selectedIds : items.map((p) => p.id);
    const count = catalogDemoService.applyAiAttributes(targetIds);
    setRefreshTrigger((n) => n + 1);
    showToast(`✓ ИИ сформировал атрибуты (материал, цвет, габариты) для ${count} товаров`);
  }

  function handleApplyAiCategories(selectedIds?: string[]) {
    const targetIds = selectedIds && selectedIds.length > 0 ? selectedIds : items.map((p) => p.id);
    const count = catalogDemoService.applyAiCategories(targetIds);
    setRefreshTrigger((n) => n + 1);
    showToast(`✓ ИИ присвоил точные категории каталога OTTO для ${count} товаров`);
  }

  function handleSyncFromOtto(selectedIds?: string[]) {
    const targetIds = selectedIds && selectedIds.length > 0 ? selectedIds : items.map((p) => p.id);
    const count = catalogDemoService.syncFromOtto(targetIds);
    setRefreshTrigger((n) => n + 1);
    showToast(`✓ Синхронизировано с ОТТО: для ${count} товаров подтянуты категории и характеристики`);
  }

  function handleTitleSave(id: string, newTitle: string) {
    catalogDemoService.updateProductTitle(id, newTitle);
    setRefreshTrigger((n) => n + 1);
    showToast(`✓ Название товара обновлено (${newTitle.length}/70 симв.)`);
  }

  function handlePriceSave(id: string, newOldPriceCents: number) {
    catalogDemoService.updateProductPrice(id, newOldPriceCents);
    setRefreshTrigger((n) => n + 1);
    showToast(`✓ Цена обновлена: ${formatPriceEur(newOldPriceCents)}`);
  }

  function handleCategorySave(id: string, newCategory: string) {
    catalogDemoService.updateProductCategory(id, newCategory);
    setRefreshTrigger((n) => n + 1);
    showToast(`✓ Категория обновлена: ${newCategory}`);
  }

  function handleDrawerSave(updated: CatalogProductItem) {
    catalogDemoService.updateProduct(updated);
    setRefreshTrigger((n) => n + 1);
    showToast(`✓ Изменения карточки сохранены`);
  }

  const factoryName = factory ? factory.name : `Фабрика #${factoryId}`;

  return (
    <div className="catalog-page">
      {/* Breadcrumb */}
      <nav className="catalog-breadcrumb" aria-label="Хлебные крошки">
        <Link href="/afterbuy">Фабрики</Link>
        <span className="breadcrumb-separator">/</span>
        <span className="current">{factoryName}</span>
      </nav>

      {/* Header with back button */}
      <div className="factory-header">
        <div className="factory-header-left">
          <Link
            href="/afterbuy"
            className="back-circle-btn"
            title="Назад к списку фабрик"
            aria-label="Назад к списку фабрик"
          >
            <ArrowLeft size={18} />
          </Link>
          <div className="factory-title-block">
            <h1>{factoryName}</h1>
            <p>
              {metrics.total} товаров 3/12M в каталоге · {account}
            </p>
          </div>
        </div>

        <div className="factory-header-actions">
          <button
            type="button"
            className="catalog-action-btn"
            onClick={() => setRefreshTrigger((n) => n + 1)}
            title="Обновить данные каталога"
          >
            <RefreshCw size={14} />
            <span>Обновить</span>
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

      {/* Unified Catalog Products Table with OttoFlow UX & Hallmark Design */}
      <CatalogProductsTable
        sourceType="AFTERBUY"
        factoryId={factoryId}
        factoryName={factoryName}
        items={items}
        metrics={metrics}
        onRefresh={() => setRefreshTrigger((n) => n + 1)}
        onTitleSave={handleTitleSave}
        onPriceSave={handlePriceSave}
        onCategorySave={handleCategorySave}
        onDrawerSave={handleDrawerSave}
        onApplyAiTitles={handleApplyAiTitles}
        onApplyAiAttributes={handleApplyAiAttributes}
        onApplyAiCategories={handleApplyAiCategories}
        onSyncFromOtto={handleSyncFromOtto}
        showToast={showToast}
      />
    </div>
  );
}
