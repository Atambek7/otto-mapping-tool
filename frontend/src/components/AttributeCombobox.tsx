"use client";

import { useState, useRef, useEffect } from "react";
import { useTranslation } from "react-i18next";

interface AttributeComboboxProps {
  name: string;
  value: string;
  options?: string[] | null;
  placeholder?: string;
  onChange: (val: string) => void;
  dimmed?: boolean;
  isAiFilled?: boolean;
  className?: string;
}

export function AttributeCombobox({ name, value, options, placeholder, onChange, dimmed, isAiFilled, className }: AttributeComboboxProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  // Local draft — doesn't call onChange on every keystroke
  const [draft, setDraft] = useState(value || "");
  const containerRef = useRef<HTMLDivElement>(null);

  // Sync if parent resets the value (e.g., AI fills it or clear)
  useEffect(() => {
    setDraft(value || "");
  }, [value]);

  // Close dropdown on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (!containerRef.current?.contains(e.target as Node)) {
        setOpen(false);
        // Commit on outside click if changed
        if (draft !== value) onChange(draft);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open, draft, value, onChange]);

  const filtered = options
    ? (draft.trim()
        ? options.filter(o => o.toLowerCase().includes(draft.toLowerCase()))
        : options
      ).slice(0, 80)
    : [];

  const handleInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value;
    setDraft(v);
    // Only open dropdown — do NOT call onChange yet
    if (options) setOpen(true);
  };

  const handleBlur = () => {
    // Commit to parent only when user leaves the field
    if (draft !== value) onChange(draft);
  };

  const selectOption = (opt: string) => {
    setDraft(opt);
    onChange(opt); // immediate commit on selection
    setOpen(false);
  };

  const clearValue = () => {
    setDraft("");
    onChange("");
    setOpen(false);
  };

  return (
    <div ref={containerRef} className="relative">
      <div className="relative flex items-center">
        <input
          type="text"
          value={draft}
          onChange={handleInput}
          onBlur={handleBlur}
          onFocus={() => { if (options) setOpen(true); }}
          placeholder={placeholder || t("product_drawer.enter_attribute", { name: name.toLowerCase() })}
          className={className || [
            "w-full h-8 text-sm border rounded-md px-2 pr-8 outline-none transition-all",
            isAiFilled
              ? "border-amber-300 dark:border-amber-600 bg-amber-50 dark:bg-amber-900/20 text-slate-900 dark:text-amber-100"
              : (!value && !dimmed)
              ? "border-blue-400 dark:border-blue-500 bg-blue-50/50 dark:bg-blue-950/30 text-slate-900 dark:text-slate-100 placeholder-blue-400/70 dark:placeholder-blue-400/50"
              : [
                  "border-slate-200 dark:border-slate-700",
                  "bg-white dark:bg-[#0B0F19]",
                  value
                    ? "border-indigo-300 dark:border-indigo-700 text-slate-900 dark:text-slate-100"
                    : dimmed
                    ? "text-slate-400 dark:text-slate-500"
                    : "text-slate-900 dark:text-slate-200",
                ].join(" "),
            "focus:border-indigo-400 dark:focus:border-indigo-600 focus:ring-1 focus:ring-indigo-400/20",
          ].flat().join(" ")}
        />
        <div className="absolute right-1.5 flex items-center gap-0.5 pointer-events-none">
          {value && (
            <button
              type="button"
              onMouseDown={(e) => { e.preventDefault(); clearValue(); }}
              className="pointer-events-auto text-slate-900 hover:text-black dark:text-slate-400 dark:hover:text-slate-200 text-xs p-0.5 leading-none rounded"
              title={t("product_drawer.clear")}
            >✕</button>
          )}
          {options && (
            <button
              type="button"
              onMouseDown={(e) => { e.preventDefault(); setOpen(o => !o); }}
              className="pointer-events-auto text-slate-900 hover:text-black dark:text-slate-400 dark:hover:text-slate-300 text-[10px] p-0.5 leading-none"
            >▼</button>
          )}
        </div>
      </div>

      {open && options && (
        <div className="absolute z-[200] left-0 right-0 mt-0.5 max-h-52 overflow-y-auto bg-white dark:bg-[#10172a] border border-slate-200 dark:border-slate-700 rounded-lg shadow-2xl text-sm">
          {filtered.length > 0 ? filtered.map(opt => (
            <button
              key={opt}
              type="button"
              onMouseDown={(e) => { e.preventDefault(); selectOption(opt); }}
              className={[
                "w-full text-left px-3 py-1.5 hover:bg-indigo-50 dark:hover:bg-indigo-900/30 transition-colors",
                value === opt
                  ? "bg-indigo-50 dark:bg-indigo-900/20 text-indigo-700 dark:text-indigo-300 font-semibold"
                  : "text-slate-700 dark:text-slate-300",
              ].join(" ")}
            >
              {opt}
            </button>
          )) : (
            <div className="px-3 py-2 text-slate-400 text-xs italic">
              {draft ? t("product_drawer.no_matches_save_draft") : t("product_drawer.no_options")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
