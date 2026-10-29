"use client";

import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";
import {
  X,
  Check,
  Save,
  Wand2,
  Sparkles,
  AlertTriangle,
  Pencil,
  Loader2,
  CloudDownload,
  Copy,
  ChevronDown,
  ChevronUp,
  Tag,
  SlidersHorizontal,
  RefreshCw,
  Search,
} from "lucide-react";
import type { CatalogProductItem } from "@/features/catalogs/types";
import {
  calculateRestartPriceCents,
  formatPriceEur,
} from "@/features/catalogs/service";
import { ImageHoverPreview, type HoverImagePreviewState } from "./image-hover-preview";

interface ProductDrawerProps {
  product: CatalogProductItem | null;
  onClose: () => void;
  onSaveProduct?: (updated: CatalogProductItem) => void;
  onSyncProduct?: (id: string) => void;
  onAiAttributes?: (id: string) => void;
}

const POPULAR_OTTO_CATEGORIES = [
  "Esstische",
  "Couchtische",
  "Stühle & Sessel",
  "Schreibtische",
  "Kommoden & Sideboards",
  "Kleiderschränke",
  "Betten & Matratzen",
  "Regale & Raumteiler",
  "TV- & Mediamöbel",
  "Gartenmöbel",
  "Polstermöbel",
];

// Schema for standard OTTO furniture attributes
interface AttrDefinition {
  name: string;
  label: string;
  unit?: string;
  required: boolean;
  options?: string[];
  placeholder?: string;
}

const STANDARD_FURNITURE_SCHEMA: AttrDefinition[] = [
  {
    name: "Marke",
    label: "Marke (Бренд)",
    required: true,
    placeholder: "z.B. OTTO Restart Selection",
  },
  {
    name: "Farbe",
    label: "Farbe (Цвет)",
    required: true,
    options: ["Schwarz", "Weiß", "Eiche", "Braun", "Grau", "Natur", "Anthrazit", "Beige"],
    placeholder: "Выберите цвет...",
  },
  {
    name: "Material",
    label: "Material (Материал)",
    required: true,
    options: ["Massivholz", "MDF", "Metall", "Glas", "Eiche massiv", "Spanplatte", "Samt", "Leder"],
    placeholder: "Выберите материал...",
  },
  {
    name: "Breite",
    label: "Breite (Ширина)",
    unit: "cm",
    required: true,
    placeholder: "z.B. 120",
  },
  {
    name: "Höhe",
    label: "Höhe (Высота)",
    unit: "cm",
    required: true,
    placeholder: "z.B. 75",
  },
  {
    name: "Tiefe",
    label: "Tiefe (Глубина)",
    unit: "cm",
    required: true,
    placeholder: "z.B. 80",
  },
  {
    name: "Lieferzustand",
    label: "Lieferzustand (Состояние поставки)",
    required: false,
    options: ["Zerlegt", "Teilmontiert", "Montiert"],
    placeholder: "z.B. Zerlegt",
  },
  {
    name: "Holzart",
    label: "Holzart (Порода дерева)",
    required: false,
    options: ["Eiche", "Kiefer", "Buche", "Akazie", "Nussbaum"],
    placeholder: "z.B. Wildeiche",
  },
  {
    name: "Oberflächenbehandlung",
    label: "Oberflächenbehandlung (Обработка)",
    required: false,
    options: ["Geölt", "Lackiert", "Gewachst", "Pulverbeschichtet"],
    placeholder: "z.B. Natur geölt",
  },
  {
    name: "Form",
    label: "Form (Форма)",
    required: false,
    options: ["Rechteckig", "Rund", "Quadratisch", "Oval"],
    placeholder: "z.B. Rechteckig",
  },
  {
    name: "Stil",
    label: "Stil (Стиль)",
    required: false,
    options: ["Modern", "Skandi", "Industrial", "Landhaus", "Klassisch"],
    placeholder: "z.B. Skandi",
  },
  {
    name: "Pflegehinweise",
    label: "Pflegehinweise (Инструкция по уходу)",
    required: false,
    placeholder: "z.B. Mit feuchtem Tuch abwischen",
  },
];

