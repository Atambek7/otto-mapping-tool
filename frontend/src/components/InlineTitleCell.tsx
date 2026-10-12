"use client";

import React, { useState, useRef, useEffect } from "react";
import { fetchWithAuth } from "@/lib/api";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { Pencil, Loader2, AlertTriangle } from "lucide-react";

interface InlineTitleCellProps {
  title: string;
  ean: string;
  factoryId: string;
  account: string;
  onTitleSaved: (ean: string, newTitle: string) => void;
  className?: string;
}

export const InlineTitleCell = React.memo(function InlineTitleCell({
  title,
  ean,
  factoryId,
  account,
  onTitleSaved,
  className
}: InlineTitleCellProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [inputValue, setInputValue] = useState(title || "");
  const [optimisticTitle, setOptimisticTitle] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const effectiveTitle = optimisticTitle !== null ? optimisticTitle : (title || "");
  const currentLen = effectiveTitle.length;
  const editLen = inputValue.length;
  const isOverLimit = currentLen > 70;
  const isEditOverLimit = editLen > 70;

  const isSavingRef = useRef(false);
  const previousTitleRef = useRef(title || "");

  // Synchronize when prop changes and not currently editing
  useEffect(() => {
    if (!isEditing) {
      setInputValue(title || "");
      previousTitleRef.current = title || "";
      if (optimisticTitle !== null && title === optimisticTitle) {
        setOptimisticTitle(null);
      }
    }
  }, [title, isEditing, optimisticTitle]);

  // Focus and select text upon entering edit mode
  useEffect(() => {
    if (isEditing) {
      previousTitleRef.current = effectiveTitle;
      const raf = requestAnimationFrame(() => {
        if (textareaRef.current) {
          textareaRef.current.focus();
          textareaRef.current.select();
        }
      });
      return () => cancelAnimationFrame(raf);
    }
  }, [isEditing, effectiveTitle]);

  const handleSave = async () => {
    if (isSavingRef.current) return;
    const cleanTitle = inputValue.trim();

    if (!cleanTitle) {
      toast.error("Название товара не может быть пустым");
      setInputValue(previousTitleRef.current);
      setIsEditing(false);
      return;
    }

    if (cleanTitle === (previousTitleRef.current || "").trim()) {
      setIsEditing(false);
      return;
    }

    const previousTitle = previousTitleRef.current;
    isSavingRef.current = true;
    setSaving(true);

    // ── Instant optimistic update (<1ms): close editing & show new title immediately ──
    setOptimisticTitle(cleanTitle);
    setInputValue(cleanTitle);
    previousTitleRef.current = cleanTitle;
    setIsEditing(false);
    onTitleSaved(ean.trim(), cleanTitle);

    // ── Background persistence to database ──
    try {
      const res = await fetchWithAuth("/mapping/product-details", {
        method: "POST",
        body: JSON.stringify({
          product_ean: ean.trim(),
          factory_id: factoryId,
          account: account || "JV",
          product_name: cleanTitle
        })
      });

      if (res.ok) {
        const data = await res.json();
        const savedTitle = data.product_name || cleanTitle;
        if (savedTitle !== cleanTitle) {
          setOptimisticTitle(savedTitle);
          setInputValue(savedTitle);
          previousTitleRef.current = savedTitle;
          onTitleSaved(ean.trim(), savedTitle);
        }
        if (savedTitle.length > 70) {
          toast.warning(`Название обновлено, но превышает лимит OTTO (${savedTitle.length}/70 символов)`);
        } else {
          toast.success("Название товара обновлено");
        }
      } else {
        const err = await res.json().catch(() => ({}));
        // Revert optimistic update on server error
        setOptimisticTitle(null);
        setInputValue(previousTitle);
        previousTitleRef.current = previousTitle;
        onTitleSaved(ean.trim(), previousTitle);
        toast.error(err.detail || "Ошибка при сохранении названия");
      }
    } catch (e) {
      console.error("Failed to save title:", e);
      // Revert optimistic update on network error
      setOptimisticTitle(null);
      setInputValue(previousTitle);
      previousTitleRef.current = previousTitle;
      onTitleSaved(ean.trim(), previousTitle);
      toast.error("Ошибка соединения при сохранении названия");
    } finally {
      isSavingRef.current = false;
      setSaving(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSave();
    } else if (e.key === "Escape") {
      e.preventDefault();
      setIsEditing(false);
      setInputValue(effectiveTitle);
    }
  };

  const handleBlur = () => {
    if (isEditing && !isSavingRef.current) {
      handleSave();
    }
  };

  if (isEditing) {
    return (
      <div
        ref={containerRef}
        data-no-drag
        className="w-full relative flex flex-col gap-1 py-1 z-30"
        onClick={(e) => e.stopPropagation()}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="relative w-full">
          <textarea
            ref={textareaRef}
            data-no-drag
            rows={2}
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            onBlur={handleBlur}
            className={cn(
              "w-full text-xs leading-snug font-medium p-1.5 pr-7 rounded-md border bg-white dark:bg-slate-900 text-slate-900 dark:text-white outline-none resize-none shadow-xs transition-all",
              isEditOverLimit
                ? "border-rose-500 ring-2 ring-rose-500/20"
                : "border-indigo-500 ring-2 ring-indigo-500/20"
            )}
          />
        </div>
        <div className="flex items-center justify-between text-[11px] px-0.5 select-none">
          <span className="text-slate-400 dark:text-slate-500 text-[10px]">
            Enter — сохр., Esc — отмена
          </span>
          <span
            className={cn(
              "font-mono px-1.5 py-0.2 rounded text-[11px] flex items-center gap-1",
              isEditOverLimit
                ? "text-rose-600 dark:text-rose-400 font-bold bg-rose-50 dark:bg-rose-950/60 border border-rose-200 dark:border-rose-900"
                : "text-slate-500 dark:text-slate-400"
            )}
          >
            {isEditOverLimit && <AlertTriangle className="w-3 h-3 text-rose-500 shrink-0" />}
            ({editLen}/70)
          </span>
        </div>
      </div>
    );
  }

  return (
    <div
      data-no-drag
      onClick={(e) => {
        e.stopPropagation();
        setIsEditing(true);
      }}
      onMouseDown={(e) => e.stopPropagation()}
      title={
        isOverLimit
          ? `${effectiveTitle}\n\n⚠️ ВНИМАНИЕ: длина названия ${currentLen} из 70 символов! Превышен лимит OTTO (макс. 70).\n(Нажмите для редактирования)`
          : effectiveTitle
          ? `${effectiveTitle}\n(Длина: ${currentLen}/70, нажмите для редактирования)`
          : "Нажмите для добавления названия"
      }
      className={cn(
        "group/title relative flex items-center justify-between gap-1.5 w-full py-1 px-1.5 -mx-1.5 rounded-md cursor-text hover:bg-slate-100/80 dark:hover:bg-slate-800/60 transition-colors",
        isOverLimit && "bg-rose-50/40 dark:bg-rose-950/20 border-l-2 border-rose-500 pl-2",
        className
      )}
    >
      <p className="font-medium text-slate-900 dark:text-white text-sm line-clamp-2 leading-snug flex-1">
        {effectiveTitle || <span className="text-slate-400 dark:text-slate-500 italic font-normal text-xs">Без названия...</span>}
      </p>
      <div className="flex items-center gap-1 shrink-0 ml-1.5 select-none">
        {saving && (
          <span title="Сохранение в фоне...">
            <Loader2 className="w-3 h-3 animate-spin text-indigo-500 shrink-0" />
          </span>
        )}
        <span
          className={cn(
            "text-[11px] font-mono px-1.5 py-0.5 rounded transition-colors whitespace-nowrap",
            isOverLimit
              ? "bg-rose-100 dark:bg-rose-950/80 text-rose-700 dark:text-rose-300 font-bold border border-rose-300 dark:border-rose-800 flex items-center gap-0.5 shadow-2xs"
              : "text-slate-400 dark:text-slate-500 bg-slate-100/70 dark:bg-slate-800/50"
          )}
        >
          {isOverLimit && <AlertTriangle className="w-3 h-3 text-rose-600 dark:text-rose-400 shrink-0" />}
          ({currentLen}/70)
        </span>
        <Pencil className="w-3 h-3 text-slate-400 opacity-0 group-hover/title:opacity-100 transition-opacity shrink-0 ml-0.5" />
      </div>
    </div>
  );
});
