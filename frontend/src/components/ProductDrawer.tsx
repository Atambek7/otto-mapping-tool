"use client";

import React, { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { X, Check, Save, Wand2, AlertTriangle, Pencil, Loader2, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CategoryAutocomplete } from "@/components/CategoryAutocomplete";
import { AttributeCombobox } from "@/components/AttributeCombobox";
import { fetchWithAuth } from "@/lib/api";
import { toast } from "sonner";
import { ImageHoverPreview, HoverImagePreviewState } from "@/components/ImageHoverPreview";
import { useTranslation } from "react-i18next";
import { useAccount } from "@/lib/useAccount";
import { showOpenAiErrorToast } from "@/lib/openaiError";
import { cn } from "@/lib/utils";

import { parsePositivePrice, getAftercoolPrice } from "@/lib/prices";

const schemaCache = new Map<string, any[]>();

interface ProductDrawerProps {
  product: any;
  factoryId: string;
  account?: string;
  initialCategory?: string;
  initialCategoryName?: string;
  initialProductName?: string;
  initialPrice?: string;
  onClose: () => void;
  onSave: (
    ean: string,
    category: string,
    categoryName: string,
    attrs: Record<string, string>,
    status: string,
    productName?: string,
    price?: string
  ) => void;
  onTitleSaved?: (ean: string, newTitle: string) => void;
}

function ProductDrawerComponent({
  product,
  factoryId,
  account: propAccount,
  initialCategory,
  initialCategoryName,
  initialProductName,
  initialPrice,
  onClose,
  onSave,
  onTitleSaved
}: ProductDrawerProps) {
  const { t } = useTranslation();
  const { account: contextAccount } = useAccount();
  const account = propAccount || product?.account || contextAccount || "JV";
  const [category, setCategory] = useState(initialCategory || "");
  const [categoryName, setCategoryName] = useState(initialCategoryName || "");
  const [attributes, setAttributes] = useState<Record<string, string>>({});
  const attributesRef = useRef<Record<string, string>>({});
  const isUserCategoryChangeRef = useRef(false);
  const [isClosing, setIsClosing] = useState(false);
  const [isMounted, setIsMounted] = useState(false);
  const [isAnimationDone, setIsAnimationDone] = useState(false);
  const closeTimerRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    // Trigger slide-in animation shortly after mount
    const timer = setTimeout(() => setIsMounted(true), 10);
    const animTimer = setTimeout(() => setIsAnimationDone(true), 220);
    return () => {
      clearTimeout(timer);
      clearTimeout(animTimer);
      if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    };
  }, []);

  const handleClose = useCallback(() => {
    if (isClosing) return;
    setIsClosing(true);
    closeTimerRef.current = setTimeout(() => {
      onClose();
    }, 200); // wait for CSS slide out
  }, [isClosing, onClose]);

  // Handle ESC key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        handleClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [handleClose]);

  // Track which fields were filled by AI (yellow highlight)
  const [aiFilledAttrs, setAiFilledAttrs] = useState<Set<string>>(new Set());
  const [missingAttributes, setMissingAttributes] = useState<string[]>([]);
  const [lastError, setLastError] = useState<string>("");
  const [schema, setSchema] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [isPredictingCategory, setIsPredictingCategory] = useState(false);
  const [isExtractingAttributes, setIsExtractingAttributes] = useState(false);
  const [isSyncingOkb, setIsSyncingOkb] = useState(false);
  const [imgPreview, setImgPreview] = useState<HoverImagePreviewState | null>(null);
  const [dbName, setDbName] = useState<string | null>(initialProductName || null);
  const [dbPrice, setDbPrice] = useState<string | null>(initialPrice || null);
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [savingTitle, setSavingTitle] = useState(false);
  const [showAllOptional, setShowAllOptional] = useState(false);

  const row = product?.row || {};
  const ean = product?.ean || row.EAN || product?.product_ean || "";
  const fallbackName = initialProductName || row.Artikelbeschreibung || product?.name || "";
  const displayName = (dbName && dbName.trim() !== "Без названия" && dbName.toLowerCase() !== "null") ? dbName : fallbackName;
  const name = displayName;
  const displayPrice = parsePositivePrice(dbPrice) || parsePositivePrice(initialPrice) || (!product?.price_conflict ? getAftercoolPrice(row) || parsePositivePrice(product?.price) : "") || "";
  const img = row.GalleryURL || row.PictureURL || product?.img || product?.image_url || "";

  useEffect(() => {
    if (initialCategory && !category) {
      setCategory(initialCategory);
    }
    if (initialCategoryName && !categoryName) {
      setCategoryName(initialCategoryName);
    }
  }, [initialCategory, initialCategoryName]);

  // 1. Fetch Draft/Mapping Data on mount
  useEffect(() => {
    if (!ean) return;
    let cancelled = false;

    // Reset state immediately so previous product's data doesn't flash or leak
    isUserCategoryChangeRef.current = false;
    setCategory(initialCategory || "");
    setCategoryName(initialCategoryName || "");
    setAttributes({});
    attributesRef.current = {};
    setAiFilledAttrs(new Set());
    setMissingAttributes([]);
    setLastError("");
    setDbName(initialProductName || null);
    setDbPrice(initialPrice || null);
    setIsEditingTitle(false);

    const fetchExisting = async () => {
      setLoading(true);
      try {
        const res = await fetchWithAuth(`/mapping/product/${ean}?account=${account}`);
        if (cancelled) return;
        if (res.ok) {
          const data = await res.json();
          if (data.category_id) {
            setCategory(data.category_id);
            setCategoryName(data.category_name || data.category_id);
          } else {
            setCategory(initialCategory || "");
            setCategoryName(initialCategoryName || "");
          }
          if (data.product_name && data.product_name.trim() !== "Без названия" && data.product_name.toLowerCase() !== "null") {
            setDbName(data.product_name.trim());
          } else if (initialProductName) {
            setDbName(initialProductName);
          }
          if (data.price) {
            setDbPrice(data.price);
          } else if (initialPrice) {
            setDbPrice(initialPrice);
          }
          const cleanAttrs: Record<string, string> = {};
          if (data.attributes && typeof data.attributes === "object") {
            for (const [k, v] of Object.entries(data.attributes)) {
              if (v && String(v).toLowerCase() !== "null") {
                cleanAttrs[k] = String(v);
              }
            }
          }
          setAttributes(cleanAttrs);
          attributesRef.current = cleanAttrs;

          if (data.ai_attributes && Array.isArray(data.ai_attributes)) {
            setAiFilledAttrs(new Set(data.ai_attributes));
          } else {
            setAiFilledAttrs(new Set());
          }
          
          if (data.missing_attributes && Array.isArray(data.missing_attributes)) {
            setMissingAttributes(data.missing_attributes);
          } else {
            setMissingAttributes([]);
          }
          
          if (data.last_error) {
            setLastError(data.last_error);
          } else {
            setLastError("");
          }
        } else {
          setCategory(initialCategory || "");
          setCategoryName(initialCategoryName || "");
          setAttributes({});
          attributesRef.current = {};
          setAiFilledAttrs(new Set());
          setMissingAttributes([]);
          setLastError("");
          setDbName(null);
          setDbPrice(null);
        }
      } catch (e) {
        console.error("Failed to load existing mapping", e);
        if (!cancelled) {
          setCategory(initialCategory || "");
          setCategoryName(initialCategoryName || "");
          setAttributes({});
          attributesRef.current = {};
          setAiFilledAttrs(new Set());
          setMissingAttributes([]);
          setLastError("");
          setDbName(null);
          setDbPrice(null);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };
    fetchExisting();
    return () => {
      cancelled = true;
    };
  }, [ean, account]);

  // 2. Fetch Category Schema when category changes
  useEffect(() => {
    if (!category) {
      setSchema([]);
      return;
    }
    const loadSchema = async () => {
      let loadedSchema: any[] = [];
      if (schemaCache.has(category)) {
        loadedSchema = schemaCache.get(category)!;
        setSchema(loadedSchema);
      } else {
        setLoading(true);
        try {
          const res = await fetchWithAuth(`/mapping/categories/${encodeURIComponent(category)}/attributes`);
          if (res.ok) {
            const data = await res.json();
            if (Array.isArray(data) && data.length > 0) {
              schemaCache.set(category, data);
            }
            loadedSchema = data;
            setSchema(data);
          }
        } catch (e) {
          console.error("Failed to load schema", e);
        } finally {
          setLoading(false);
        }
      }

      // Smart Attribute Preservation:
      // Reconcile current attributes with new schema ONLY when user changes category manually
      if (isUserCategoryChangeRef.current) {
        isUserCategoryChangeRef.current = false;
        if (Array.isArray(loadedSchema) && loadedSchema.length > 0) {
          const allowedLowerMap = new Map<string, string>();
          for (const s of loadedSchema) {
            if (s.name) {
              allowedLowerMap.set(s.name.trim().toLowerCase(), s.name.trim());
            }
          }
          for (const u of ["marke", "farbe", "material", "lieferzeit", "zolltarifnummer"]) {
            if (!allowedLowerMap.has(u)) {
              allowedLowerMap.set(u, u.charAt(0).toUpperCase() + u.slice(1));
            }
          }

          const current = attributesRef.current || {};
          if (Object.keys(current).length > 0) {
            const surviving: Record<string, string> = {};
            let preservedCount = 0;
            let discardedCount = 0;

            for (const [k, v] of Object.entries(current)) {
              if (!v || !String(v).trim()) continue;
              const kLower = k.trim().toLowerCase();
              if (allowedLowerMap.has(kLower)) {
                const canonicalKey = allowedLowerMap.get(kLower)!;
                surviving[canonicalKey] = String(v).trim();
                preservedCount++;
              } else {
                discardedCount++;
              }
            }

            attributesRef.current = surviving;
            setAttributes({ ...surviving });

            if (preservedCount > 0 && discardedCount > 0) {
              toast.info(`Категория обновлена: сохранено ${preservedCount} общих атрибутов, отсеяно ${discardedCount} несовместимых.`);
            } else if (preservedCount > 0) {
              toast.info(`Категория обновлена: все ${preservedCount} совместимых атрибутов сохранены.`);
            }
          }
        }
      }
    };
    loadSchema();
  }, [category]);

  const handleAttrChange = useCallback((name: string, val: string, isNumeric?: boolean) => {
    let cleanVal = val;
    if (isNumeric || /(breite|höhe|tiefe|länge|maße|abmessung|gewicht|stärke|durchmesser)/i.test(name)) {
      if (cleanVal) {
        cleanVal = cleanVal.replace(/\s*cm\b/gi, "").replace(/\s*mm\b/gi, "").trim();
      }
    }
    attributesRef.current = { ...attributesRef.current, [name]: cleanVal };
    // Use functional update to merge both state updates in one pass
    setAttributes({ ...attributesRef.current });
    // Only clear AI highlight if it was actually set (avoids unnecessary re-render)
    setAiFilledAttrs(prev => {
      if (!prev.has(name)) return prev;
      const next = new Set(prev);
      next.delete(name);
      return next;
    });
  }, []);

  const getAiProductData = () => {
    return {
      ean,
      title: name,
      description: row.Beschreibung || "",
      specifics: row.ItemSpecifics || "",
      gallery_url: row.GalleryURL || img || "",
      picture_urls: row.PictureURLs || row.PictureURL || ""
    };
  };

  // Pre-sort schema alphabetically once and split attributes efficiently
  const sortedSchema = useMemo(() => {
    return [...schema].sort((a: any, b: any) => (a.name || "").localeCompare(b.name || ""));
  }, [schema]);

  const requiredAttrs = useMemo(() => sortedSchema.filter((a: any) => a.required), [sortedSchema]);

  const { filledOptionalAttrs, emptyOptionalAttrs } = useMemo(() => {
    const filled: any[] = [];
    const empty: any[] = [];
    for (const a of sortedSchema) {
      if (a.required) continue;
      const val = attributes[a.name];
      if (val && String(val).trim() && String(val).toLowerCase() !== "null") {
        filled.push(a);
      } else {
        empty.push(a);
      }
    }
    return { filledOptionalAttrs: filled, emptyOptionalAttrs: empty };
  }, [sortedSchema, attributes]);

  const handlePredictCategory = async () => {
    setIsPredictingCategory(true);
    try {
      const res = await fetchWithAuth("/mapping/predict-category", {
        method: "POST",
        body: JSON.stringify(getAiProductData())
      });
      if (res.ok) {
        const data = await res.json();
        if (data.category_id) {
          if (data.category_id !== category) {
            setCategory(data.category_id);
            setCategoryName(data.category_name || data.category_id);
            setAttributes({});
            attributesRef.current = {};
            setAiFilledAttrs(new Set());
            setMissingAttributes([]);
            setLastError("");
          }
        }
      } else {
        const errorCode = res.headers.get("x-error-code");
        const errorBody = await res.json().catch(() => ({}));
        const errorMsg = errorBody.detail;
        if (errorCode === "OPENAI_API_KEY_MISSING") {
          toast.error(t("product_drawer.toast_openai_missing_title"), {
            id: "openai-error-toast",
            description: t("product_drawer.toast_openai_missing_desc"),
            duration: 7000,
            action: {
              label: t("product_drawer.toast_openai_action_settings", { defaultValue: "Настройки" }),
              onClick: () => { window.location.href = "/dashboard/settings"; }
            }
          });
        } else if (errorCode === "OPENAI_API_ERROR") {
          showOpenAiErrorToast(String(errorMsg || ""), t);
        } else {
          toast.error(t("product_drawer.toast_category_fail"), { id: "openai-error-toast" });
        }
      }
    } catch (e) {
      console.error(e);
      toast.error(t("product_drawer.toast_network_error"), { id: "network-error-toast" });
    } finally {
      setIsPredictingCategory(false);
    }
  };

  const handleExtractAttributes = async (useVision: boolean = true) => {
    if (!category) return;
    setIsExtractingAttributes(true);
    try {
      const aiData = getAiProductData();
      const hasImg = Boolean(aiData.gallery_url || aiData.picture_urls);
      const res = await fetchWithAuth("/mapping/extract-attributes", {
        method: "POST",
        body: JSON.stringify({ ...aiData, category_id: category, use_vision: useVision && hasImg })
      });
      if (res.ok) {
        const data = await res.json();
        if (data.attributes) {
          const filled: Record<string, string> = {};
          for (const [k, v] of Object.entries(data.attributes)) {
            if (v !== null && v !== undefined && String(v).trim() !== "") {
              const valStr = String(v).trim();
              if (valStr.toLowerCase() !== "null") {
                filled[k] = valStr;
              }
            }
          }
          setAttributes(prev => {
            const next = { ...prev };
            const actuallyFilled: string[] = [];
            for (const [k, v] of Object.entries(filled)) {
              if (!next[k] || next[k].trim() === "" || next[k].toLowerCase() === "null") {
                next[k] = v;
                actuallyFilled.push(k);
              }
            }
            attributesRef.current = next;
            setAiFilledAttrs(old => new Set([...old, ...actuallyFilled]));
            return next;
          });
        }
      } else {
        const errorCode = res.headers.get("x-error-code");
        const errorBody = await res.json().catch(() => ({}));
        const errorMsg = errorBody.detail;
        if (errorCode === "OPENAI_API_KEY_MISSING") {
          toast.error(t("product_drawer.toast_openai_missing_title"), {
            id: "openai-error-toast",
            description: t("product_drawer.toast_openai_missing_desc"),
            duration: 7000,
            action: {
              label: t("product_drawer.toast_openai_action_settings", { defaultValue: "Настройки" }),
              onClick: () => { window.location.href = "/dashboard/settings"; }
            }
          });
        } else if (errorCode === "OPENAI_API_ERROR") {
          showOpenAiErrorToast(String(errorMsg || ""), t);
        } else {
          toast.error(t("product_drawer.toast_attr_fail"), { id: "openai-error-toast" });
        }
      }
    } catch (e) {
      console.error(e);
      toast.error(t("product_drawer.toast_network_error"), { id: "network-error-toast" });
    } finally {
      setIsExtractingAttributes(false);
    }
  };

  const handleSyncOkb = async () => {
    setIsSyncingOkb(true);
    try {
      const res = await fetchWithAuth("/mapping/sync-okb-bulk", {
        method: "POST",
        body: JSON.stringify({
          product_eans: [ean],
          factory_id: factoryId,
          account: account || "JV",
          products_data: [{ ean, row }]
        })
      });
      if (res.ok) {
        const data = await res.json();
        const r = (data.results || [])[0];
        if (r && r.status === "success") {
          toast.success(t("product_drawer.sync_success") || "Данные успешно синхронизированы");
          // Reload the mapping data for this account
          const fetchRes = await fetchWithAuth(`/mapping/product/${ean}?account=${account}`);
          if (fetchRes.ok) {
            const fetchdata = await fetchRes.json();
            setDbPrice(fetchdata.price || null);
            if (fetchdata.product_name) setDbName(fetchdata.product_name);
            let newCatId = "";
            let newCatName = "";
            if (fetchdata.category_id) {
              newCatId = fetchdata.category_id;
              newCatName = fetchdata.category_name || fetchdata.category_id;
              setCategory(newCatId);
              setCategoryName(newCatName);
            }
            const cleanAttrs: Record<string, string> = {};
            if (fetchdata.attributes && Object.keys(fetchdata.attributes).length > 0) {
              for (const [k, v] of Object.entries(fetchdata.attributes)) {
                if (v && String(v).toLowerCase() !== "null") {
                  cleanAttrs[k] = String(v);
                }
              }
            }
            setAttributes(cleanAttrs);
            attributesRef.current = cleanAttrs;

            if (fetchdata.ai_attributes && Array.isArray(fetchdata.ai_attributes)) {
              setAiFilledAttrs(new Set(fetchdata.ai_attributes));
            } else {
              setAiFilledAttrs(new Set());
            }
            
            if (fetchdata.missing_attributes && Array.isArray(fetchdata.missing_attributes)) {
              setMissingAttributes(fetchdata.missing_attributes);
            } else {
              setMissingAttributes([]);
            }
            
            if (fetchdata.last_error) {
              setLastError(fetchdata.last_error);
            } else {
              setLastError("");
            }
            
            // Update parent table mapping state immediately
            if (newCatId) {
              onSave(ean, newCatId, newCatName, cleanAttrs, fetchdata.status || "Ready", fetchdata.product_name, fetchdata.price);
            }
          }
        } else if (r && (r.status === "not_found" || r.status === "unresolved")) {
          toast.error(t("product_drawer.sync_not_found") || "Товар не найден в базе данных");
        } else {
          toast.error(t("product_drawer.sync_error") || "Ошибка синхронизации");
        }
      } else {
        toast.error(t("product_drawer.sync_error") || "Ошибка синхронизации");
      }
    } catch (e: any) {
      toast.error(t("product_drawer.sync_failed") || "Сбой при синхронизации");
    } finally {
      setIsSyncingOkb(false);
    }
  };

  const [isGeneratingAiTitle, setIsGeneratingAiTitle] = useState(false);

  const handleGenerateSingleAiTitle = async () => {
    if (!ean || isGeneratingAiTitle) return;
    const currentSourceTitle = (titleDraft || dbName || fallbackName || "").trim();
    if (!currentSourceTitle) {
      toast.error("Отсутствует исходное название товара для генерации");
      return;
    }
    setIsGeneratingAiTitle(true);
    try {
      const res = await fetchWithAuth("/mapping/generate-ai-titles", {
        method: "POST",
        body: JSON.stringify({
          factory_id: factoryId,
          account: (account || "JV").toUpperCase(),
          eans: [ean.trim()],
          current_title: currentSourceTitle,
          titles: { [ean.trim()]: currentSourceTitle },
          category_id: category || undefined,
          category_name: categoryName || undefined,
          force: true,
        }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || "Ошибка генерации названия");
      }
      const data = await res.json();
      const updatedMap = data.updated || {};
      const newTitle = updatedMap[ean.trim()];
      if (newTitle) {
        setDbName(newTitle);
        setTitleDraft(newTitle);
        setIsEditingTitle(false);
        if (onTitleSaved) {
          onTitleSaved(ean.trim(), newTitle);
        }
        toast.success(`ИИ создал название (${newTitle.length}/70 симв.)`);
      } else {
        const errorDetail = data.errors?.[ean.trim()];
        toast.error(errorDetail || "ИИ не вернул новое название");
      }
    } catch (err: any) {
      toast.error(err.message || "Не удалось сгенерировать название");
    } finally {
      setIsGeneratingAiTitle(false);
    }
  };

  const handleSaveTitleFromDrawer = async () => {
    const cleanTitle = titleDraft.trim();
    if (!cleanTitle) {
      toast.error("Название товара не может быть пустым");
      return;
    }
    const previousTitle = name || "";
    // Optimistically update UI immediately
    setDbName(cleanTitle);
    setIsEditingTitle(false);
    if (onTitleSaved) {
      onTitleSaved(ean.trim(), cleanTitle);
    }
    setSavingTitle(true);
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
        if (cleanTitle.length > 70) {
          toast.warning(`Название обновлено, но превышает лимит OTTO (${cleanTitle.length}/70 символов)`);
        } else {
          toast.success("Название товара обновлено");
        }
      } else {
        // Revert on error
        setDbName(previousTitle);
        if (onTitleSaved) onTitleSaved(ean.trim(), previousTitle);
        toast.error("Не удалось сохранить название");
      }
    } catch {
      // Revert on network error
      setDbName(previousTitle);
      if (onTitleSaved) onTitleSaved(ean.trim(), previousTitle);
      toast.error("Ошибка при сохранении названия");
    } finally {
      setSavingTitle(false);
    }
  };

  const handleSave = async () => {
    if (!category) {
      toast.warning("Пожалуйста, выберите категорию Otto перед сохранением");
      return;
    }

    setLoading(true);
    try {
      // 1. Assign Category
      const catRes = await fetchWithAuth("/mapping/category", {
        method: "POST",
        body: JSON.stringify({ product_ean: ean, factory_id: factoryId, category_id: category, account }),
      });
      if (!catRes.ok) {
        const err = await catRes.json().catch(() => ({}));
        toast.error(err.detail || "Не удалось сохранить категорию");
        return;
      }

      // 2. Assign Attributes — read back the real status from server
      const currentAttrs = attributesRef.current;
      let realStatus = "Need Attributes";
      if (Object.keys(currentAttrs).length > 0) {
        const attrRes = await fetchWithAuth("/mapping/attributes", {
          method: "POST",
          body: JSON.stringify({
            product_ean: ean,
            attributes: currentAttrs,
            account,
            ai_attributes: Array.from(aiFilledAttrs)
          }),
        });
        if (attrRes.ok) {
          const attrData = await attrRes.json();
          realStatus = (attrData.errors && attrData.errors.length > 0) ? "Error" : "Ready";
          if (realStatus === "Ready") {
            setLastError("");
            setMissingAttributes([]);
          }
        } else {
          const err = await attrRes.json().catch(() => ({}));
          toast.error(err.detail || "Не удалось сохранить атрибуты");
          return;
        }
      }

      toast.success("Маппинг сохранён");
      onSave(
        ean,
        category,
        categoryName,
        currentAttrs,
        realStatus,
        (dbName || initialProductName || undefined),
        (dbPrice || initialPrice || undefined)
      );
      handleClose();
    } catch (e) {
      console.error(e);
      toast.error("Не удалось сохранить маппинг");
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      {/* Integrated backdrop with smooth fade transition */}
      <div
        className={cn(
          "fixed inset-0 z-[110] bg-black/25 dark:bg-black/50 backdrop-blur-xs transition-opacity duration-200",
          isMounted && !isClosing ? "opacity-100" : "opacity-0 pointer-events-none"
        )}
        onClick={handleClose}
      />

      {/* Drawer */}
      <div className={cn(
        "fixed inset-y-0 right-0 w-full sm:w-[480px] bg-white dark:bg-card border-l border-slate-200 dark:border-slate-800 shadow-2xl z-[120] flex flex-col transition-transform duration-200 ease-out",
        isMounted && !isClosing ? "translate-x-0" : "translate-x-full"
      )}>
      
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100 dark:border-slate-800 bg-white dark:bg-card">
        <h2 className="text-sm font-bold text-slate-900 dark:text-white">{t("product_drawer.title")}</h2>
        <button
          onClick={handleClose}
          className="p-1.5 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* Product Info */}
        <div className="p-5 border-b border-slate-100 dark:border-slate-800 bg-slate-50/60 dark:bg-slate-900/30">
          <div className="flex gap-3.5">
            <div
              className="w-16 h-16 bg-white dark:bg-card rounded-lg border border-slate-200 dark:border-slate-800 overflow-hidden shrink-0 cursor-zoom-in hover:ring-2 hover:ring-indigo-500/30 transition-all p-1 flex items-center justify-center shadow-xs"
              onMouseEnter={(e) => {
                if (!img) return;
                const r = e.currentTarget.getBoundingClientRect();
                setImgPreview({
                  url: img,
                  name,
                  price: displayPrice ? `${displayPrice}` : "",
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
            >
              {img ? <img src={img} alt="" className="w-full h-full object-contain" /> : <span className="text-slate-300 text-xs">—</span>}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5 mb-1 flex-wrap">
                <span className="text-[11px] text-slate-500 font-mono bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 rounded border border-slate-200/60 dark:border-slate-700/50 inline-block select-text cursor-text">
                  {ean || t("product_drawer.no_ean")}
                </span>
                <span
                  className={cn(
                    "text-[11px] font-mono px-1.5 py-0.5 rounded border inline-flex items-center gap-1",
                    (name || "").length > 70
                      ? "bg-rose-100 text-rose-700 dark:bg-rose-950/80 dark:text-rose-300 font-bold border-rose-300 dark:border-rose-800"
                      : "bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 border-slate-200/60 dark:border-slate-700/50"
                  )}
                  title={(name || "").length > 70 ? `Превышен лимит 70 символов для OTTO (${(name || "").length}/70)` : `Длина названия: ${(name || "").length}/70`}
                >
                  {(name || "").length > 70 && <AlertTriangle className="w-3 h-3 text-rose-600 dark:text-rose-400 shrink-0" />}
                  ({(name || "").length}/70)
                </span>
              </div>
              {isEditingTitle ? (
                <div className="space-y-1.5 mt-1">
                  <textarea
                    value={titleDraft}
                    onChange={(e) => setTitleDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        handleSaveTitleFromDrawer();
                      } else if (e.key === "Escape") {
                        e.preventDefault();
                        setIsEditingTitle(false);
                      }
                    }}
                    rows={2}
                    className={cn(
                      "w-full text-xs p-1.5 rounded border outline-none resize-none bg-white dark:bg-slate-900 text-slate-900 dark:text-white",
                      titleDraft.length > 70
                        ? "border-rose-400 focus:border-rose-500 focus:ring-1 focus:ring-rose-400/20"
                        : "border-indigo-300 dark:border-indigo-700 focus:border-indigo-500"
                    )}
                    placeholder="Введите название товара"
                    autoFocus
                  />
                  <div className="flex items-center justify-between">
                    <span className={cn(
                      "text-[10px] font-mono",
                      titleDraft.length > 70 ? "text-rose-600 font-bold" : "text-slate-400"
                    )}>
                      {titleDraft.length}/70
                    </span>
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={handleGenerateSingleAiTitle}
                        disabled={isGeneratingAiTitle || savingTitle}
                        className="px-2 py-0.5 text-[11px] rounded bg-amber-500 hover:bg-amber-600 text-white font-medium flex items-center gap-1 disabled:opacity-50"
                        title="Сгенерировать название до 70 символов через ИИ"
                      >
                        {isGeneratingAiTitle ? <Loader2 className="w-3 h-3 animate-spin" /> : <Sparkles className="w-3 h-3" />}
                        ИИ
                      </button>
                      <button
                        type="button"
                        onClick={() => setIsEditingTitle(false)}
                        className="px-2 py-0.5 text-[11px] rounded border border-slate-200 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800 text-slate-600 dark:text-slate-300"
                      >
                        Отмена
                      </button>
                      <button
                        type="button"
                        onClick={handleSaveTitleFromDrawer}
                        disabled={savingTitle || isGeneratingAiTitle}
                        className="px-2 py-0.5 text-[11px] rounded bg-indigo-600 hover:bg-indigo-700 text-white font-medium flex items-center gap-1 disabled:opacity-50"
                      >
                        {savingTitle ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
                        Сохранить
                      </button>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="group/title flex items-start gap-1.5">
                  <p className={cn(
                    "text-xs font-medium leading-snug line-clamp-2 flex-1",
                    (name || "").length > 70 ? "text-rose-900 dark:text-rose-200" : "text-slate-900 dark:text-white"
                  )}>
                    {name}
                  </p>
                  <button
                    type="button"
                    onClick={() => {
                      setTitleDraft(name || "");
                      setIsEditingTitle(true);
                    }}
                    className="opacity-0 group-hover/title:opacity-100 p-1 text-slate-400 hover:text-indigo-600 dark:hover:text-indigo-400 transition-opacity rounded hover:bg-slate-100 dark:hover:bg-slate-800 shrink-0"
                    title="Редактировать название товара"
                  >
                    <Pencil className="w-3.5 h-3.5" />
                  </button>
                </div>
              )}
              <div className="text-xs font-semibold text-slate-700 dark:text-slate-300 mt-1 tabular-nums">{displayPrice ? `${displayPrice} €` : "—"}</div>
            </div>
          </div>
        </div>

        {/* Error / Missing Attributes Banner / Over 70 chars limit */}
        {(lastError || (missingAttributes && missingAttributes.length > 0) || ((name || "").length > 70)) && (
          <div className="px-5 py-3 border-b border-slate-100 dark:border-slate-800 bg-rose-50/50 dark:bg-rose-950/20 space-y-1.5">
            {(name || "").length > 70 && (
              <div className="text-[11px] text-rose-700 dark:text-rose-300 font-medium flex items-center justify-between gap-2 flex-wrap">
                <div className="flex items-center gap-1.5 min-w-0">
                  <AlertTriangle className="w-3.5 h-3.5 text-rose-600 dark:text-rose-400 shrink-0" />
                  <span>
                    <strong>Превышен лимит названия:</strong> {(name || "").length}/70 символов.
                  </span>
                </div>
                <button
                  type="button"
                  onClick={handleGenerateSingleAiTitle}
                  disabled={isGeneratingAiTitle}
                  className="px-2 py-0.5 text-[11px] rounded bg-amber-500 hover:bg-amber-600 text-white font-medium flex items-center gap-1 shrink-0 disabled:opacity-50 transition-colors shadow-xs ml-auto"
                >
                  {isGeneratingAiTitle ? <Loader2 className="w-3 h-3 animate-spin" /> : <Sparkles className="w-3 h-3" />}
                  ИИ-сокращение
                </button>
              </div>
            )}
            {lastError && (
              <div className="text-[11px] text-rose-700 dark:text-rose-300 font-mono break-all leading-tight">
                <strong>Ошибка OTTO:</strong> {lastError}
              </div>
            )}
            {missingAttributes && missingAttributes.length > 0 && (
              <div className="text-[11px] text-amber-700 dark:text-amber-300">
                <strong>Отсутствуют обязательные атрибуты:</strong> {missingAttributes.join(", ")}
              </div>
            )}
          </div>
        )}

        {/* Stage 1: Category */}
        <div className="p-5 space-y-3">
          <div className="flex items-center justify-between mb-1.5">
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400 dark:text-slate-500">{t("product_drawer.stage_1")}</h3>
            <div className="flex gap-1.5">
              <Button 
                variant="outline" 
                size="sm" 
                onClick={handleSyncOkb} 
                disabled={isSyncingOkb}
                className="h-7 text-xs border-slate-200 dark:border-slate-800 bg-white dark:bg-card text-slate-700 dark:text-slate-300 hover:bg-slate-50"
              >
                {isSyncingOkb ? t("product_drawer.syncing") : t("product_drawer.sync_okb")}
              </Button>
              <Button 
                variant="outline" 
                size="sm" 
                onClick={handlePredictCategory} 
                disabled={isPredictingCategory}
                className="h-7 text-xs border-slate-200 dark:border-slate-800 bg-white dark:bg-card text-indigo-600 dark:text-indigo-400 hover:bg-slate-50 gap-1 font-medium"
              >
                <Wand2 className="w-3 h-3" />
                {isPredictingCategory ? t("product_drawer.predicting") : t("product_drawer.auto_ai")}
              </Button>
            </div>
          </div>
          <CategoryAutocomplete 
            value={category} 
            initialName={categoryName}
            onChange={(id, name) => {
              if (id !== category) {
                isUserCategoryChangeRef.current = true;
                setCategory(id);
                setCategoryName(name);
              }
            }} 
          />
        </div>

        {/* Stage 2: Attributes */}
        <div className="p-5 border-t border-slate-100 dark:border-slate-800/50 space-y-4">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400 dark:text-slate-500">{t("product_drawer.stage_2")}</h3>
            <div className="flex gap-2 items-center">
              {category && schema.length > 0 && (
                <>
                  <Button 
                    variant="outline" 
                    size="sm" 
                    onClick={() => handleExtractAttributes(true)} 
                    disabled={isExtractingAttributes}
                    className="h-7 text-xs border-indigo-200 text-indigo-600 hover:bg-indigo-50 dark:border-indigo-900/50 dark:text-indigo-400 dark:hover:bg-indigo-900/20"
                    title={t("product_drawer.ai_vision_hint")}
                  >
                    <Wand2 className="w-3 h-3 mr-1" />
                    {isExtractingAttributes ? t("common.loading") : t("product_drawer.ai_vision")}
                  </Button>
                </>
              )}
              {category && schema.length > 0 && (
                <span className="text-[10px] bg-emerald-100 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-400 px-2 py-0.5 rounded-full font-medium">
                  {t("product_drawer.loaded")}
                </span>
              )}
            </div>
          </div>

          {!category ? (
            <div className="text-sm text-slate-400 dark:text-slate-500 p-4 border border-dashed dark:border-slate-800 rounded-xl text-center">
              {t("product_drawer.select_category_first")}
            </div>
          ) : schema.length === 0 ? (
            <div className="text-sm text-slate-400 dark:text-slate-500 p-4 border border-dashed dark:border-slate-800 rounded-xl text-center">
              {t("product_drawer.loading_schema")}
            </div>
          ) : (
            <div className="space-y-4">
              {/* Required attributes (HIGH relevance) */}
              {requiredAttrs.map((attr: any) => {
                const isAiFilled = aiFilledAttrs.has(attr.name);
                const isNum = attr.type === "number" || !!attr.unit;
                return (
                  <div key={attr.name} className="space-y-1.5">
                    <label className="text-xs font-medium text-slate-700 dark:text-slate-300 flex items-center gap-1">
                      {attr.name}
                      {attr.unit && (
                        <span className="text-[10px] font-mono text-slate-500 dark:text-slate-400 bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 rounded border border-slate-200 dark:border-slate-700">
                          {attr.unit}
                        </span>
                      )}
                      <span className="text-red-500 text-[10px]">★</span>
                      {isAiFilled && (
                        <span className="ml-1 text-[9px] bg-amber-100 dark:bg-amber-900/40 text-amber-600 dark:text-amber-400 px-1.5 py-0.5 rounded-full font-semibold tracking-wide">✦ AI</span>
                      )}
                      {attributes[attr.name] && (
                        <span className="ml-auto text-[10px] text-emerald-500 font-normal">✓ {t("product_drawer.filled")}</span>
                      )}
                    </label>
                    <AttributeCombobox
                      name={attr.name}
                      value={attributes[attr.name] || ""}
                      options={attr.options}
                      onChange={(v) => handleAttrChange(attr.name, v, isNum)}
                      isAiFilled={isAiFilled}
                    />
                  </div>
                );
              })}

              {/* Optional attributes: Render filled ones once slide animation completes */}
              {filledOptionalAttrs.length > 0 && isAnimationDone ? (
                <div className="space-y-4 pt-2">
                  <div className="flex items-center gap-2 text-xs font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider select-none">
                    <span className="flex-1 border-t border-dashed border-slate-200 dark:border-slate-700/50" />
                    <span>{t("product_drawer.optional_attributes", { count: filledOptionalAttrs.length })}</span>
                    <span className="flex-1 border-t border-dashed border-slate-200 dark:border-slate-700/50" />
                  </div>
                  {filledOptionalAttrs.map((attr: any) => {
                    const isAiFilled = aiFilledAttrs.has(attr.name);
                    const isNum = attr.type === "number" || !!attr.unit;
                    return (
                      <div key={attr.name} className="space-y-1.5">
                        <label className="text-xs font-medium text-slate-500 dark:text-slate-400 flex items-center gap-1">
                          {attr.name}
                          {attr.unit && (
                            <span className="text-[10px] font-mono text-slate-500 dark:text-slate-400 bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 rounded border border-slate-200 dark:border-slate-700">
                              {attr.unit}
                            </span>
                          )}
                          {isAiFilled && (
                            <span className="ml-1 text-[9px] bg-amber-100 dark:bg-amber-900/40 text-amber-600 dark:text-amber-400 px-1.5 py-0.5 rounded-full font-semibold tracking-wide">✦ AI</span>
                          )}
                          <span className="ml-auto text-[10px] text-emerald-500 font-normal">✓ {t("product_drawer.filled")}</span>
                        </label>
                        <AttributeCombobox
                          name={attr.name}
                          value={attributes[attr.name] || ""}
                          options={attr.options}
                          placeholder={t("product_drawer.leave_empty")}
                          onChange={(v) => handleAttrChange(attr.name, v, isNum)}
                          dimmed
                          isAiFilled={isAiFilled}
                        />
                      </div>
                    );
                  })}
                </div>
              ) : filledOptionalAttrs.length > 0 ? (
                <div className="py-3 flex items-center justify-center text-xs text-slate-400 gap-1.5">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  <span>{t("product_drawer.loading_schema")}</span>
                </div>
              ) : null}

              {/* Collapsed / Lazy Empty Optional Attributes */}
              {emptyOptionalAttrs.length > 0 && (
                <div className="pt-2">
                  <button
                    type="button"
                    onClick={() => setShowAllOptional(prev => !prev)}
                    className="w-full flex items-center justify-between py-2 px-3 rounded-lg border border-dashed border-slate-200 dark:border-slate-800 bg-slate-50/70 dark:bg-slate-900/40 hover:bg-slate-100 dark:hover:bg-slate-850 text-xs font-semibold text-slate-500 dark:text-slate-400 transition-colors select-none"
                  >
                    <span>
                      {showAllOptional
                        ? t("product_drawer.hide_empty_attributes", "Скрыть пустые необязательные атрибуты")
                        : t("product_drawer.show_empty_attributes", `Показать остальные пустые атрибуты (${emptyOptionalAttrs.length})`)}
                    </span>
                    <span className="text-[10px] text-slate-400 ml-2">
                      {showAllOptional ? "▲" : "▼"}
                    </span>
                  </button>

                  {showAllOptional && (
                    <div className="space-y-4 mt-3 pt-1">
                      {emptyOptionalAttrs.map((attr: any) => {
                        const isAiFilled = aiFilledAttrs.has(attr.name);
                        const isNum = attr.type === "number" || !!attr.unit;
                        return (
                          <div key={attr.name} className="space-y-1.5">
                            <label className="text-xs font-medium text-slate-500 dark:text-slate-400 flex items-center gap-1">
                              {attr.name}
                              {attr.unit && (
                                <span className="text-[10px] font-mono text-slate-500 dark:text-slate-400 bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 rounded border border-slate-200 dark:border-slate-700">
                                  {attr.unit}
                                </span>
                              )}
                              {isAiFilled && (
                                <span className="ml-1 text-[9px] bg-amber-100 dark:bg-amber-900/40 text-amber-600 dark:text-amber-400 px-1.5 py-0.5 rounded-full font-semibold tracking-wide">✦ AI</span>
                              )}
                            </label>
                            <AttributeCombobox
                              name={attr.name}
                              value={attributes[attr.name] || ""}
                              options={attr.options}
                              placeholder={t("product_drawer.leave_empty")}
                              onChange={(v) => handleAttrChange(attr.name, v, isNum)}
                              dimmed
                              isAiFilled={isAiFilled}
                            />
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Source Data Accordion (Simplified) */}
        <div className="p-5 border-t border-slate-100 dark:border-slate-800/50">
          <details className="text-sm group">
            <summary className="cursor-pointer text-slate-500 dark:text-slate-400 font-medium hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors">
              {t("product_drawer.source_data")}
            </summary>
            <pre className="mt-2 text-[10px] p-3 rounded-xl bg-slate-800 text-slate-300 overflow-x-auto">
              {JSON.stringify(row, null, 2)}
            </pre>
          </details>
        </div>
      </div>

      {/* Footer / Save */}
      <div className="p-4 border-t border-slate-100 dark:border-slate-800 bg-white dark:bg-card">
        <Button
          onClick={handleSave}
          disabled={loading || !category || isClosing}
          className="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-semibold shadow-xs h-10"
        >
          <Save className="w-4 h-4 mr-2" />
          {loading ? t("product_drawer.saving") : t("product_drawer.save_mapping")}
        </Button>
      </div>

      <ImageHoverPreview preview={imgPreview} />

      </div>
    </>
  );
}

function areProductDrawerPropsEqual(prev: ProductDrawerProps, next: ProductDrawerProps) {
  const prevEan = prev.product?.ean || prev.product?.row?.EAN || prev.product?.product_ean;
  const nextEan = next.product?.ean || next.product?.row?.EAN || next.product?.product_ean;
  if (prevEan !== nextEan) return false;
  if (prev.factoryId !== next.factoryId) return false;
  if (prev.account !== next.account) return false;
  if (prev.initialCategory !== next.initialCategory) return false;
  if (prev.initialCategoryName !== next.initialCategoryName) return false;
  if (prev.initialProductName !== next.initialProductName) return false;
  if (prev.initialPrice !== next.initialPrice) return false;
  return true;
}

export const ProductDrawer = React.memo(ProductDrawerComponent, areProductDrawerPropsEqual);
