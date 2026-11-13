"use client";

import { useState, useRef, useEffect } from "react";
import { Search, Check, Loader2 } from "lucide-react";
import { useTranslation } from "@/features/i18n";

export interface CategoryItem {
  id: string;
  name: string;
  group?: string;
  group_id?: string;
}

interface CategoryAutocompleteProps {
  value: string;
  initialName?: string;
  account?: string;
  disabled?: boolean;
  onChange: (id: string, name: string) => void;
}

export function CategoryAutocomplete({
  value,
  initialName,
  account,
  disabled = false,
  onChange,
}: CategoryAutocompleteProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState(initialName || value || "");
  const [suggestions, setSuggestions] = useState<CategoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (initialName) {
      setQuery(initialName);
    } else if (value) {
      setQuery(value);
    } else {
      setQuery("");
    }
  }, [value, initialName]);

  useEffect(() => {
    if (!open) return;
    const delayDebounceFn = setTimeout(() => {
      setLoading(true);
      const accParam = account ? `&account=${encodeURIComponent(account)}` : "";
      fetch(`/restart/api/backend/categories?q=${encodeURIComponent(query.trim())}${accParam}`, {
        headers: { Accept: "application/json" },
      })
        .then((res) => (res.ok ? res.json() : []))
        .then((data: CategoryItem[]) => {
          setSuggestions(Array.isArray(data) ? data : []);
        })
        .catch(() => setSuggestions([]))
        .finally(() => setLoading(false));
    }, 200);

    return () => clearTimeout(delayDebounceFn);
  }, [query, open, account]);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  return (
    <div ref={ref} className="category-autocomplete-container">
      <div className={`category-autocomplete-input-box ${value ? "has-selected" : ""}`}>
        <Search size={14} className="category-autocomplete-search-icon" />
        <input
          type="text"
          disabled={disabled}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            if (!open) setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          placeholder={t("drawer.categorySearchInputPlaceholder")}
          className="category-autocomplete-input"
        />
        {value && (
          <span className="category-autocomplete-check-badge" title={t("drawer.categorySelected")}>
            <Check size={12} />
          </span>
        )}
      </div>

      {open && (
        <div className="category-autocomplete-dropdown-panel">
          {loading && suggestions.length === 0 ? (
            <div className="category-autocomplete-loading">
              <Loader2 size={13} className="spin" />
              <span>{t("drawer.searchingCategories")}</span>
            </div>
          ) : suggestions.length > 0 ? (
            <div className="category-autocomplete-list">
              {suggestions.map((cat) => {
                const isSelected = value === cat.id || value === cat.name;
                return (
                  <button
                    key={`${cat.id}-${cat.name}`}
                    type="button"
                    onMouseDown={(e) => {
                      e.preventDefault();
                      onChange(cat.id, cat.name);
                      setQuery(cat.name);
                      setOpen(false);
                    }}
                    className={`category-autocomplete-item ${isSelected ? "selected" : ""}`}
                  >
                    <div className="cat-item-content">
                      <span className="cat-item-name">{cat.name}</span>
                      {cat.group && <span className="cat-item-group">{cat.group}</span>}
                    </div>
                    {isSelected && <Check size={13} className="cat-item-check" />}
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="category-autocomplete-empty">
              {query
                ? t("drawer.categoryNotFound", { query })
                : t("drawer.startTypingCategory")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
