"use client";

import {
  Check,
  CloudDownload,
  Copy,
  ExternalLink,
  Layers3,
  RefreshCw,
  SlidersHorizontal,
  Sparkles,
  Tag,
  X,
} from "lucide-react";
import type { CatalogProductItem } from "@/features/catalogs/types";
import { formatPriceEur, calculateRestartPriceCents } from "@/features/catalogs/service";

interface ProductAttributesModalProps {
  product: CatalogProductItem | null;
  onClose: () => void;
  onSyncProduct?: (id: string) => void;
  onAiAttributes?: (id: string) => void;
}

export function ProductAttributesModal({
  product,
  onClose,
  onSyncProduct,
  onAiAttributes,
}: ProductAttributesModalProps) {
  if (!product) return null;

  const restartPriceCents = calculateRestartPriceCents(
    product.oldStandardPriceCents,
    product.account
  );

  const attributesList = Object.entries(product.attributes).filter(
    ([_, val]) => val !== undefined && val !== ""
  );

  return (
    <div className="product-modal-backdrop" onClick={onClose} role="dialog" aria-modal="true">
      <div className="product-modal-dialog" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="product-modal-header">
          <div className="modal-header-info">
            <span className="modal-header-tag">
              {product.isSyncedWithOtto ? (
                <span className="sync-status-badge badge-synced">
                  <CloudDownload size={13} /> Карточка синхронизирована с ОТТО
                </span>
              ) : product.hasAiAttributes ? (
                <span className="sync-status-badge badge-ai">
                  <Sparkles size={13} /> Атрибуты сформированы ИИ
                </span>
              ) : (
                <span className="sync-status-badge badge-pending">
                  Требуется синхронизация атрибутов
                </span>
              )}
            </span>
            <h2 className="modal-product-title">{product.title}</h2>
            <div className="modal-product-meta">
              <span>EAN: <strong>{product.ean}</strong></span>
              <span>·</span>
              <span>Фабрика: <strong>{product.factoryName || product.factoryId || "Прямая выгрузка"}</strong></span>
              <span>·</span>
              <span>Аккаунт: <strong>{product.account}</strong></span>
            </div>
          </div>

          <button
            type="button"
            className="modal-close-btn"
            onClick={onClose}
            aria-label="Закрыть модальное окно"
          >
            <X size={18} />
          </button>
        </div>

        {/* Body Content */}
        <div className="product-modal-body">
          {/* Top Banner if synced with OTTO */}
          {product.isSyncedWithOtto && (
            <div className="otto-sync-banner">
              <div className="sync-banner-icon">
                <CloudDownload size={20} />
              </div>
              <div className="sync-banner-text">
                <strong>Данные успешно получены из каталога OTTO</strong>
                <p>
                  Категория и {attributesList.length} подтверждённых атрибутов подтянуты из актуальной карточки маркетплейса OTTO.
                  {product.syncedAt && ` Синхронизировано: ${new Date(product.syncedAt).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}.`}
                </p>
              </div>
            </div>
          )}

          {/* Category Section */}
          <div className="modal-section">
            <div className="modal-section-title">
              <Tag size={15} />
              <span>Категория OTTO</span>
            </div>
            <div className="modal-category-box">
              <span className="category-hierarchy">
                {product.category || "Без категории"}
              </span>
              {product.isSyncedWithOtto && (
                <span className="verified-otto-pill">
                  <Check size={12} /> OTTO Verified
                </span>
              )}
            </div>
          </div>

          {/* Attributes Grid */}
          <div className="modal-section">
            <div className="modal-section-title">
              <SlidersHorizontal size={15} />
              <span>Характеристики и атрибуты ({attributesList.length})</span>
            </div>

            <div className="attributes-grid">
              {attributesList.length > 0 ? (
                attributesList.map(([key, val]) => (
                  <div className="attribute-card" key={key}>
                    <span className="attribute-key">{key}</span>
                    <strong className="attribute-val">{val}</strong>
                  </div>
                ))
              ) : (
                <div className="attributes-empty">
                  <p>Атрибуты ещё не заполнены. Нажмите «Синхронизация с ОТТО» или «Ai attributes».</p>
                </div>
              )}
            </div>
          </div>

          {/* Pricing & Restart Context */}
          <div className="modal-section">
            <div className="modal-section-title">
              <Layers3 size={15} />
              <span>Контекст Restart 3/12M</span>
            </div>
            <div className="restart-context-row">
              <div className="context-item">
                <span className="context-label">Стандартная цена (OTTO)</span>
                <strong className="context-val">{formatPriceEur(product.oldStandardPriceCents)}</strong>
              </div>
              <div className="context-item">
                <span className="context-label">Цена Restart</span>
                <strong className="context-val color-green">
                  {formatPriceEur(restartPriceCents)} (-{product.account === "JV" ? "10 €" : "15 €"})
                </strong>
              </div>
              <div className="context-item">
                <span className="context-label">Период деактивации</span>
                <span className={`period-pill ${product.period === "3M" ? "pill-3m" : "pill-12m"}`}>
                  {product.period} ({product.errorCode})
                </span>
              </div>
              <div className="context-item">
                <span className="context-label">Статус готовности</span>
                <span className="status-pill status-ready">
                  {product.status === "READY" ? "Готов к публикации" : product.status}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* Footer Actions */}
        <div className="product-modal-footer">
          <div className="modal-footer-left">
            <button
              type="button"
              className="modal-action-btn btn-ai"
              onClick={() => onAiAttributes && onAiAttributes(product.id)}
            >
              <Sparkles size={14} />
              Перегенерировать через ИИ
            </button>

            <button
              type="button"
              className="modal-action-btn btn-sync"
              onClick={() => onSyncProduct && onSyncProduct(product.id)}
            >
              <RefreshCw size={14} />
              Синхронизировать с ОТТО
            </button>
          </div>

          <button
            type="button"
            className="modal-btn-close"
            onClick={onClose}
          >
            Закрыть
          </button>
        </div>
      </div>
    </div>
  );
}
