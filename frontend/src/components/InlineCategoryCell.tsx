"use client";

import React, { useState, useRef, useEffect } from "react";
import { fetchWithAuth } from "@/lib/api";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { Search, Check, Loader2, X, Pencil } from "lucide-react";

interface Category {
  id: string;
  name: string;
  group?: string;
  group_id?: string;
}

interface InlineCategoryCellProps {
  ean: string;
  factoryId: string;
  account: string;
  categoryId?: string;
  categoryName?: string;
  status?: string;
  onCategorySaved: (ean: string, categoryId: string, categoryName: string) => void;
  onEditingChange?: (isEditing: boolean) => void;
}

export const InlineCategoryCell = React.memo(function InlineCategoryCell({
  ean,
  factoryId,
  account,
  categoryId,
  categoryName,
  status,
  onCategorySaved,
  onEditingChange
}: InlineCategoryCellProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [inputValue, setInputValue] = useState(categoryName || "");
  const [debouncedQuery, setDebouncedQuery] = useState(categoryName || "");
  const [suggestions, setSuggestions] = useState<Category[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);

  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // Notify parent row about editing state to elevate z-index
  useEffect(() => {
    onEditingChange?.(isEditing);
  }, [isEditing, onEditingChange]);

  // Sync inputValue with categoryName prop when idle
  useEffect(() => {
    if (!isEditing) {
      setInputValue(categoryName || "");
      setDebouncedQuery(categoryName || "");
    }
  }, [categoryName, isEditing]);

  // Debounce inputValue -> debouncedQuery without blocking user input
  useEffect(() => {
    if (!isEditing) return;
    const timer = setTimeout(() => {
      setDebouncedQuery(inputValue.trim());
    }, 180);
    return () => clearTimeout(timer);
  }, [inputValue, isEditing]);

  // Fetch suggestions with AbortController
  useEffect(() => {
    if (!isEditing) return;

    const controller = new AbortController();
    setLoading(true);
    const url = `/mapping/categories?q=${encodeURIComponent(debouncedQuery)}&limit=40`;

    fetchWithAuth(url, { signal: controller.signal })
      .then((res) => (res.ok ? res.json() : []))
      .then((data: Category[]) => {
        setSuggestions(data || []);
        setSelectedIndex(0);
      })
      .catch((err) => {
        if (err.name !== "AbortError") {
          setSuggestions([]);
        }
      })
      .finally(() => {
        setLoading(false);
      });

    return () => {
      controller.abort();
    };
  }, [isEditing, debouncedQuery]);

  // Auto-focus and select text when entering editing mode
  useEffect(() => {
    if (isEditing) {
      const raf = requestAnimationFrame(() => {
        if (inputRef.current) {
          inputRef.current.focus();
          inputRef.current.select();
        }
      });
      return () => cancelAnimationFrame(raf);
    }
  }, [isEditing]);

  // Click outside listener
  useEffect(() => {
    if (!isEditing) return;

    const handleOutsideClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsEditing(false);
        setInputValue(categoryName || "");
      }
    };

    document.addEventListener("mousedown", handleOutsideClick);
    return () => document.removeEventListener("mousedown", handleOutsideClick);
  }, [isEditing, categoryName]);

  // Auto-scroll selected item into view
  useEffect(() => {
    if (!listRef.current) return;
    const items = listRef.current.querySelectorAll("[data-cat-item]");
    if (items[selectedIndex]) {
      items[selectedIndex].scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [selectedIndex]);

  // Save category (either existing from Otto catalog or custom string)
  const saveCategory = async (catId: string, catName: string) => {
    if (saving) return;
    const cleanId = catId.trim();
    const cleanName = catName.trim() || cleanId;

    if (!cleanId) {
      toast.error("Название категории не может быть пустым");
      return;
    }

    setSaving(true);
    try {
      const res = await fetchWithAuth("/mapping/category", {
        method: "POST",
        body: JSON.stringify({
          product_ean: ean.trim(),
          factory_id: factoryId,
          category_id: cleanId,
          account: account || "JV"
        })
      });

      if (res.ok) {
        toast.success(`Категория «${cleanName}» сохранена`);
        onCategorySaved(ean.trim(), cleanId, cleanName);
        setInputValue(cleanName);
        setIsEditing(false);
      } else {
        const err = await res.json().catch(() => ({}));
        toast.error(err.detail || "Ошибка при сохранении категории");
      }
    } catch (e) {
      console.error(e);
      toast.error("Ошибка сети при сохранении категории");
    } finally {
      setSaving(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      e.preventDefault();
      setIsEditing(false);
      setInputValue(categoryName || "");
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((prev) => Math.min(prev + 1, Math.max(0, suggestions.length - 1)));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((prev) => Math.max(prev - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (suggestions.length > 0 && suggestions[selectedIndex]) {
        saveCategory(suggestions[selectedIndex].id, suggestions[selectedIndex].name);
      } else if (inputValue.trim()) {
        saveCategory(inputValue.trim(), inputValue.trim());
      }
    }
  };

  const hasExactMatch = suggestions.some(
    (s) => s.name.toLowerCase() === inputValue.trim().toLowerCase()
  );

  return (
    <div
      ref={containerRef}
      data-no-drag
      onClick={(e) => e.stopPropagation()}
      className="relative w-full"
    >
      {!isEditing ? (
        /* ── IDLE STATE: Simple compact category tag ── */
        <div
          onClick={() => setIsEditing(true)}
          className={cn(
            "group/cell flex items-center gap-1.5 px-2 py-1 rounded-md cursor-pointer transition-all text-xs border select-none",
            categoryId
              ? "bg-slate-50 dark:bg-slate-800/80 border-slate-200 dark:border-slate-700/80 text-slate-800 dark:text-slate-200 hover:border-indigo-400"
              : "border-dashed border-slate-300 dark:border-slate-700 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 hover:border-slate-400",
            status === "Error" && "border-rose-300 text-rose-600 dark:text-rose-400"
          )}
          title="Нажмите, чтобы изменить категорию"
        >
          <span className="truncate font-medium">
            {categoryName || categoryId || "Без категории"}
          </span>
          <Pencil className="w-3 h-3 shrink-0 ml-auto text-slate-400 opacity-0 group-hover/cell:opacity-80 transition-opacity" />
        </div>
      ) : (
        /* ── EDITING STATE: Compact input + simple dropdown ── */
        <div className="relative z-50">
          {/* Simple compact input box */}
          <div
            className={cn(
              "flex items-center gap-1 rounded-lg px-2 py-1 transition-all",
              "bg-white dark:bg-slate-900",
              "border border-indigo-500 ring-2 ring-indigo-500/20",
              "shadow-lg"
            )}
          >
            <Search className="w-3.5 h-3.5 shrink-0 text-slate-400" />
            <input
              ref={inputRef}
              type="text"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Категория..."
              disabled={saving}
              className="w-full h-6 px-1 text-xs bg-transparent border-none outline-none text-slate-900 dark:text-white placeholder-slate-400"
            />

            {saving ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin text-indigo-500 shrink-0" />
            ) : inputValue.trim() ? (
              <button
                type="button"
                onClick={() => saveCategory(inputValue.trim(), inputValue.trim())}
                className="p-1 rounded bg-indigo-600 hover:bg-indigo-500 text-white text-[10px] font-semibold shrink-0 transition-colors"
                title="Сохранить (Enter)"
              >
                <Check className="w-3 h-3" />
              </button>
            ) : null}

            <button
              type="button"
              onClick={() => {
                setIsEditing(false);
                setInputValue(categoryName || "");
              }}
              className="p-0.5 rounded text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 transition-colors shrink-0"
              title="Отмена (Esc)"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>

          {/* ── SIMPLE COMPACT DROPDOWN ── */}
          <div
            className={cn(
              "absolute top-full left-0 mt-1 z-50 rounded-lg overflow-hidden",
              "bg-white dark:bg-slate-900",
              "border border-slate-200 dark:border-slate-700",
              "shadow-xl",
              "w-full min-w-[260px] max-w-[320px]"
            )}
          >
            {/* If user typed a custom category not in list, quick single-line action */}
            {inputValue.trim() && !hasExactMatch && (
              <div
                onClick={() => saveCategory(inputValue.trim(), inputValue.trim())}
                className="px-3 py-1.5 text-xs font-medium text-indigo-600 dark:text-indigo-400 bg-indigo-50/70 dark:bg-indigo-950/40 hover:bg-indigo-100 dark:hover:bg-indigo-900/60 cursor-pointer border-b border-slate-100 dark:border-slate-800 flex items-center justify-between transition-colors"
              >
                <span className="truncate">+ Использовать «{inputValue.trim()}»</span>
                <span className="text-[10px] text-slate-400 font-mono ml-2 shrink-0">Enter ↵</span>
              </div>
            )}

            {/* List */}
            <div
              ref={listRef}
              className="max-h-[220px] overflow-y-auto overscroll-contain divide-y divide-slate-100 dark:divide-slate-800/60"
              style={{ scrollbarWidth: "thin" }}
            >
              {loading && suggestions.length === 0 ? (
                <div className="p-4 flex items-center justify-center gap-2 text-xs text-slate-400">
                  <Loader2 className="w-3.5 h-3.5 animate-spin text-indigo-500" />
                  <span>Поиск...</span>
                </div>
              ) : suggestions.length > 0 ? (
                suggestions.map((cat, idx) => {
                  const isCurrent = categoryId === cat.id;
                  const isHighlighted = idx === selectedIndex;
                  return (
                    <div
                      key={cat.id}
                      data-cat-item
                      onClick={() => saveCategory(cat.id, cat.name)}
                      onMouseEnter={() => setSelectedIndex(idx)}
                      className={cn(
                        "px-3 py-1.5 cursor-pointer text-xs transition-colors flex items-center justify-between",
                        isHighlighted
                          ? "bg-slate-100 dark:bg-slate-800 text-slate-900 dark:text-white"
                          : "text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800/50",
                        isCurrent && "font-semibold text-indigo-600 dark:text-indigo-400"
                      )}
                    >
                      <span className="truncate">{cat.name}</span>
                      {isCurrent && <Check className="w-3.5 h-3.5 text-indigo-500 shrink-0 ml-1" />}
                    </div>
                  );
                })
              ) : (
                <div
                  onClick={() => inputValue.trim() && saveCategory(inputValue.trim(), inputValue.trim())}
                  className={cn(
                    "p-3 text-xs text-center transition-colors",
                    inputValue.trim()
                      ? "cursor-pointer text-indigo-600 dark:text-indigo-400 hover:bg-indigo-50 dark:hover:bg-indigo-950/40 font-medium"
                      : "text-slate-400"
                  )}
                >
                  {inputValue.trim()
                    ? `Нажмите Enter, чтобы сохранить «${inputValue.trim()}»`
                    : "Категории не найдены"}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
});
