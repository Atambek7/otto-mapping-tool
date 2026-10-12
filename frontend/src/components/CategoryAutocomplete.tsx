"use client";

import { useState, useMemo, useRef, useEffect } from "react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { fetchWithAuth } from "@/lib/api";
import { useTranslation } from "react-i18next";

interface Category {
  id: string;
  name: string;
  group: string;
  group_id: string;
}

interface Props {
  value: string;
  initialName?: string;
  onChange: (id: string, name: string) => void;
}

export function CategoryAutocomplete({ value, initialName, onChange }: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState(initialName || "");
  const [suggestions, setSuggestions] = useState<Category[]>([]);
  const [loading, setLoading] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (initialName) {
      setQuery(initialName);
      return;
    }
    if (value && !query) {
      // try to fetch the category name
      fetchWithAuth(`/mapping/categories/${value}`)
        .then(res => {
          if (res.ok) return res.json();
          throw new Error("Not found");
        })
        .then(data => {
          setQuery(data.name || value);
        })
        .catch(() => {
          setQuery(value);
        });
    } else if (!value) {
      setQuery("");
    }
  }, [value, initialName]);

  useEffect(() => {
    const delayDebounceFn = setTimeout(() => {
      if (open) {
        setLoading(true);
        fetchWithAuth(`/mapping/categories?q=${encodeURIComponent(query)}&limit=60`)
          .then(res => res.json())
          .then(data => {
            setSuggestions(data || []);
          })
          .catch(console.error)
          .finally(() => setLoading(false));
      }
    }, 250);

    return () => clearTimeout(delayDebounceFn);
  }, [query, open]);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  return (
    <div ref={ref} className="relative">
      <div className="relative flex items-center">
        <Input
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            if (!open) setOpen(true);
          }}
          onFocus={() => {
            setOpen(true);
          }}
          placeholder={t("product_drawer.category_search_placeholder")}
          className={cn(
            "h-7 text-xs rounded-md px-2 shadow-sm border",
            value
              ? "border-indigo-300 bg-indigo-50 text-indigo-900 dark:border-indigo-800/50 dark:bg-indigo-900/30 dark:text-indigo-200"
              : "border-slate-200 bg-white dark:border-slate-800 dark:bg-[#0B0F19] dark:text-slate-200"
          )}
        />
      </div>
      {open && (
        <div className="absolute top-full left-0 right-0 z-50 mt-1 bg-white border border-slate-200 dark:bg-[#0B0F19] dark:border-slate-800 rounded-xl shadow-xl overflow-hidden">
          {loading && suggestions.length === 0 ? (
            <div className="px-3 py-2 text-xs text-slate-400">{t("common.loading")}</div>
          ) : suggestions.length > 0 ? (
            <div className="max-h-64 overflow-y-auto">
              {suggestions.map((cat) => {
                return (
                  <button
                    key={cat.id}
                    type="button"
                    onMouseDown={(e) => {
                      e.preventDefault();
                      onChange(cat.id, cat.name);
                      setQuery(cat.name);
                      setOpen(false);
                    }}
                    className={cn(
                      "w-full text-left px-3 py-1.5 text-xs hover:bg-indigo-50 dark:hover:bg-indigo-900/30 flex flex-col transition-colors",
                      value === cat.id && "bg-indigo-50 text-indigo-700 font-medium dark:bg-indigo-900/40 dark:text-indigo-300"
                    )}
                  >
                    <span className="font-medium text-slate-800 dark:text-slate-200 truncate">{cat.name}</span>
                    <span className="text-[10px] text-slate-400 dark:text-slate-500 shrink-0">{cat.group}</span>
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="px-3 py-2 text-xs text-slate-400">{t("product_drawer.nothing_found")}</div>
          )}
        </div>
      )}
    </div>
  );
}
