"use client";

import React, { useState, useRef, useEffect } from "react";
import { useTranslation } from "@/features/i18n";

interface AttributeComboboxProps {
  name: string;
  value: string;
  options?: string[] | null;
  placeholder?: string;
  onChange: (val: string) => void;
  required?: boolean;
  dimmed?: boolean;
  isAiFilled?: boolean;
  disabled?: boolean;
  className?: string;
}

export const AttributeCombobox = React.memo(function AttributeCombobox({
  name,
  value,
  options,
  placeholder,
  onChange,
  required,
  dimmed,
  isAiFilled,
  disabled = false,
  className,
}: AttributeComboboxProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value || "");
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setDraft(value || "");
  }, [value]);

  useEffect(() => {
    if (!disabled) return;
    setOpen(false);
    setDraft(value || "");
  }, [disabled, value]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (!containerRef.current?.contains(e.target as Node)) {
        setOpen(false);
        if (draft !== value) onChange(draft);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open, draft, value, onChange]);

  const filtered = !disabled && open && options
    ? (draft.trim()
        ? options.filter((o) => o.toLowerCase().includes(draft.toLowerCase()))
        : options
      ).slice(0, 80)
    : [];

  const handleInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (disabled) return;
    const v = e.target.value;
    setDraft(v);
    if (isAiFilled && v !== value) {
      onChange(v);
    }
    if (options && options.length > 0) setOpen(true);
  };

  const handleBlur = () => {
    if (!disabled && draft !== value) onChange(draft);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      setOpen(false);
      if (draft !== value) onChange(draft);
    } else if (e.key === "Escape") {
      setOpen(false);
      setDraft(value || "");
    }
  };

  const selectOption = (opt: string) => {
    setDraft(opt);
    onChange(opt);
    setOpen(false);
  };

  const clearValue = () => {
    setDraft("");
    onChange("");
    setOpen(false);
  };

  const isFieldAiFilled = Boolean(isAiFilled && draft === value);
  const isEmptyRequired = Boolean(required && !draft.trim() && !dimmed);

  return (
    <div ref={containerRef} className="attr-combobox-wrap">
      <div className="attr-combobox-input-wrap">
        <input
          type="text"
          value={draft}
          onChange={handleInput}
          disabled={disabled}
          onBlur={handleBlur}
          onKeyDown={handleKeyDown}
          onFocus={() => {
            if (!disabled && options && options.length > 0) setOpen(true);
          }}
          placeholder={placeholder || t("drawer.enterAttrValue", { name: name.toLowerCase() })}
          className={`attr-combobox-input ${
            isFieldAiFilled
              ? "input-ai-filled"
              : isEmptyRequired
              ? "input-empty-required"
              : value
              ? "input-has-value"
              : dimmed
              ? "input-dimmed"
              : ""
          } ${className || ""}`}
        />
        {!disabled ? <div className="attr-combobox-controls">
          {draft && (
            <button
              type="button"
              onMouseDown={(e) => {
                e.preventDefault();
                clearValue();
              }}
              className="attr-combobox-clear-btn"
              title={t("common.clear")}
              aria-label={t("common.clear")}
            >
              ✕
            </button>
          )}
          {options && options.length > 0 && (
            <button
              type="button"
              onMouseDown={(e) => {
                e.preventDefault();
                setOpen((o) => !o);
              }}
              className="attr-combobox-arrow-btn"
              title={t("drawer.optionsList")}
              aria-label={t("drawer.optionsList")}
            >
              ▼
            </button>
          )}
        </div> : null}
      </div>

      {!disabled && open && options && options.length > 0 && (
        <div className="attr-combobox-dropdown-menu">
          {filtered.length > 0 ? (
            filtered.map((opt) => (
              <button
                key={opt}
                type="button"
                onMouseDown={(e) => {
                  e.preventDefault();
                  selectOption(opt);
                }}
                className={`attr-combobox-option-item ${value === opt ? "selected" : ""}`}
              >
                <span>{opt}</span>
                {value === opt && <span className="option-check">✓</span>}
              </button>
            ))
          ) : (
            <div className="attr-combobox-no-options">
              {draft
                ? t("drawer.noMatchesFor", { query: draft })
                : t("drawer.noAvailableOptions")}
            </div>
          )}
        </div>
      )}
    </div>
  );
});