export function ProductDrawer({
  product,
  onClose,
  onSaveProduct,
  onSyncProduct,
  onAiAttributes,
}: ProductDrawerProps) {
  const [isMounted, setIsMounted] = useState(false);
  const [isClosing, setIsClosing] = useState(false);
  const [title, setTitle] = useState("");
  const [titleDraft, setTitleDraft] = useState("");
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [isGeneratingAiTitle, setIsGeneratingAiTitle] = useState(false);
  const [category, setCategory] = useState("");
  const [categorySearchQuery, setCategorySearchQuery] = useState("");
  const [isCategoryDropdownOpen, setIsCategoryDropdownOpen] = useState(false);
  const [attributes, setAttributes] = useState<Record<string, string>>({});
  const [aiFilledAttrs, setAiFilledAttrs] = useState<Set<string>>(new Set());
  const [showAllOptional, setShowAllOptional] = useState(false);
  const [copiedEan, setCopiedEan] = useState(false);
  const [saving, setSaving] = useState(false);
  const [imgPreview, setImgPreview] = useState<HoverImagePreviewState | null>(null);

  const categoryInputRef = useRef<HTMLInputElement>(null);
  const closeTimerRef = useRef<NodeJS.Timeout | null>(null);
  const isClosingRef = useRef(false);

  // Initialize state when product changes
  useEffect(() => {
    if (product) {
      setTitle(product.title);
      setTitleDraft(product.title);
      setIsEditingTitle(false);
      setCategory(product.category || "");
      setCategorySearchQuery(product.category || "");

      // Normalize attributes
      const initialAttrs: Record<string, string> = {};
      const aiAttrs = new Set<string>();

      if (product.attributes) {
        for (const [k, v] of Object.entries(product.attributes)) {
          if (v !== undefined && v !== null && String(v).trim()) {
            initialAttrs[k] = String(v);
            if (product.hasAiAttributes) {
              aiAttrs.add(k);
            }
          }
        }
      }

      setAttributes(initialAttrs);
      setAiFilledAttrs(aiAttrs);
      isClosingRef.current = false;
      setIsClosing(false);

      // Slide in animation
      const mountTimer = setTimeout(() => setIsMounted(true), 15);
      return () => clearTimeout(mountTimer);
    } else {
      isClosingRef.current = false;
      setIsClosing(false);
      setIsMounted(false);
    }
  }, [product]);

  // Clean up close timer only on unmount
  useEffect(() => {
    return () => {
      if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    };
  }, []);

  // Handle ESC key
  const handleClose = useCallback(() => {
    if (isClosingRef.current) return;
    isClosingRef.current = true;
    setIsClosing(true);
    if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    closeTimerRef.current = setTimeout(() => {
      isClosingRef.current = false;
      setIsClosing(false);
      onClose();
    }, 200);
  }, [onClose]);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        if (isEditingTitle) {
          setIsEditingTitle(false);
          setTitleDraft(title);
        } else if (isCategoryDropdownOpen) {
          setIsCategoryDropdownOpen(false);
        } else {
          handleClose();
        }
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [handleClose, isEditingTitle, isCategoryDropdownOpen, title]);

  if (!product) return null;

  // Copy EAN helper
  const handleCopyEan = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(product.ean);
    setCopiedEan(true);
    setTimeout(() => setCopiedEan(false), 2000);
  };

  // Inline Title Save
  const handleSaveTitle = () => {
    const clean = titleDraft.trim();
    if (!clean) return;
    setTitle(clean);
    setIsEditingTitle(false);
  };

  // Smart AI Title Compression (<= 70 chars for OTTO)
  const handleAiShortenTitle = () => {
    setIsGeneratingAiTitle(true);
    setTimeout(() => {
      let current = (titleDraft || title || "").trim();
      if (!current) {
        setIsGeneratingAiTitle(false);
        return;
      }

      // If already under 70, polish format
      if (current.length <= 70) {
        setTitleDraft(current);
        setTitle(current);
        setIsGeneratingAiTitle(false);
        return;
      }

      // Intelligent furniture title shortening algorithm
      let optimized = current
        // Remove repetitive brand prefix if excessive
        .replace(/^(OTTO Restart Selection|OTTO|Afterbuy|Direct OTTO)\s*[-–:]\s*/i, "")
        // Clean up redundant noise words
        .replace(/\b(wunderschöner?|hochwertiger?|exklusiver?|toller?|praktischer?|modernes?)\b/gi, "")
        // Normalize spaces
        .replace(/\s{2,}/g, " ")
        .trim();

      if (optimized.length > 70) {
        // Cut at word boundary
        const words = optimized.split(" ");
        let result = "";
        for (const w of words) {
          if ((result + (result ? " " : "") + w).length <= 70) {
            result += (result ? " " : "") + w;
          } else {
            break;
          }
        }
        optimized = result || optimized.slice(0, 70);
      }

      setTitleDraft(optimized);
      setTitle(optimized);
      setIsGeneratingAiTitle(false);
    }, 300);
  };

  // Stage 1: Category selection
  const filteredCategories = POPULAR_OTTO_CATEGORIES.filter((c) =>
    c.toLowerCase().includes(categorySearchQuery.toLowerCase())
  );

  const handleSelectCategory = (catName: string) => {
    setCategory(catName);
    setCategorySearchQuery(catName);
    setIsCategoryDropdownOpen(false);
  };

  // Stage 1 Auto AI Category
  const handleAutoAiCategory = () => {
    const titleLower = title.toLowerCase();
    let bestMatch = "Esstische";
    if (titleLower.includes("couchtisch") || titleLower.includes("beistelltisch")) {
      bestMatch = "Couchtische";
    } else if (titleLower.includes("stuhl") || titleLower.includes("sessel") || titleLower.includes("hocker")) {
      bestMatch = "Stühle & Sessel";
    } else if (titleLower.includes("schreibtisch")) {
      bestMatch = "Schreibtische";
    } else if (titleLower.includes("kommode") || titleLower.includes("sideboard")) {
      bestMatch = "Kommoden & Sideboards";
    } else if (titleLower.includes("regal") || titleLower.includes("raumteiler")) {
      bestMatch = "Regale & Raumteiler";
    } else if (titleLower.includes("bett") || titleLower.includes("matratze")) {
      bestMatch = "Betten & Matratzen";
    } else if (titleLower.includes("schrank") || titleLower.includes("kleiderschrank")) {
      bestMatch = "Kleiderschränke";
    }
    handleSelectCategory(bestMatch);
  };

  // Stage 2 Attribute value change
  const handleAttrChange = (key: string, value: string) => {
    setAttributes((prev) => ({
      ...prev,
      [key]: value,
    }));
  };

  // Stage 2 Auto AI Attributes
  const handleTriggerAiAttributes = () => {
    onAiAttributes?.(product.id);

    // Provide immediate optimistic attributes in drawer
    const newAttrs = { ...attributes };
    const newAiSet = new Set(aiFilledAttrs);

    if (!newAttrs["Marke"]) {
      newAttrs["Marke"] = "OTTO Restart Selection";
      newAiSet.add("Marke");
    }
    if (!newAttrs["Farbe"]) {
      newAttrs["Farbe"] = "Eiche Natur";
      newAiSet.add("Farbe");
    }
    if (!newAttrs["Material"]) {
      newAttrs["Material"] = "Massivholz";
      newAiSet.add("Material");
    }
    if (!newAttrs["Breite"]) {
      newAttrs["Breite"] = "120";
      newAiSet.add("Breite");
    }
    if (!newAttrs["Höhe"]) {
      newAttrs["Höhe"] = "76";
      newAiSet.add("Höhe");
    }
    if (!newAttrs["Tiefe"]) {
      newAttrs["Tiefe"] = "80";
      newAiSet.add("Tiefe");
    }

    setAttributes(newAttrs);
    setAiFilledAttrs(newAiSet);
  };

  // Full Drawer Save
  const handleSave = () => {
    setSaving(true);
    setTimeout(() => {
      onSaveProduct?.({
        ...product,
        title,
        category,
        attributes,
        hasAiAttributes: aiFilledAttrs.size > 0,
        isSyncedWithOtto: product.isSyncedWithOtto || !!category,
      });
      setSaving(false);
      handleClose();
    }, 280);
  };

  // Calculations for Pricing
  const restartPriceCents = calculateRestartPriceCents(
    product.oldStandardPriceCents,
    product.account
  );
  const discountAmount = product.account === "JV" ? "10 €" : "15 €";

  const isTitleExceeded = title.length > 70;
  const isDraftExceeded = titleDraft.length > 70;

  // Schema partitioning: required vs optional
  const requiredSchema = STANDARD_FURNITURE_SCHEMA.filter((s) => s.required);
  const optionalSchema = STANDARD_FURNITURE_SCHEMA.filter((s) => !s.required);

  const filledOptional = optionalSchema.filter((s) => !!attributes[s.name]);
  const emptyOptional = optionalSchema.filter((s) => !attributes[s.name]);

  return (
    <>
      {/* Integrated backdrop with smooth fade transition */}
      <div
        className={`drawer-backdrop-overlay ${isMounted && !isClosing ? "backdrop-open" : "backdrop-closed"}`}
        onClick={handleClose}
        aria-hidden="true"
      />

      {/* Drawer Panel */}
      <aside
        className={`ottoflow-product-drawer ${isMounted && !isClosing ? "drawer-open" : "drawer-closed"}`}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={`Редактирование товара ${title}`}
      >
        {/* =========================================================
            1. HEADER (OttoFlow style)
            ========================================================= */}
        <div className="drawer-header-row">
          <div className="drawer-header-title-wrap">
            <h2 className="drawer-header-h2">Редактирование товара</h2>
            <span className="drawer-account-chip">
              Аккаунт: <strong>{product.account}</strong>
            </span>
          </div>

          <button
            type="button"
            onClick={handleClose}
            className="drawer-close-icon-btn"
            title="Закрыть панель (Esc)"
            aria-label="Закрыть панель"
          >
            <X size={16} />
          </button>
        </div>

        {/* =========================================================
            SCROLLABLE BODY
            ========================================================= */}
        <div className="drawer-scroll-body">
          {/* =========================================================
              2. PRODUCT INFO CARD (Top block with Zoom Hover)
              ========================================================= */}
          <div className="drawer-product-summary-card">
            <div className="drawer-summary-flex">
              {/* Product Thumbnail with Zoom Hover preview */}
              <div
                className="drawer-thumb-box"
                onMouseEnter={(e) => {
                  if (!product.imageUrl) return;
                  const r = e.currentTarget.getBoundingClientRect();
                  setImgPreview({
                    url: product.imageUrl,
                    name: title,
                    oldPrice: formatPriceEur(product.oldStandardPriceCents),
                    restartPrice: formatPriceEur(restartPriceCents),
                    ean: product.ean,
                    category,
                    rect: {
                      top: r.top,
                      left: r.left,
                      right: r.right,
                      bottom: r.bottom,
                      height: r.height,
                    },
                  });
                }}
                onMouseLeave={() => setImgPreview(null)}
                title="Наведите для увеличения фото товара"
              >
                {product.imageUrl ? (
                  <img
                    src={product.imageUrl}
                    alt={title}
                    className="drawer-thumb-img"
                  />
                ) : (
                  <span className="drawer-thumb-empty">—</span>
                )}
              </div>

              {/* Product Title & Identifiers */}
              <div className="drawer-summary-details">
                {/* Meta pills row */}
                <div className="drawer-badges-row">
                  <span className="drawer-ean-tag" title="Нажмите, чтобы скопировать EAN">
                    <span>{product.ean}</span>
                    <button
                      type="button"
                      className="drawer-inline-copy-btn"
                      onClick={handleCopyEan}
                      title="Скопировать EAN"
                    >
                      {copiedEan ? (
                        <Check size={11} style={{ color: "#22c55e" }} />
                      ) : (
                        <Copy size={11} />
                      )}
                    </button>
                  </span>

                  {/* Character limit pill */}
                  <span
                    className={`drawer-char-pill ${
                      isTitleExceeded ? "pill-exceeded" : "pill-valid"
                    }`}
                    title={
                      isTitleExceeded
                        ? `Превышен лимит 70 символов для OTTO (${title.length}/70)`
                        : `Длина названия: ${title.length}/70 симв.`
                    }
                  >
                    {isTitleExceeded && (
                      <AlertTriangle size={11} className="pill-warn-icon" />
                    )}
                    ({title.length}/70)
                  </span>

                  {/* Sync status badge */}
                  {product.isSyncedWithOtto ? (
                    <span className="drawer-sync-badge badge-synced">
                      <Check size={10} /> Синхр. с ОТТО
                    </span>
                  ) : product.hasAiAttributes ? (
                    <span className="drawer-sync-badge badge-ai">
                      <Sparkles size={10} /> Атрибуты ИИ
                    </span>
                  ) : (
                    <span className="drawer-sync-badge badge-pending">
                      Требует заполнения
                    </span>
                  )}
                </div>

                {/* Editable Title Section */}
                {isEditingTitle ? (
                  <div className="drawer-title-edit-box">
                    <textarea
                      value={titleDraft}
                      onChange={(e) => setTitleDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          handleSaveTitle();
                        } else if (e.key === "Escape") {
                          e.preventDefault();
                          setIsEditingTitle(false);
                          setTitleDraft(title);
                        }
                      }}
                      rows={2}
                      className={`drawer-title-textarea ${
                        isDraftExceeded ? "textarea-limit-exceeded" : ""
                      }`}
                      placeholder="Введите название товара для OTTO..."
                      autoFocus
                    />
                    <div className="drawer-title-edit-actions">
                      <span
                        className={`drawer-edit-char-counter ${
                          isDraftExceeded ? "counter-warn" : ""
                        }`}
                      >
                        {titleDraft.length}/70
                      </span>

                      <div className="drawer-edit-buttons-group">
                        <button
                          type="button"
                          onClick={handleAiShortenTitle}
                          disabled={isGeneratingAiTitle}
                          className="drawer-btn-ai-shorten"
                          title="Сжать и оптимизировать название до 70 символов через ИИ"
                        >
                          {isGeneratingAiTitle ? (
                            <Loader2 size={11} className="spin" />
                          ) : (
                            <Sparkles size={11} />
                          )}
                          <span>ИИ</span>
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setIsEditingTitle(false);
                            setTitleDraft(title);
                          }}
                          className="drawer-btn-cancel-edit"
                        >
                          Отмена
                        </button>
                        <button
                          type="button"
                          onClick={handleSaveTitle}
                          className="drawer-btn-save-edit"
                        >
                          <Check size={12} />
                          <span>Сохранить</span>
                        </button>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="drawer-title-display-box group">
                    <p
                      className={`drawer-title-p ${
                        isTitleExceeded ? "title-color-exceeded" : ""
                      }`}
                      title={title}
                    >
                      {title}
                    </p>
                    <button
                      type="button"
                      onClick={() => {
                        setTitleDraft(title);
                        setIsEditingTitle(true);
                      }}
                      className="drawer-pencil-edit-trigger"
                      title="Редактировать название товара"
                      aria-label="Редактировать название"
                    >
                      <Pencil size={12} />
                    </button>
                  </div>
                )}

                {/* Price Display */}
                <div className="drawer-price-indicator-row">
                  <span className="price-label-old">
                    Исходная: {formatPriceEur(product.oldStandardPriceCents)}
                  </span>
                  <span className="price-divider">→</span>
                  <span className="price-label-restart">
                    Restart: <strong>{formatPriceEur(restartPriceCents)}</strong>
                  </span>
                  <span className="price-discount-tag">(-{discountAmount})</span>
                </div>
              </div>
            </div>
          </div>

          {/* =========================================================
              3. WARNING / OVER-LIMIT BANNER (OttoFlow style)
              ========================================================= */}
          {isTitleExceeded && (
            <div className="drawer-alert-banner banner-warning">
              <div className="banner-alert-content">
                <AlertTriangle size={14} className="banner-icon-warn" />
                <span className="banner-alert-text">
                  <strong>Превышен лимит названия:</strong> {title.length}/70 символов.
                  Маркетплейс OTTO отклоняет товары с заголовком длиннее 70 символов.
                </span>
              </div>
              <button
                type="button"
                onClick={handleAiShortenTitle}
                disabled={isGeneratingAiTitle}
                className="banner-action-ai-btn"
                title="Сгенерировать название до 70 символов через ИИ"
              >
                {isGeneratingAiTitle ? (
                  <Loader2 size={11} className="spin" />
                ) : (
                  <Sparkles size={11} />
                )}
                <span>ИИ-сокращение</span>
              </button>
            </div>
          )}

          {/* =========================================================
              4. STAGE 1: CATEGORY (ЭТАП 1: КАТЕГОРИЯ OTTO)
              ========================================================= */}
          <div className="drawer-stage-card">
            <div className="drawer-stage-header">
              <h3 className="drawer-stage-title">ЭТАП 1: КАТЕГОРИЯ OTTO</h3>
              <div className="drawer-stage-actions">
                <button
                  type="button"
                  onClick={() => onSyncProduct?.(product.id)}
                  className="drawer-stage-action-btn btn-sync-stage"
                  title="Подтянуть категорию из каталога OTTO"
                >
                  <CloudDownload size={12} />
                  <span>Синхр. с ОТТО</span>
                </button>
                <button
                  type="button"
                  onClick={handleAutoAiCategory}
                  className="drawer-stage-action-btn btn-ai-stage"
                  title="Подобрать подходящую категорию с помощью ИИ"
                >
                  <Wand2 size={12} />
                  <span>Авто (ИИ)</span>
                </button>
              </div>
            </div>

            {/* Category Autocomplete / Searchable Input */}
            <div className="drawer-category-combobox-wrap">
              <div className="category-search-input-box">
                <Search size={14} className="category-search-icon" />
                <input
                  ref={categoryInputRef}
                  type="text"
                  value={categorySearchQuery}
                  onChange={(e) => {
                    setCategorySearchQuery(e.target.value);
                    if (!isCategoryDropdownOpen) setIsCategoryDropdownOpen(true);
                  }}
                  onFocus={() => setIsCategoryDropdownOpen(true)}
                  placeholder="Поиск категории OTTO (например, Esstische, Stühle)..."
                  className="category-combobox-input"
                />
                {category && (
                  <span className="category-selected-check" title="Категория выбрана">
                    <Check size={12} />
                  </span>
                )}
              </div>

              {/* Suggestions Dropdown */}
              {isCategoryDropdownOpen && (
                <div className="category-combobox-panel">
                  <div className="category-dropdown-scroll">
                    {filteredCategories.length > 0 ? (
                      filteredCategories.map((cat) => (
                        <button
                          key={cat}
                          type="button"
                          className={`category-item-row ${category === cat ? "selected" : ""}`}
                          onClick={() => handleSelectCategory(cat)}
                        >
                          <span>{cat}</span>
                          {category === cat && <Check size={12} className="cat-check" />}
                        </button>
                      ))
                    ) : (
                      <div className="category-empty-msg">
                        Категория не найдена. Нажмите Enter, чтобы использовать «{categorySearchQuery}»
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Quick suggestion pills */}
              <div className="drawer-quick-categories">
                <span className="quick-cat-label">Быстрый выбор:</span>
                {POPULAR_OTTO_CATEGORIES.slice(0, 5).map((quick) => (
                  <button
                    key={quick}
                    type="button"
                    className={`quick-cat-chip ${category === quick ? "chip-active" : ""}`}
                    onClick={() => handleSelectCategory(quick)}
                  >
                    {quick}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* =========================================================
              5. STAGE 2: ATTRIBUTES (ЭТАП 2: ХАРАКТЕРИСТИКИ)
              ========================================================= */}
          <div className="drawer-stage-card">
            <div className="drawer-stage-header">
              <h3 className="drawer-stage-title">ЭТАП 2: ХАРАКТЕРИСТИКИ</h3>
              <div className="drawer-stage-actions">
                <button
                  type="button"
                  onClick={handleTriggerAiAttributes}
                  className="drawer-stage-action-btn btn-ai-stage"
                  title="Заполнить обязательные свойства через AI Vision"
                >
                  <Wand2 size={12} />
                  <span>AI Vision</span>
                </button>
                <span className="drawer-loaded-badge">
                  Загружено ({Object.keys(attributes).length})
                </span>
              </div>
            </div>

            {/* Required Attributes Group */}
            <div className="drawer-attrs-section">
              <div className="attrs-group-header">
                <span className="attrs-group-title">Обязательные атрибуты</span>
                <span className="attrs-required-star-legend">★ обязательно для OTTO</span>
              </div>

              <div className="drawer-attrs-form-grid">
                {requiredSchema.map((attr) => {
                  const val = attributes[attr.name] || "";
                  const isAi = aiFilledAttrs.has(attr.name);
                  const isFilled = !!val.trim();

                  return (
                    <div key={attr.name} className="drawer-attr-field-item">
                      <div className="attr-field-label-row">
                        <label className="attr-field-label">
                          <span>{attr.name}</span>
                          {attr.unit && <span className="attr-unit-tag">{attr.unit}</span>}
                          <span className="attr-star">★</span>
                          {isAi && <span className="attr-ai-badge">✦ AI</span>}
                        </label>
                        {isFilled && <span className="attr-filled-check">✓ Заполнено</span>}
                      </div>

                      {attr.options ? (
                        <div className="attr-select-wrap">
                          <select
                            value={val}
                            onChange={(e) => handleAttrChange(attr.name, e.target.value)}
                            className={`attr-field-input attr-select ${isAi ? "input-ai-filled" : ""}`}
                          >
                            <option value="">{attr.placeholder || "Выберите значение..."}</option>
                            {attr.options.map((opt) => (
                              <option key={opt} value={opt}>
                                {opt}
                              </option>
                            ))}
                          </select>
                        </div>
                      ) : (
                        <input
                          type="text"
                          value={val}
                          onChange={(e) => handleAttrChange(attr.name, e.target.value)}
                          placeholder={attr.placeholder || "—"}
                          className={`attr-field-input ${isAi ? "input-ai-filled" : ""}`}
                        />
                      )}
                    </div>
                  );
                })}
              </div>

              {/* Filled Optional Attributes */}
              {filledOptional.length > 0 && (
                <div className="drawer-optional-filled-block">
                  <div className="optional-separator">
                    <span className="sep-line" />
                    <span className="sep-text">
                      Заполненные дополнительные свойства ({filledOptional.length})
                    </span>
                    <span className="sep-line" />
                  </div>

                  <div className="drawer-attrs-form-grid">
                    {filledOptional.map((attr) => {
                      const val = attributes[attr.name] || "";
                      const isAi = aiFilledAttrs.has(attr.name);

                      return (
                        <div key={attr.name} className="drawer-attr-field-item">
                          <div className="attr-field-label-row">
                            <label className="attr-field-label attr-label-optional">
                              <span>{attr.name}</span>
                              {attr.unit && <span className="attr-unit-tag">{attr.unit}</span>}
                              {isAi && <span className="attr-ai-badge">✦ AI</span>}
                            </label>
                            <span className="attr-filled-check">✓ Заполнено</span>
                          </div>
                          <input
                            type="text"
                            value={val}
                            onChange={(e) => handleAttrChange(attr.name, e.target.value)}
                            className={`attr-field-input ${isAi ? "input-ai-filled" : ""}`}
                          />
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Collapsed Empty Optional Attributes Toggle (OttoFlow style) */}
              {emptyOptional.length > 0 && (
                <div className="drawer-empty-optional-accordion">
                  <button
                    type="button"
                    onClick={() => setShowAllOptional((prev) => !prev)}
                    className="accordion-toggle-btn"
                  >
                    <span>
                      {showAllOptional
                        ? "Скрыть пустые необязательные атрибуты"
                        : `Показать остальные пустые атрибуты (${emptyOptional.length})`}
                    </span>
                    {showAllOptional ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                  </button>

                  {showAllOptional && (
                    <div className="drawer-attrs-form-grid accordion-expanded-content">
                      {emptyOptional.map((attr) => (
                        <div key={attr.name} className="drawer-attr-field-item">
                          <div className="attr-field-label-row">
                            <label className="attr-field-label attr-label-optional">
                              <span>{attr.name}</span>
                              {attr.unit && <span className="attr-unit-tag">{attr.unit}</span>}
                            </label>
                          </div>
                          <input
                            type="text"
                            value={attributes[attr.name] || ""}
                            onChange={(e) => handleAttrChange(attr.name, e.target.value)}
                            placeholder={attr.placeholder || "Оставьте пустым"}
                            className="attr-field-input attr-field-dimmed"
                          />
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* =========================================================
              6. RESTART PRICING ACCORDION
              ========================================================= */}
          <div className="drawer-stage-card">
            <details className="drawer-details-group">
              <summary className="drawer-details-summary">
                <div className="summary-title-flex">
                  <Tag size={13} className="summary-icon" />
                  <span>Схема расчёта цены OTTO Restart 3/12M</span>
                </div>
                <ChevronDown size={14} className="details-chevron" />
              </summary>
              <div className="details-expanded-inner">
                <div className="pricing-mini-grid">
                  <div className="pricing-card card-old">
                    <span className="pricing-card-sub">standardPrice до снятия</span>
                    <strong className="pricing-card-val">
                      {formatPriceEur(product.oldStandardPriceCents)}
                    </strong>
                  </div>
                  <div className="pricing-card card-discount">
                    <span className="pricing-card-sub">Фиксированная скидка ({product.account})</span>
                    <strong className="pricing-card-val color-amber">
                      -{discountAmount}
                    </strong>
                  </div>
                  <div className="pricing-card card-restart">
                    <span className="pricing-card-sub">Новая цена Restart</span>
                    <strong className="pricing-card-val color-green">
                      {formatPriceEur(restartPriceCents)}
                    </strong>
                  </div>
                </div>
              </div>
            </details>
          </div>

          {/* =========================================================
              7. SOURCE DATA ACCORDION (OttoFlow style)
              ========================================================= */}
          <div className="drawer-stage-card">
            <details className="drawer-details-group">
              <summary className="drawer-details-summary">
                <div className="summary-title-flex">
                  <SlidersHorizontal size={13} className="summary-icon" />
                  <span>Исходные данные товара (Afterbuy / OTTO)</span>
                </div>
                <ChevronDown size={14} className="details-chevron" />
              </summary>
              <div className="details-expanded-inner">
                <pre className="source-data-code-block">
                  {JSON.stringify(
                    {
                      id: product.id,
                      ean: product.ean,
                      sku: product.sku,
                      account: product.account,
                      factoryId: product.factoryId,
                      factoryName: product.factoryName,
                      sourceType: product.sourceType,
                      oldStandardPriceCents: product.oldStandardPriceCents,
                      restartPriceCents,
                      period: product.period,
                      errorCode: product.errorCode,
                      category,
                      attributes,
                    },
                    null,
                    2
                  )}
                </pre>
              </div>
            </details>
          </div>
        </div>

        {/* =========================================================
            8. STICKY FOOTER (OttoFlow style)
            ========================================================= */}
        <div className="drawer-footer-actions-row">
          <div className="footer-left-buttons">
            <button
              type="button"
              onClick={() => onSyncProduct?.(product.id)}
              className="drawer-footer-btn-secondary"
              title="Синхронизировать характеристики с OTTO"
            >
              <CloudDownload size={13} />
              <span>Синхронизировать с ОТТО</span>
            </button>
            <button
              type="button"
              onClick={handleTriggerAiAttributes}
              className="drawer-footer-btn-secondary"
              title="Сгенерировать недостающие характеристики с помощью ИИ"
            >
              <Sparkles size={13} />
              <span>ИИ-атрибуты</span>
            </button>
          </div>

          <div className="footer-right-buttons">
            <button
              type="button"
              onClick={handleClose}
              className="drawer-footer-btn-cancel"
            >
              Отмена
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="drawer-footer-btn-save"
            >
              {saving ? (
                <Loader2 size={14} className="spin" />
              ) : (
                <Save size={14} />
              )}
              <span>Сохранить</span>
            </button>
          </div>
        </div>

        {/* Image Zoom Portal (reusing existing ImageHoverPreview) */}
        <ImageHoverPreview preview={imgPreview} />
      </aside>
    </>
  );
}
