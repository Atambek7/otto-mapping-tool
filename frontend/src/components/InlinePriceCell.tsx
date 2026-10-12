"use client";

import React, { useState, useRef, useEffect } from "react";
import { fetchWithAuth } from "@/lib/api";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { Pencil, Loader2 } from "lucide-react";

interface InlinePriceCellProps {
  price: string | number;
  ean: string;
  factoryId: string;
  account: string;
  onPriceSaved: (ean: string, newPrice: string) => void;
  className?: string;
}

export const InlinePriceCell = React.memo(function InlinePriceCell({
  price,
  ean,
  factoryId,
  account,
  onPriceSaved,
  className
}: InlinePriceCellProps) {
  const [isEditing, setIsEditing] = useState(false);
  const parseCleanPrice = (val: any): string => {
    if (!val || val === "-" || val === "—") return "";
    const s = String(val).replace("€", "").replace(/EUR/gi, "").replace(",", ".").trim();
    const num = parseFloat(s);
    if (isNaN(num) || num <= 0) return "";
    return num % 1 === 0 ? String(Math.floor(num)) : num.toFixed(2);
  };
  const rawPriceStr = parseCleanPrice(price);
  const [inputValue, setInputValue] = useState(rawPriceStr);
  const [saving, setSaving] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const isSavingRef = useRef(false);
  const previousPriceRef = useRef(rawPriceStr);

  // Synchronize when prop changes and not currently editing
  useEffect(() => {
    if (!isEditing) {
      setInputValue(rawPriceStr);
      previousPriceRef.current = rawPriceStr;
    }
  }, [rawPriceStr, isEditing]);

  // Focus and select text upon entering edit mode
  useEffect(() => {
    if (isEditing) {
      previousPriceRef.current = rawPriceStr;
      const raf = requestAnimationFrame(() => {
        if (inputRef.current) {
          inputRef.current.focus();
          inputRef.current.select();
        }
      });
      return () => cancelAnimationFrame(raf);
    }
  }, [isEditing, rawPriceStr]);

  const handleSave = async () => {
    if (isSavingRef.current) return;

    const rawInput = inputValue
      .replace("€", "")
      .replace(/EUR/gi, "")
      .trim()
      .replace(",", ".");

    if (!rawInput) {
      toast.error("Цена не может быть пустой");
      setInputValue(previousPriceRef.current);
      setIsEditing(false);
      return;
    }

    const num = parseFloat(rawInput);
    if (isNaN(num) || num <= 0) {
      toast.error("Укажите корректную цену больше 0 (например, 1299 или 1299.99)");
      setInputValue(previousPriceRef.current);
      setIsEditing(false);
      return;
    }

    // Preserve exact manual price entered by user
    const cleanPrice = num % 1 === 0 ? String(Math.floor(num)) : num.toFixed(2);

    if (cleanPrice === (previousPriceRef.current || "").trim()) {
      setIsEditing(false);
      return;
    }

    const previousPrice = previousPriceRef.current;
    isSavingRef.current = true;
    setSaving(true);

    // Optimistically update UI immediately (<1ms)
    onPriceSaved(ean.trim(), cleanPrice);
    setInputValue(cleanPrice);
    previousPriceRef.current = cleanPrice;
    setIsEditing(false);

    try {
      const res = await fetchWithAuth("/mapping/product-details", {
        method: "POST",
        body: JSON.stringify({
          product_ean: ean.trim(),
          factory_id: factoryId,
          account: account || "JV",
          price: cleanPrice
        })
      });

      if (res.ok) {
        const data = await res.json();
        const savedPrice = data.price || cleanPrice;
        if (savedPrice !== cleanPrice) {
          onPriceSaved(ean.trim(), savedPrice);
          setInputValue(savedPrice);
          previousPriceRef.current = savedPrice;
        }
        toast.success(`Цена обновлена: ${savedPrice} €`);
      } else {
        const err = await res.json().catch(() => ({}));
        // Revert optimistic update on error
        onPriceSaved(ean.trim(), previousPrice);
        setInputValue(previousPrice);
        previousPriceRef.current = previousPrice;
        toast.error(err.detail || "Ошибка при сохранении цены");
      }
    } catch (e) {
      console.error("Failed to save price:", e);
      // Revert optimistic update on error
      onPriceSaved(ean.trim(), previousPrice);
      setInputValue(previousPrice);
      previousPriceRef.current = previousPrice;
      toast.error("Ошибка соединения при сохранении цены");
    } finally {
      isSavingRef.current = false;
      setSaving(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleSave();
    } else if (e.key === "Escape") {
      e.preventDefault();
      setIsEditing(false);
      setInputValue(rawPriceStr);
    }
  };

  const handleBlur = () => {
    if (isEditing && !saving) {
      handleSave();
    }
  };

  if (isEditing) {
    return (
      <div
        ref={containerRef}
        data-no-drag
        className="relative flex items-center w-24 z-30"
        onClick={(e) => e.stopPropagation()}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <input
          ref={inputRef}
          data-no-drag
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={handleBlur}
          disabled={saving}
          placeholder="0.00"
          className="w-full pl-2 pr-5 py-1 text-xs font-semibold tabular-nums rounded-md border border-indigo-500 ring-2 ring-indigo-500/20 bg-white dark:bg-slate-900 text-slate-900 dark:text-white outline-none shadow-xs transition-all"
        />
        <span className="absolute right-1.5 text-[11px] font-semibold text-slate-400 select-none pointer-events-none">
          {saving ? <Loader2 className="w-3 h-3 animate-spin text-indigo-500" /> : "€"}
        </span>
      </div>
    );
  }

  const displayPrice = rawPriceStr ? `${rawPriceStr.replace(".", ",")} €` : "—";

  return (
    <div
      data-no-drag
      onClick={(e) => {
        e.stopPropagation();
        setIsEditing(true);
      }}
      onMouseDown={(e) => e.stopPropagation()}
      title={rawPriceStr ? `${rawPriceStr} € (Нажмите для изменения цены)` : "Нажмите для указания цены"}
      className={cn(
        "group/price relative inline-flex items-center gap-1 py-1 px-1.5 -mx-1.5 rounded-md cursor-text hover:bg-slate-100/80 dark:hover:bg-slate-800/60 transition-colors",
        className
      )}
    >
      <span className="font-semibold text-slate-900 dark:text-white tabular-nums text-sm">
        {displayPrice}
      </span>
      <Pencil className="w-2.5 h-2.5 text-slate-400 opacity-0 group-hover/price:opacity-100 transition-opacity shrink-0" />
    </div>
  );
});
