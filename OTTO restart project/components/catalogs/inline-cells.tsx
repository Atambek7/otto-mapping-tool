"use client";

import React, { useState, useRef, useEffect } from "react";
import { Check, Edit2, Loader2, Search, Tag, X } from "lucide-react";
import type { RestartAccount } from "@/features/api/types";
import { MAX_PREPARED_TITLE_CODEPOINTS, titleCodePointLength } from "@/features/catalogs/title-policy";
import {
  calculateRestartPriceCents,
  formatPriceEur,
} from "@/features/catalogs/service";

// ============================================================================
// 1. INLINE TITLE CELL
// ============================================================================
interface InlineTitleCellProps {
  title: string;
  onSave: (newTitle: string) => void;
  onOpenDrawer?: () => void;
  readOnly?: boolean;
  titleOrigin?: "SOURCE" | "DRAFT" | "PREPARED";
  className?: string;
}

export const InlineTitleCell = React.memo(function InlineTitleCell({
  title,
  onSave,
  onOpenDrawer,
  readOnly = false,
  titleOrigin = "PREPARED",
  className = "",
}: InlineTitleCellProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [value, setValue] = useState(title);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!isEditing) {
      setValue(title);
    }
  }, [title, isEditing]);

  useEffect(() => {
    if (isEditing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [isEditing]);

  const currentLen = titleCodePointLength(value.trim());
  const isExceeded = currentLen > MAX_PREPARED_TITLE_CODEPOINTS;

  function handleSave() {
    const clean = value.trim();
    if (!clean) {
      setValue(title);
      setIsEditing(false);
      return;
    }
    if (clean !== title) {
      onSave(clean);
    }
    setIsEditing(false);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      handleSave();
    } else if (e.key === "Escape") {
      e.preventDefault();
      setValue(title);
      setIsEditing(false);
    }
  }

  if (isEditing) {
    return (
      <div
        className={`inline-edit-wrap inline-title-editing ${className}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="inline-input-group">
          <input
            ref={inputRef}
            type="text"
            className={`inline-text-input ${isExceeded ? "input-exceeded" : ""}`}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            onBlur={handleSave}
            placeholder="Название товара..."
          />
          <span
            className={`inline-char-badge ${isExceeded ? "badge-exceeded" : "badge-valid"}`}
            title={
              isExceeded
                ? `Подготовленное название OttoRestart длиннее внутреннего лимита 70 символов на ${currentLen - MAX_PREPARED_TITLE_CODEPOINTS}.`
                : `Подготовленное название в пределах внутреннего лимита OttoRestart.`
            }
          >
            {currentLen}/70
          </span>
        </div>

        <div className="inline-edit-actions">
          <button
            type="button"
            className="inline-save-btn"
            onMouseDown={(e) => {
              e.preventDefault();
              handleSave();
            }}
            title="Сохранить (Enter)"
            aria-label="Сохранить название"
          >
            <Check size={13} />
          </button>
          <button
            type="button"
            className="inline-cancel-btn"
            onMouseDown={(e) => {
              e.preventDefault();
              setValue(title);
              setIsEditing(false);
            }}
            title="Отмена (Esc)"
            aria-label="Отменить редактирование"
          >
            <X size={13} />
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className={`prod-title-cell ${className}`}>
      <span
        className="prod-title-text"
        title={`${title} (нажмите для открытия карточки)`}
        onClick={onOpenDrawer}
      >
        {title}
      </span>

      <span
        className={`char-len-badge ${titleOrigin === "SOURCE" ? "badge-source" : isExceeded ? "badge-exceeded" : "badge-valid"}`}
        title={
          titleOrigin === "SOURCE"
            ? `Исходное название: ${currentLen} символов. Оно хранится отдельно; при подготовке будет создана отдельная проверенная версия до 70 символов.`
            : isExceeded
              ? `Подготовленное название OttoRestart: ${currentLen}/70 символов. Подготовка/QC блокируется до исправления.`
              : `Подготовленное название OttoRestart: ${currentLen}/70 символов.`
        }
      >
        {titleOrigin === "SOURCE" ? `${currentLen} исходное` : `(${currentLen}/70)`}
      </span>

      {!readOnly ? <button
        type="button"
        className="inline-pencil-btn"
        onClick={(e) => {
          e.stopPropagation();
          setIsEditing(true);
        }}
        title="Редактировать название прямо в строке"
        aria-label="Редактировать название"
      >
        <Edit2 size={12} />
      </button> : null}
    </div>
  );
});

// ============================================================================
// 2. INLINE PRICE CELL
// ============================================================================
interface InlinePriceCellProps {
  oldStandardPriceCents: number;
  account: RestartAccount;
  onSavePriceCents: (newCents: number) => void;
  readOnly?: boolean;
  priceAvailable?: boolean;
  className?: string;
}

export const InlinePriceCell = React.memo(function InlinePriceCell({
  oldStandardPriceCents,
  account,
  onSavePriceCents,
  readOnly = false,
  priceAvailable = true,
  className = "",
}: InlinePriceCellProps) {
  const [isEditing, setIsEditing] = useState(false);
  const currentEur = (oldStandardPriceCents / 100).toFixed(2);
  const [inputValue, setInputValue] = useState(currentEur);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!isEditing) {
      setInputValue((oldStandardPriceCents / 100).toFixed(2));
    }
  }, [oldStandardPriceCents, isEditing]);

  useEffect(() => {
    if (isEditing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [isEditing]);

  function handleSave() {
    const raw = inputValue.replace(",", ".").trim();
    const num = parseFloat(raw);
    if (isNaN(num) || num <= 0) {
      setInputValue(currentEur);
      setIsEditing(false);
      return;
    }
    const cents = Math.round(num * 100);
    if (cents !== oldStandardPriceCents) {
      onSavePriceCents(cents);
    }
    setIsEditing(false);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      handleSave();
    } else if (e.key === "Escape") {
      e.preventDefault();
      setInputValue(currentEur);
      setIsEditing(false);
    }
  }

  if (isEditing) {
    return (
      <div
        className={`inline-edit-wrap inline-price-editing ${className}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="inline-price-input-box">
          <input
            ref={inputRef}
            type="text"
            className="inline-price-input"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            onBlur={handleSave}
            placeholder="0.00"
          />
          <span className="price-currency-tag">€</span>
        </div>

        <button
          type="button"
          className="inline-save-btn"
          onMouseDown={(e) => {
            e.preventDefault();
            handleSave();
          }}
          title="Сохранить цену (Enter)"
        >
          <Check size={13} />
        </button>
      </div>
    );
  }

  if (readOnly) return <div className={`prod-price-cell ${className}`}><span className="prod-price-main">{priceAvailable ? formatPriceEur(oldStandardPriceCents) : "Цена не подтверждена"}</span></div>;

  return (
    <div
      className={`prod-price-cell ${className}`}
      onClick={(e) => {
        e.stopPropagation();
        setIsEditing(true);
      }}
      title="Нажмите, чтобы изменить цену"
    >
      <span className="prod-price-main">
        {formatPriceEur(oldStandardPriceCents)}
        <Edit2 size={11} className="price-hover-pencil" />
      </span>
    </div>
  );
});

// ============================================================================
// 3. INLINE CATEGORY CELL
// ============================================================================
interface InlineCategoryCellProps {
  category: string;
  isSyncedWithOtto?: boolean;
  hasAiCategory?: boolean;
  categoriesList?: string[];
  onSaveCategory: (newCategory: string) => void;
  readOnly?: boolean;
  className?: string;
}

const DEFAULT_CATEGORIES = [
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

export const InlineCategoryCell = React.memo(function InlineCategoryCell({
  category,
  isSyncedWithOtto = false,
  hasAiCategory = false,
  categoriesList = DEFAULT_CATEGORIES,
  onSaveCategory,
  readOnly = false,
  className = "",
}: InlineCategoryCellProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [search, setSearch] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isEditing) {
      inputRef.current?.focus();
    }
  }, [isEditing]);

  // Click outside to close dropdown
  useEffect(() => {
    if (!isEditing) return;
    function handleClickOutside(e: MouseEvent) {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setIsEditing(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [isEditing]);

  const filtered = categoriesList.filter((cat) =>
    cat.toLowerCase().includes(search.toLowerCase())
  );

  if (readOnly) return <div className={`category-cell-stack ${className}`}><span className="category-badge">{category || "Без категории"}</span>{isSyncedWithOtto ? <span className="badge-otto-synced">Синхр. с ОТТО</span> : null}</div>;

  function handleSelect(cat: string) {
    onSaveCategory(cat);
    setIsEditing(false);
    setSearch("");
  }

  return (
    <div
      ref={containerRef}
      className={`category-cell-stack ${className} ${isEditing ? "category-editing-active" : ""}`}
      onClick={(e) => e.stopPropagation()}
    >
      <div
        className="category-badge-wrap"
        onClick={() => setIsEditing((prev) => !prev)}
        title="Нажмите, чтобы изменить категорию"
      >
        <span className="category-badge">
          {category || "Без категории"}
          <Edit2 size={10} className="category-hover-pencil" />
        </span>
      </div>

      {isSyncedWithOtto ? (
        <span
          className="badge-otto-synced"
          title="Категория и характеристики синхронизированы из OTTO"
        >
          Синхр. с ОТТО
        </span>
      ) : hasAiCategory ? (
        <span
          className="badge-ai-assigned"
          title="Категория определена с помощью ИИ"
        >
          <Tag size={10} /> ИИ-категория
        </span>
      ) : null}

      {/* Dropdown Combobox */}
      {isEditing && (
        <div className="category-combobox-dropdown">
          <div className="combobox-search-box">
            <Search size={13} />
            <input
              ref={inputRef}
              type="text"
              placeholder="Поиск категории..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  if (search.trim()) {
                    handleSelect(search.trim());
                  }
                } else if (e.key === "Escape") {
                  setIsEditing(false);
                }
              }}
            />
          </div>

          <div className="combobox-options-list">
            {filtered.length > 0 ? (
              filtered.map((cat) => (
                <button
                  key={cat}
                  type="button"
                  className={`combobox-option ${cat === category ? "selected" : ""}`}
                  onClick={() => handleSelect(cat)}
                >
                  <span>{cat}</span>
                  {cat === category && <Check size={13} />}
                </button>
              ))
            ) : (
              <div className="combobox-empty">Категория не найдена</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
});
