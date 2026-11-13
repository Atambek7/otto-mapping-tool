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
} from "lucide-react";
import type { CatalogProductItem } from "@/features/catalogs/types";
import {
  calculateRestartPriceCents,
  formatPriceEur,
} from "@/features/catalogs/service";
import { ImageHoverPreview, type HoverImagePreviewState } from "./image-hover-preview";
import { AttributeCombobox } from "./attribute-combobox";
import { CategoryAutocomplete } from "./category-autocomplete";
import { useTranslation } from "@/features/i18n";
import { readProductDraft, removeProductDraftIfUnchanged, type ProductDraft } from "@/features/catalogs/product-drafts";
import { RestartApiClient, RestartApiError } from "@/features/api/client";
import { toCatalogProductItem } from "@/features/catalogs/use-catalog-products";
import { MAX_PREPARED_TITLE_CODEPOINTS, safelyCompactSourceTitle, titleCodePointLength } from "@/features/catalogs/title-policy";

interface ProductDrawerProps {
  product: CatalogProductItem | null;
  onClose: () => void;
  demoMode?: boolean;
  onSaveProduct?: (updated: CatalogProductItem) => void;
  onProductSaved?: (updated: CatalogProductItem) => void;
  onSyncProduct?: (id: string) => Promise<void> | void;
  onAiAttributes?: (id: string) => void;
}

// Schema for OTTO category attributes
interface AttrDefinition {
  id?: string;
  name: string;
  label?: string;
  type?: string;
  unit?: string | null;
  required: boolean;
  options?: string[] | null;
  placeholder?: string;
  multiValue?: boolean;
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
    type: "number",
    required: true,
    placeholder: "z.B. 120",
  },
  {
    name: "Höhe",
    label: "Höhe (Высота)",
    unit: "cm",
    type: "number",
    required: true,
    placeholder: "z.B. 75",
  },
  {
    name: "Tiefe",
    label: "Tiefe (Глубина)",
    unit: "cm",
    type: "number",
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
  demoMode = false,
  onSaveProduct,
  onProductSaved,
  onSyncProduct,
  onAiAttributes,
}: ProductDrawerProps) {
  const { t } = useTranslation();
  const [isMounted, setIsMounted] = useState(false);
  const [isClosing, setIsClosing] = useState(false);
  const [title, setTitle] = useState("");
  const [titleDraft, setTitleDraft] = useState("");
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [isGeneratingAiTitle, setIsGeneratingAiTitle] = useState(false);
  const [aiTitleAvailable, setAiTitleAvailable] = useState(false);
  const [category, setCategory] = useState("");
  const categoryRef = useRef("");
  const activeProductIdRef = useRef<string | null>(null);
  const attributeAiRequestRef = useRef(0);
  const attributeAiAbortRef = useRef<AbortController | null>(null);
  const [attributes, setAttributes] = useState<Record<string, string>>({});
  const attributesRef = useRef<Record<string, string>>({});
  const isUserCategoryChangeRef = useRef(false);
  const [aiFilledAttrs, setAiFilledAttrs] = useState<Set<string>>(new Set());
  const [showAllOptional, setShowAllOptional] = useState(false);
  const [copiedEan, setCopiedEan] = useState(false);
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [latestSource, setLatestSource] = useState<CatalogProductItem | null>(null);
  const [imgPreview, setImgPreview] = useState<HoverImagePreviewState | null>(null);
  const [isExtractingAttributes, setIsExtractingAttributes] = useState(false);
  const [categoryPreserveNotice, setCategoryPreserveNotice] = useState<string | null>(null);
  const [draftError, setDraftError] = useState("");
  const [localDraft, setLocalDraft] = useState<ProductDraft | null>(null);
  const [importedDraft, setImportedDraft] = useState<ProductDraft | null>(null);
  const [serverVersion, setServerVersion] = useState(0);
  const [serverLoaded, setServerLoaded] = useState(false);
  const [serverLoading, setServerLoading] = useState(false);
  const [serverConflict, setServerConflict] = useState(false);
  const [saveNotice, setSaveNotice] = useState("");
  const [reloadEdit, setReloadEdit] = useState(0);
  const api = useMemo(() => new RestartApiClient(), []);

  // Dynamic OTTO Category Schema Loading & Caching (OttoFlow parity)
  const schemaCacheRef = useRef<Map<string, AttrDefinition[]>>(new Map());
  const [schema, setSchema] = useState<AttrDefinition[]>(STANDARD_FURNITURE_SCHEMA);
  const [loadingSchema, setLoadingSchema] = useState(false);
  const [schemaError, setSchemaError] = useState("");

  const closeTimerRef = useRef<NodeJS.Timeout | null>(null);
  const isClosingRef = useRef(false);
  const aiAbortRef = useRef<AbortController | null>(null);

  // Initialize state when product changes
  useEffect(() => {
    if (product) {
      attributeAiAbortRef.current?.abort();
      attributeAiRequestRef.current += 1;
      activeProductIdRef.current = product.id;
      categoryRef.current = product.category ?? "";
      const controller = new AbortController();
      setDraftError("");
      setSaveNotice("");
      setServerConflict(false);
      setImportedDraft(null);
      setServerVersion(0);
      setLatestSource(null);
      setServerLoaded(demoMode);
      setServerLoading(!demoMode);
      let draft: ProductDraft | null = null;
      if (!demoMode) {
        try {
          draft = readProductDraft(window.localStorage, product.account, product.id);
        } catch {
          setDraftError("Не удалось открыть черновик в браузере. Проверьте доступ к хранилищу браузера.");
        }
      }
      setLocalDraft(draft);
      setTitle(product.title);
      setTitleDraft(product.title);
      setIsEditingTitle(false);
      setCategory(product.category ?? "");
      isUserCategoryChangeRef.current = false;
      setCategoryPreserveNotice(null);

      // Normalize attributes
      const initialAttrs: Record<string, string> = {};
      const aiAttrs = new Set<string>();

      const sourceAttributes = product.attributes;
      if (sourceAttributes) {
        for (const [k, v] of Object.entries(sourceAttributes)) {
          if (v !== undefined && v !== null && String(v).trim()) {
            initialAttrs[k] = String(v);
            if (product.hasAiAttributes) {
              aiAttrs.add(k);
            }
          }
        }
      }

      setAttributes(initialAttrs);
      attributesRef.current = initialAttrs;
      setAiFilledAttrs(aiAttrs);
      if (!demoMode) {
        void api.getProductEdit(product.id, product.account, controller.signal).then(({ edit }) => {
          if (controller.signal.aborted) return;
          if (edit) {
            setTitle(edit.title);
            setTitleDraft(edit.title);
            setCategory(edit.category);
            setAttributes(edit.attributes);
            attributesRef.current = edit.attributes;
            setAiFilledAttrs(new Set());
            setServerVersion(edit.version);
          }
          setServerLoaded(true);
        }).catch((error: unknown) => {
          if (!controller.signal.aborted) setDraftError(`Не удалось загрузить сохранённые изменения: ${error instanceof Error ? error.message : String(error)}`);
        }).finally(() => {
          if (!controller.signal.aborted) setServerLoading(false);
        });
      }
      isClosingRef.current = false;
      setIsClosing(false);

      // Slide in animation
      const mountTimer = setTimeout(() => setIsMounted(true), 15);
      return () => { controller.abort(); clearTimeout(mountTimer); };
    } else {
      isClosingRef.current = false;
      setIsClosing(false);
      setIsMounted(false);
    }
  }, [product, demoMode, api, reloadEdit]);

  useEffect(() => {
    if (categoryRef.current === category) return;
    categoryRef.current = category;
    attributeAiRequestRef.current += 1;
    attributeAiAbortRef.current?.abort();
  }, [category]);

  useEffect(() => {
    if (!product || demoMode) { setAiTitleAvailable(false); return; }
    const controller = new AbortController();
    setAiTitleAvailable(false);
    void api.getAiStatus(product.account, controller.signal).then((status) => {
      if (!controller.signal.aborted) setAiTitleAvailable(status.available);
    }).catch(() => { if (!controller.signal.aborted) setAiTitleAvailable(false); });
    return () => controller.abort();
  }, [api, demoMode, product?.account]);

  // Clean up close timer on unmount
  useEffect(() => {
    return () => {
      if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
      aiAbortRef.current?.abort();
      attributeAiAbortRef.current?.abort();
      attributeAiRequestRef.current += 1;
    };
  }, []);

  // Handle ESC key
  const handleClose = useCallback(() => {
    if (isClosingRef.current) return;
    isClosingRef.current = true;
    aiAbortRef.current?.abort();
    attributeAiAbortRef.current?.abort();
    attributeAiRequestRef.current += 1;
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
        } else {
          handleClose();
        }
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [handleClose, isEditingTitle, title]);

  // Dynamic OTTO Category Schema Loading & Caching (with Smart Attribute Preservation like OttoFlow)
  useEffect(() => {
    if (!product || !category) {
      setSchema(STANDARD_FURNITURE_SCHEMA);
      setSchemaError("");
      return;
    }

    const trimmedCat = category.trim();
    let isCancelled = false;

    const applyLoadedSchema = (loadedSchema: AttrDefinition[]) => {
      setSchema(loadedSchema);

      // Smart Attribute Preservation (reconciles existing attributes with new schema)
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
            const surviving: Record<string, string> = { ...current };
            let preservedCount = 0;
            let discardedCount = 0;

            for (const [k, v] of Object.entries(current)) {
              if (!v || !String(v).trim()) continue;
              const kLower = k.trim().toLowerCase();
              if (allowedLowerMap.has(kLower)) {
                const canonicalKey = allowedLowerMap.get(kLower)!;
                if (canonicalKey !== k && !(canonicalKey in surviving)) {
                  surviving[canonicalKey] = String(v).trim();
                  delete surviving[k];
                }
                preservedCount++;
              } else {
                discardedCount++;
              }
            }

            attributesRef.current = surviving;
            setAttributes({ ...surviving });

            if (preservedCount > 0 && discardedCount > 0) {
              setCategoryPreserveNotice(`Категория обновлена: ${preservedCount} атрибутов совпадают со схемой; ещё ${discardedCount} сохранены в черновике и требуют проверки.`);
            } else if (preservedCount > 0) {
              setCategoryPreserveNotice(`Категория обновлена: все ${preservedCount} совместимых атрибутов сохранены.`);
            } else if (discardedCount > 0) {
              setCategoryPreserveNotice(`Категория обновлена: ${discardedCount} прежних атрибутов сохранены в черновике и требуют проверки.`);
            }
          }
        }
      }
    };

    if (schemaCacheRef.current.has(trimmedCat)) {
      setLoadingSchema(false);
      setSchemaError("");
      applyLoadedSchema(schemaCacheRef.current.get(trimmedCat)!);
      return;
    }

    setSchema([]);
    setSchemaError("");
    setLoadingSchema(true);

    const loadCategorySchema = async () => {
      try {
        const url = `/restart/api/backend/categories/${encodeURIComponent(trimmedCat)}/attributes?account=${encodeURIComponent(product.account)}`;
        const res = await fetch(url, {
          headers: { Accept: "application/json" },
        });
        if (res.ok) {
          const data: AttrDefinition[] = await res.json();
          if (!isCancelled && Array.isArray(data) && data.length > 0) {
            schemaCacheRef.current.set(trimmedCat, data);
            applyLoadedSchema(data);
            return;
          }
        }
      } catch (err) {
        console.warn("Failed to load category schema from backend:", err);
      } finally {
        if (!isCancelled) setLoadingSchema(false);
      }

      if (!isCancelled) {
        if (demoMode) {
          applyLoadedSchema(STANDARD_FURNITURE_SCHEMA);
        } else {
          isUserCategoryChangeRef.current = false;
          setSchemaError("Не удалось загрузить характеристики категории. Все текущие значения сохранены в форме; попробуйте открыть карточку снова.");
        }
      }
    };

    loadCategorySchema();

    return () => {
      isCancelled = true;
    };
  }, [category, product?.account, demoMode]);

  // Handle Category Change (Stage 1)
  const handleCategoryChange = (id: string, name: string) => {
    if (name !== category) {
      isUserCategoryChangeRef.current = true;
      categoryRef.current = name;
      attributeAiRequestRef.current += 1;
      attributeAiAbortRef.current?.abort();
      setCategory(name);
    }
  };

  // Stage 1 category proposal. Production uses the explicit operator AI endpoint.
  const handleAutoAiCategory = async () => {
    if (!product || (!demoMode && (!aiTitleAvailable || saving || syncing))) return;
    if (!demoMode) {
      setDraftError("");
      try {
        const suggestion = await api.suggestCategory(product.id, product.account);
        handleCategoryChange(suggestion.categoryId, suggestion.category);
        setSaveNotice(`Предложение категории ИИ: ${suggestion.category}. Проверьте и сохраните вручную.`);
      } catch (error) {
        setDraftError(`ИИ не смог предложить категорию: ${error instanceof Error ? error.message : String(error)}`);
      }
      return;
    }
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
    handleCategoryChange(bestMatch, bestMatch);
  };

  // Handle Attribute Change with numeric dimension cleaning (Stage 2 OttoFlow UX)
  const handleAttrChange = useCallback((name: string, val: string, isNumeric?: boolean) => {
    let cleanVal = val;
    if (isNumeric || /(breite|höhe|tiefe|länge|maße|abmessung|gewicht|stärke|durchmesser)/i.test(name)) {
      if (cleanVal) {
        cleanVal = cleanVal.replace(/\s*cm\b/gi, "").replace(/\s*mm\b/gi, "").trim();
      }
    }
    attributesRef.current = { ...attributesRef.current, [name]: cleanVal };
    setAttributes({ ...attributesRef.current });
    setAiFilledAttrs((prev) => {
      if (!prev.has(name)) return prev;
      const next = new Set(prev);
      next.delete(name);
      return next;
    });
  }, []);

  // Stage 2 AI Vision Attributes Extraction (OttoFlow UX)
  const handleTriggerAiAttributes = async () => {
    if (!product || isExtractingAttributes || loadingSchema || (!demoMode && !aiTitleAvailable)) return;
    setIsExtractingAttributes(true);
    setDraftError("");
    if (!demoMode) {
      const requestedCategory = category.trim();
      const requestedProductId = product.id;
      const requestId = attributeAiRequestRef.current + 1;
      attributeAiRequestRef.current = requestId;
      attributeAiAbortRef.current?.abort();
      const controller = new AbortController();
      attributeAiAbortRef.current = controller;
      try {
        const result = await api.suggestAttributes(
          requestedProductId, product.account, requestedCategory, { ...attributesRef.current }, controller.signal,
        );
        if (controller.signal.aborted || attributeAiRequestRef.current !== requestId
          || activeProductIdRef.current !== requestedProductId || categoryRef.current.trim() !== requestedCategory) return;
        const categorySchema = schemaCacheRef.current.get(requestedCategory) ?? schema;
        const allowedNames = new Set(categorySchema.map((definition) => definition.name));
        const next = { ...attributesRef.current };
        const nextAi = new Set(aiFilledAttrs);
        let appliedCount = 0;
        for (const item of result.attributes) {
          // Recheck the live editor state. A manual entry made while the AI
          // request was pending always wins over the older suggestion.
          if (allowedNames.has(item.name) && !String(next[item.name] ?? "").trim() && item.value.trim()) {
            next[item.name] = item.value;
            nextAi.add(item.name);
            appliedCount += 1;
          }
        }
        attributesRef.current = next;
        setAttributes(next);
        setAiFilledAttrs(nextAi);
        setSaveNotice(appliedCount
          ? `ИИ предложил ${appliedCount} значения только для пустых полей. Проверьте их; карточка не сохранена и не утверждена.`
          : "Подходящих пустых полей не найдено. Заполненные значения не изменены.");
      } catch (error) {
        if (!controller.signal.aborted && attributeAiRequestRef.current === requestId) {
          setDraftError(`Не удалось получить ИИ-характеристики: ${error instanceof Error ? error.message : String(error)}`);
        }
      } finally {
        if (attributeAiAbortRef.current === controller) attributeAiAbortRef.current = null;
        if (attributeAiRequestRef.current === requestId) setIsExtractingAttributes(false);
      }
      return;
    }
    onAiAttributes?.(product.id);

    setTimeout(() => {
      const newAttrs = { ...attributesRef.current };
      const newAiSet = new Set(aiFilledAttrs);

      if (!newAttrs["Marke"]) {
        newAttrs["Marke"] = product.account === "XL" ? "XLMOEBEL" : "JVMOEBEL";
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

      attributesRef.current = newAttrs;
      setAttributes(newAttrs);
      setAiFilledAttrs(newAiSet);
      setIsExtractingAttributes(false);
    }, 400);
  };

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

  // Demo-only deterministic title proposal. Production AI uses the explicit server action.
  const handleAiShortenTitle = () => {
    if (!demoMode) return;
    setIsGeneratingAiTitle(true);
    setTimeout(() => {
      const current = (titleDraft || title || "").trim();
      if (!current) {
        setIsGeneratingAiTitle(false);
        return;
      }

      const optimized = safelyCompactSourceTitle(current);
      if (!optimized) {
        setDraftError("Не удалось безопасно сократить исходное название без потери идентификаторов. Отредактируйте его вручную.");
        setIsGeneratingAiTitle(false);
        return;
      }
      setTitleDraft(optimized);
      setTitle(optimized);
      setIsGeneratingAiTitle(false);
    }, 300);
  };

  const handleRealAiTitle = async () => {
    if (!product || demoMode || !aiTitleAvailable || isGeneratingAiTitle) return;
    const sourceTitle = (isEditingTitle ? titleDraft : title).trim();
    if (!sourceTitle) return;
    setIsGeneratingAiTitle(true);
    setDraftError("");
    const controller = new AbortController();
    aiAbortRef.current = controller;
    try {
      const result = await api.suggestTitle(product.account, sourceTitle, controller.signal);
      if (controller.signal.aborted) return;
      setTitleDraft(result.title);
      setIsEditingTitle(true);
      setSaveNotice("Предложение ИИ не сохранено. Проверьте название и сохраните изменения.");
    } catch (error) {
      if (!controller.signal.aborted) setDraftError(`Не удалось получить предложение ИИ: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      if (aiAbortRef.current === controller) aiAbortRef.current = null;
      setIsGeneratingAiTitle(false);
    }
  };

  // Full Drawer Save
  const handleSave = async () => {
    if (!demoMode) {
      if (!serverLoaded || serverLoading || saving) return;
      const finalTitle = (isEditingTitle ? titleDraft : title).trim();
      if (!finalTitle) {
        setDraftError("Введите название товара перед сохранением.");
        return;
      }
      setSaving(true);
      setDraftError("");
      setSaveNotice("");
      try {
        const idempotencyKey = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
          ? crypto.randomUUID() : `edit-${Date.now()}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
        const { edit } = await api.saveProductEdit(product.id, product.account, {
          title: finalTitle, category, attributes: attributesRef.current, expectedVersion: serverVersion,
        }, idempotencyKey);
        setServerVersion(edit.version);
        setTitle(edit.title);
        setTitleDraft(edit.title);
        setIsEditingTitle(false);
        setServerConflict(false);
        onProductSaved?.({ ...product, title: edit.title, category: edit.category, attributes: edit.attributes });
        let localNotice = "";
        if (importedDraft) {
          try {
            if (removeProductDraftIfUnchanged(window.localStorage, product.account, product.id, importedDraft)) setLocalDraft(null);
            else localNotice = " Локальный черновик изменился и сохранён в браузере.";
          } catch {
            localNotice = " Локальную копию удалить не удалось; она осталась в браузере.";
          }
          setImportedDraft(null);
        }
        setSaveNotice(`Изменения сохранены на сервере.${localNotice}`);
      } catch (error) {
        const conflict = error instanceof RestartApiError && error.code === "VERSION_CONFLICT";
        setServerConflict(conflict);
        const locked = error instanceof RestartApiError && (error.code === "OPERATION_NOT_EDITABLE" || error.code === "OPERATION_QUEUED");
        setDraftError(conflict ? "Карточка уже изменена другим пользователем. Обновите данные с сервера; текущие несохранённые правки будут заменены." : locked ? "Карточка находится в очереди или уже отправлена; сохранение изменений заблокировано." : `Не удалось сохранить изменения: ${error instanceof Error ? error.message : String(error)}`);
      } finally {
        setSaving(false);
      }
      return;
    }
    if (!onSaveProduct) return;
    setSaving(true);
    setTimeout(() => {
      onSaveProduct({
        ...product,
        title,
        category,
        attributes: attributesRef.current,
        hasAiAttributes: aiFilledAttrs.size > 0,
        isSyncedWithOtto: product.isSyncedWithOtto || !!category,
      });
      setSaving(false);
      handleClose();
    }, 280);
  };

  const handleSync = async () => {
    if (!product || !onSyncProduct || syncing) return;
    setSyncing(true);
    setDraftError("");
    try {
      await onSyncProduct(product.id);
      if (!demoMode) {
        const refreshed = await api.getProduct(product.id, product.account);
        const source = toCatalogProductItem({ ...refreshed, savedEdit: null });
        setLatestSource(source);
        const priceReviewMessage = source.priceReviewReason === "AFTERCOOL_START_PRICE_UNVERIFIED"
          ? "Целевая цена не рассчитана: нужен подтверждённый Aftercool Startpreis."
          : source.priceReviewReason === "AFTERCOOL_IDENTITY_MISMATCH"
            ? "Целевая цена не рассчитана: Aftercool не подтвердил ту же карточку и фабрику."
            : source.targetPriceStatus === "PRICE_REVIEW_REQUIRED"
              ? "Целевая цена не рассчитана: не хватает подтверждённых источников."
              : null;
        if (!category || category === source.category) {
          const merged = { ...source.attributes, ...attributesRef.current } as Record<string, string>;
          attributesRef.current = merged;
          setAttributes(merged);
          if (!category && source.category) setCategory(source.category);
          setSaveNotice(priceReviewMessage
            ? `Источник OTTO обновлён. ${priceReviewMessage} Товар остаётся неготовым.`
            : "Источник обновлён. Отсутствующие характеристики добавлены в форму; сохраните нужные изменения.");
        } else {
          setSaveNotice(priceReviewMessage
            ? `Источник OTTO обновлён. ${priceReviewMessage} Категория и характеристики формы не заменены.`
            : "Источник обновлён. Категория в форме отличается от OTTO, поэтому характеристики не заменены.");
        }
      }
    } catch (error) {
      setDraftError(`Синхронизация не завершена: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setSyncing(false);
    }
  };

  // Calculations for Pricing
  const priceProduct = latestSource ?? product;
  const restartPriceCents = priceProduct.restartPriceCents ?? (demoMode
    ? calculateRestartPriceCents(priceProduct.oldStandardPriceCents, product.account)
    : undefined);
  const restartUvpCents = priceProduct.restartUvpCents;
  const targetPriceAvailable = demoMode || priceProduct.priceAvailable === true;
  const sourcePriceAvailable = demoMode || priceProduct.sourcePriceAvailable === true || priceProduct.priceAvailable === true;
  const priceSourceLabel = priceProduct.priceSource === "AFTERCOOL_START_PRICE"
    ? "Aftercool Startpreis"
    : priceProduct.ottoSourcePriceCents !== null && priceProduct.ottoSourcePriceCents !== undefined
      ? "OTTO standardPrice"
      : priceProduct.aftercoolSourcePriceCents !== null && priceProduct.aftercoolSourcePriceCents !== undefined
        ? "Aftercool Startpreis"
        : undefined;
  const discountAmount = product.account === "JV" ? "10 €" : "20 €";

  const titleLength = titleCodePointLength(title);
  const titleDraftLength = titleCodePointLength(titleDraft);
  const isTitleExceeded = titleLength > MAX_PREPARED_TITLE_CODEPOINTS;
  const isDraftExceeded = titleDraftLength > MAX_PREPARED_TITLE_CODEPOINTS;

  // Pre-sort schema alphabetically once and split attributes efficiently (100% OttoFlow parity)
  const sortedSchema = useMemo(() => {
    return [...schema].sort((a, b) => (a.name || "").localeCompare(b.name || "", "de", { sensitivity: "base" }));
  }, [schema]);

  const requiredAttrs = useMemo(() => {
    return sortedSchema.filter((a) => a.required);
  }, [sortedSchema]);

  const { filledOptionalAttrs, emptyOptionalAttrs } = useMemo(() => {
    const schemaNames = new Set(sortedSchema.map((a) => a.name));
    const filled: AttrDefinition[] = [];
    const empty: AttrDefinition[] = [];

    for (const a of sortedSchema) {
      if (a.required) continue;
      const val = attributes[a.name];
      if (val !== undefined && val !== null && String(val).trim()) {
        filled.push(a);
      } else {
        empty.push(a);
      }
    }

    // Also include non-schema filled attributes (legal defaults, EU Data Act, etc.)
    for (const [k, v] of Object.entries(attributes)) {
      if (!schemaNames.has(k) && v !== undefined && v !== null && String(v).trim()) {
        filled.push({
          id: k.toLowerCase().replace(/\s+/g, "_"),
          name: k,
          label: k,
          required: false,
        });
      }
    }

    filled.sort((a, b) => (a.name || "").localeCompare(b.name || "", "de", { sensitivity: "base" }));

    return { filledOptionalAttrs: filled, emptyOptionalAttrs: empty };
  }, [sortedSchema, attributes]);

  const totalFilledCount = useMemo(() => {
    return Object.values(attributes).filter((v) => v !== undefined && v !== null && String(v).trim()).length;
  }, [attributes]);

  return (
    <>
      {/* Integrated backdrop with smooth fade transition */}
      <div
        className={`drawer-backdrop-overlay ${isMounted && !isClosing ? "backdrop-open" : "backdrop-closed"}`}
        onClick={handleClose}
        aria-hidden="true"
      />

      {/* Slide-in Drawer Panel */}
      <aside
        className={`ottoflow-product-drawer ${isMounted && !isClosing ? "drawer-open" : "drawer-closed"}`}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={`${t("drawer.title")} ${title}`}
      >
        {/* =========================================================
            1. HEADER (OttoFlow UX)
            ========================================================= */}
        <div className="drawer-header-row">
          <div className="drawer-header-title-wrap">
            <h2 className="drawer-header-h2">{t("drawer.title")}</h2>
            <span className="drawer-account-chip">
              {t("drawer.account")}: <strong>{product.account}</strong>
            </span>
          </div>

          <button
            type="button"
            onClick={handleClose}
            className="drawer-close-icon-btn"
            title={t("drawer.closePanel")}
            aria-label={t("drawer.closePanel")}
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
                    oldPrice: sourcePriceAvailable ? formatPriceEur(priceProduct.oldStandardPriceCents) : "Цена не подтверждена",
                    restartPrice: targetPriceAvailable && restartPriceCents !== undefined ? formatPriceEur(restartPriceCents) : "Целевая цена не подтверждена",
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
                title={t("drawer.imageHoverTooltip")}
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
                  <span className="drawer-ean-tag" title={t("drawer.copyEan")}>
                    <span>{product.ean}</span>
                    <button
                      type="button"
                      className="drawer-inline-copy-btn"
                      onClick={handleCopyEan}
                      title={t("drawer.copyEan")}
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
                    className={`drawer-char-pill ${product.titleOrigin === "SOURCE" ? "pill-source" : isTitleExceeded ? "pill-exceeded" : "pill-valid"}`}
                    title={
                      product.titleOrigin === "SOURCE"
                        ? `Исходное название: ${titleLength} символов. Оно не изменяется; внутренняя проверка применяется к отдельной целевой версии.`
                        : isTitleExceeded
                        ? t("drawer.titleLengthExceeded", { len: titleLength })
                        : `(${titleLength}/70)`
                    }
                  >
                    {isTitleExceeded && product.titleOrigin !== "SOURCE" && (
                      <AlertTriangle size={11} className="pill-warn-icon" />
                    )}
                    {product.titleOrigin === "SOURCE" ? `${titleLength} исх.` : `(${titleLength}/70)`}
                  </span>

                  {/* Sync status badge */}
                  {(product.isSyncedWithOtto || latestSource) ? (
                    <span className="drawer-sync-badge badge-synced">
                      <Check size={10} /> {t("drawer.syncOtto")}
                    </span>
                  ) : product.hasAiAttributes ? (
                    <span className="drawer-sync-badge badge-ai">
                      <Sparkles size={10} /> {t("drawer.autoAi")}
                    </span>
                  ) : (
                    <span className="drawer-sync-badge badge-pending">
                      {t("drawer.needsFilling")}
                    </span>
                  )}
                </div>

                {/* Editable Title Section */}
                {isEditingTitle ? (
                  <div className="drawer-title-edit-box">
                    <textarea
                      disabled={saving || syncing || isGeneratingAiTitle}
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
                      placeholder={t("drawer.enterTitlePlaceholder")}
                      autoFocus
                    />
                    <div className="drawer-title-edit-actions">
                      <span
                        className={`drawer-edit-char-counter ${
                          isDraftExceeded ? "counter-warn" : ""
                        }`}
                      >
                        {titleDraftLength}/70
                      </span>

                      <div className="drawer-edit-buttons-group">
                        {(demoMode || aiTitleAvailable) ? <button
                          type="button"
                          onClick={demoMode ? handleAiShortenTitle : () => void handleRealAiTitle()}
                          disabled={syncing || isGeneratingAiTitle || (!demoMode && (!serverLoaded || saving))}
                          className="drawer-btn-ai-shorten"
                          title={demoMode ? t("drawer.aiShortenTooltip") : "Отправить название в ИИ для предложения; результат потребуется проверить и сохранить"}
                        >
                          {isGeneratingAiTitle ? (
                            <Loader2 size={11} className="spin" />
                          ) : (
                            <Sparkles size={11} />
                          )}
                          <span>{t("drawer.aiShorten")}</span>
                        </button> : null}
                        <button
                          type="button"
                          onClick={() => {
                            setIsEditingTitle(false);
                            setTitleDraft(title);
                          }}
                          className="drawer-btn-cancel-edit"
                        >
                          {t("drawer.cancel")}
                        </button>
                        <button
                          type="button"
                          onClick={handleSaveTitle}
                          className="drawer-btn-save-edit"
                        >
                          <Check size={12} />
                          <span>{t("drawer.save")}</span>
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
                      disabled={!demoMode && (!serverLoaded || serverLoading || saving || syncing || isGeneratingAiTitle)}
                      onClick={() => {
                        setTitleDraft(title);
                        setIsEditingTitle(true);
                      }}
                      className="drawer-pencil-edit-trigger"
                      title={t("drawer.editTitleTooltip")}
                      aria-label={t("drawer.editTitleTooltip")}
                    >
                      <Pencil size={12} />
                    </button>
                  </div>
                )}

                {/* Price Display */}
                <div className="drawer-price-indicator-row">
                  {demoMode ? <span className="price-label-old">{t("drawer.initialPrice")}: {formatPriceEur(priceProduct.oldStandardPriceCents)}</span> : <span className="price-label-old">
                    OTTO: {priceProduct.ottoSourcePriceCents != null ? formatPriceEur(priceProduct.ottoSourcePriceCents) : "нет данных"}
                    {priceProduct.aftercoolSourcePriceCents != null ? ` · Aftercool: ${formatPriceEur(priceProduct.aftercoolSourcePriceCents)}` : ""}
                  </span>}
                  {targetPriceAvailable && restartPriceCents !== undefined ? <>
                    <span className="price-divider">→</span>
                    <span className="price-label-restart">
                      {t("drawer.restartPrice")}: <strong>{formatPriceEur(restartPriceCents)}</strong>
                    </span>
                    <span className="price-discount-tag">(-{discountAmount}, затем ,99)</span>
                  </> : <span className="price-label-restart"><strong>Целевая цена не подтверждена</strong></span>}
                </div>
              </div>
            </div>
          </div>

          {/* =========================================================
              3. WARNING / OVER-LIMIT BANNER (OttoFlow style)
              ========================================================= */}
          {!demoMode && product.sourceTitle && (
            <div className="source-title-reference">
              <span>Исходное название (источник, без изменений)</span>
              <div>{product.sourceTitle}</div>
            </div>
          )}

          {isTitleExceeded && (
            <div className="drawer-alert-banner banner-warning">
              <div className="banner-alert-content">
                <AlertTriangle size={14} className="banner-icon-warn" />
                <span className="banner-alert-text">
                  <strong>{t("drawer.titleLengthExceeded", { len: titleLength })}</strong>{" "}
                  {product.titleOrigin === "SOURCE"
                    ? "Это исходное название и оно не изменяется. При подготовке OttoRestart создаст отдельную безопасно сокращённую версию; если нельзя сохранить идентификаторы и важные данные, подготовка остановится."
                    : t("drawer.titleExceededNote")}
                </span>
              </div>
              {(demoMode || aiTitleAvailable) ? <button
                type="button"
                onClick={demoMode ? handleAiShortenTitle : () => void handleRealAiTitle()}
                disabled={syncing || isGeneratingAiTitle || (!demoMode && (!serverLoaded || saving))}
                className="banner-action-ai-btn"
                title={demoMode ? t("drawer.aiShortenTooltip") : "Отправить название в ИИ для предложения; результат потребуется проверить и сохранить"}
              >
                {isGeneratingAiTitle ? (
                  <Loader2 size={11} className="spin" />
                ) : (
                  <Sparkles size={11} />
                )}
                <span>{t("drawer.aiShorten")}</span>
              </button> : null}
            </div>
          )}

          {categoryPreserveNotice && (
            <div className="drawer-alert-banner" style={{ background: "rgba(14, 165, 233, 0.08)", borderBottomColor: "rgba(14, 165, 233, 0.2)" }}>
              <div className="banner-alert-content">
                <span className="banner-alert-text" style={{ color: "var(--ink)" }}>
                  {categoryPreserveNotice}
                </span>
              </div>
            </div>
          )}

          {/* =========================================================
              4. STAGE 1: CATEGORY (ЭТАП 1: КАТЕГОРИЯ OTTO)
              ========================================================= */}
          <div className="drawer-stage-card">
            <div className="drawer-stage-header">
              <h3 className="drawer-stage-title">{t("drawer.stage1Title")}</h3>
              <div className="drawer-stage-actions">
                <button
                  type="button"
                  onClick={() => void handleSync()}
                  disabled={!onSyncProduct || syncing || saving || (!demoMode && !serverLoaded)}
                  className="drawer-stage-action-btn btn-sync-stage"
                  title={t("drawer.syncOtto")}
                >
                  <CloudDownload size={12} />
                  <span>{syncing ? "Синхронизация…" : t("drawer.syncOtto")}</span>
                </button>
                {(demoMode || aiTitleAvailable) ? <button
                  type="button"
                  onClick={() => void handleAutoAiCategory()}
                  className="drawer-stage-action-btn btn-ai-stage"
                  title={t("drawer.autoAi")}
                >
                  <Wand2 size={12} />
                  <span>{t("drawer.autoAi")}</span>
                </button> : null}
              </div>
            </div>

            {/* Category Autocomplete Dropdown (OttoFlow UX Parity) */}
            <CategoryAutocomplete
              value={category}
              initialName={category}
              account={product.account}
              disabled={!demoMode && (!serverLoaded || serverLoading || saving || syncing)}
              onChange={handleCategoryChange}
            />
          </div>

          {/* =========================================================
              5. STAGE 2: ATTRIBUTES (ЭТАП 2: ХАРАКТЕРИСТИКИ)
              ========================================================= */}
          <div className="drawer-stage-card">
            <div className="drawer-stage-header">
              <h3 className="drawer-stage-title">{t("drawer.stage2Title")}</h3>
              <div className="drawer-stage-actions">
                {(demoMode || aiTitleAvailable) && category && schema.length > 0 && (
                  <button
                    type="button"
                    onClick={handleTriggerAiAttributes}
                    disabled={isExtractingAttributes || (!demoMode && (!serverLoaded || serverLoading || serverConflict))}
                    className="drawer-stage-action-btn btn-ai-stage"
                    title={t("drawer.aiVision")}
                  >
                    {isExtractingAttributes ? (
                      <Loader2 size={12} className="spin" />
                    ) : (
                      <Wand2 size={12} />
                    )}
                    <span>{t("drawer.aiVision")}</span>
                  </button>
                )}
                {category && schema.length > 0 && (
                  <span className="drawer-loaded-badge">
                    {t("drawer.loaded")} ({totalFilledCount})
                  </span>
                )}
              </div>
            </div>

            {!category ? (
              <div className="category-empty-msg" style={{ textAlign: "center", padding: "16px", border: "1px dashed var(--line)", borderRadius: "10px" }}>
                {t("drawer.selectCategoryFirst")}
              </div>
            ) : loadingSchema && schema.length === 0 ? (
              <div
                className="category-empty-msg"
                style={{
                  textAlign: "center",
                  padding: "16px",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: "8px",
                  border: "1px dashed var(--line)",
                  borderRadius: "10px",
                }}
              >
                <Loader2 size={14} className="spin" />
                <span>{t("drawer.loadingCategorySchema")}</span>
              </div>
            ) : schemaError ? (
              <div className="category-empty-msg" role="alert">{schemaError}</div>
            ) : (
              <div className="drawer-attrs-section">
                {/* 1. Required Attributes Group (★) */}
                {requiredAttrs.length > 0 && (
                  <>
                    <div className="attrs-group-header">
                      <span className="attrs-group-title">{t("drawer.requiredAttributes")}</span>
                      <span className="attrs-required-star-legend">{t("drawer.requiredOttoNote")}</span>
                    </div>

                    <div className="drawer-attrs-form-grid">
                      {requiredAttrs.map((attr) => {
                        const val = attributes[attr.name] || "";
                        const isAi = aiFilledAttrs.has(attr.name);
                        const isFilled = !!val.trim();
                        const isNum = attr.type === "number" || !!attr.unit;

                        return (
                          <div key={attr.name} className="drawer-attr-field-item">
                            <div className="attr-field-label-row">
                              <label className="attr-field-label">
                                <span>{attr.label || attr.name}</span>
                                {attr.unit && <span className="attr-unit-tag">{attr.unit}</span>}
                                <span className="attr-star">★</span>
                                {isAi && <span className="attr-ai-badge">✦ AI</span>}
                              </label>
                              {isFilled && <span className="attr-filled-check">{t("drawer.filledBadge")}</span>}
                            </div>

                            <AttributeCombobox
                              name={attr.name}
                              value={val}
                              options={attr.options}
                              required={true}
                              disabled={!demoMode && (!serverLoaded || serverLoading || saving || syncing)}
                              isAiFilled={isAi}
                              placeholder={attr.placeholder || t("drawer.selectOrType")}
                              onChange={(v) => handleAttrChange(attr.name, v, isNum)}
                            />
                          </div>
                        );
                      })}
                    </div>
                  </>
                )}

                {/* 2. Filled Optional Attributes */}
                {filledOptionalAttrs.length > 0 && (
                  <div className="drawer-optional-filled-block">
                    <div className="optional-separator">
                      <span className="sep-line" />
                      <span className="sep-text">
                        {t("drawer.filledAdditional", { count: filledOptionalAttrs.length })}
                      </span>
                      <span className="sep-line" />
                    </div>

                    <div className="drawer-attrs-form-grid">
                      {filledOptionalAttrs.map((attr) => {
                        const val = attributes[attr.name] || "";
                        const isAi = aiFilledAttrs.has(attr.name);
                        const isNum = attr.type === "number" || !!attr.unit;

                        return (
                          <div key={attr.name} className="drawer-attr-field-item">
                            <div className="attr-field-label-row">
                              <label className="attr-field-label attr-label-optional">
                                <span>{attr.label || attr.name}</span>
                                {attr.unit && <span className="attr-unit-tag">{attr.unit}</span>}
                                {isAi && <span className="attr-ai-badge">✦ AI</span>}
                              </label>
                              <span className="attr-filled-check">{t("drawer.filledBadge")}</span>
                            </div>

                            <AttributeCombobox
                              name={attr.name}
                              value={val}
                              options={attr.options}
                              required={false}
                              disabled={!demoMode && (!serverLoaded || serverLoading || saving || syncing)}
                              dimmed={true}
                              isAiFilled={isAi}
                              placeholder={attr.placeholder || t("drawer.leaveEmpty")}
                              onChange={(v) => handleAttrChange(attr.name, v, isNum)}
                            />
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* 3. Collapsed Empty Optional Attributes Toggle (OttoFlow style) */}
                {emptyOptionalAttrs.length > 0 && (
                  <div className="drawer-empty-optional-accordion">
                    <button
                      type="button"
                      onClick={() => setShowAllOptional((prev) => !prev)}
                      className="accordion-toggle-btn"
                    >
                      <span>
                        {showAllOptional
                          ? t("drawer.hideEmptyAttributes")
                          : t("drawer.showEmptyAttributes", { count: emptyOptionalAttrs.length })}
                      </span>
                      {showAllOptional ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                    </button>

                    {showAllOptional && (
                      <div className="drawer-attrs-form-grid accordion-expanded-content">
                        {emptyOptionalAttrs.map((attr) => {
                          const val = attributes[attr.name] || "";
                          const isNum = attr.type === "number" || !!attr.unit;

                          return (
                            <div key={attr.name} className="drawer-attr-field-item">
                              <div className="attr-field-label-row">
                                <label className="attr-field-label attr-label-optional">
                                  <span>{attr.label || attr.name}</span>
                                  {attr.unit && <span className="attr-unit-tag">{attr.unit}</span>}
                                </label>
                              </div>

                              <AttributeCombobox
                                name={attr.name}
                                value={val}
                                options={attr.options}
                                required={false}
                                disabled={!demoMode && (!serverLoaded || serverLoading || saving || syncing)}
                                dimmed={true}
                                placeholder={attr.placeholder || t("drawer.leaveEmpty")}
                                onChange={(v) => handleAttrChange(attr.name, v, isNum)}
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

          {/* =========================================================
              6. RESTART PRICING ACCORDION
              ========================================================= */}
          <div className="drawer-stage-card">
            <details className="drawer-details-group">
              <summary className="drawer-details-summary">
                <div className="summary-title-flex">
                  <Tag size={13} className="summary-icon" />
                  <span>{t("drawer.pricingFormulaTitle")}</span>
                </div>
                <ChevronDown size={14} className="details-chevron" />
              </summary>
              <div className="details-expanded-inner">
                <div className={restartUvpCents !== undefined && !demoMode ? "pricing-mini-grid pricing-mini-grid-uvp" : "pricing-mini-grid"}>
                  <div className="pricing-card card-old">
                    <span className="pricing-card-sub">{demoMode ? t("drawer.priceBeforeDrop") : targetPriceAvailable ? "Выбранная база расчёта" : "Исходные цены OTTO / Aftercool"}</span>
                    <strong className="pricing-card-val">
                      {demoMode ? formatPriceEur(priceProduct.oldStandardPriceCents) : targetPriceAvailable ? formatPriceEur(priceProduct.oldStandardPriceCents) : sourcePriceAvailable ? "См. источники ниже" : "Не получена"}
                    </strong>
                  </div>
                  <div className="pricing-card card-discount">
                    <span className="pricing-card-sub">{t("drawer.fixedDiscount", { account: product.account })}</span>
                    <strong className="pricing-card-val color-amber">
                      -{discountAmount}
                    </strong>
                  </div>
                  <div className="pricing-card card-restart">
                    <span className="pricing-card-sub">{t("drawer.newRestartPrice")}</span>
                    <strong className="pricing-card-val color-green">
                      {targetPriceAvailable && restartPriceCents !== undefined ? formatPriceEur(restartPriceCents) : "Не подтверждена"}
                    </strong>
                  </div>
                  {!demoMode && restartUvpCents !== undefined && (
                    <div className="pricing-card card-uvp">
                      <span className="pricing-card-sub">UVP / MSRP</span>
                      <strong className="pricing-card-val">{formatPriceEur(restartUvpCents)}</strong>
                    </div>
                  )}
                </div>
                {!demoMode && <p className="safe-note">
                  {`OTTO standardPrice: ${priceProduct.ottoSourcePriceCents !== null && priceProduct.ottoSourcePriceCents !== undefined ? formatPriceEur(priceProduct.ottoSourcePriceCents) : "нет подтверждённых данных"}`}
                  {priceProduct.aftercoolSourcePriceCents !== null && priceProduct.aftercoolSourcePriceCents !== undefined
                    ? ` · Aftercool Startpreis: ${formatPriceEur(priceProduct.aftercoolSourcePriceCents)}` : ""}
                  {` · ${targetPriceAvailable ? `целевая цена рассчитана по ${priceSourceLabel ?? "подтверждённому источнику"}` : "целевая цена не подтверждена; статус готовности не меняется"}`}
                  {priceProduct.priceCapturedAt ? ` · снимок: ${new Date(priceProduct.priceCapturedAt).toLocaleString("ru-RU")}` : ""}
                </p>}
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
                  <span>{t("drawer.sourceProductData")}</span>
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
                      attributes: attributesRef.current,
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
          <div className="footer-right-buttons">
            <button
              type="button"
              onClick={handleClose}
              className="drawer-footer-btn-cancel"
            >
              {t("drawer.cancel")}
            </button>
            {(demoMode ? Boolean(onSaveProduct) : true) ? <button
              type="button"
              onClick={handleSave}
              disabled={saving || syncing || isGeneratingAiTitle || (demoMode ? !category : (!serverLoaded || serverLoading || serverConflict))}
              className="drawer-footer-btn-save"
            >
              {saving ? (
                <Loader2 size={14} className="spin" />
              ) : (
                <Save size={14} />
              )}
              <span>{saving ? t("drawer.saving") : demoMode ? t("drawer.save") : "Сохранить изменения"}</span>
            </button> : null}
            {!demoMode && <span className="safe-note" role={draftError ? "alert" : undefined}>
              {draftError || (serverLoading ? "Загружаем сохранённые изменения…" : saveNotice)}
            </span>}
            {!demoMode && (serverConflict || (!serverLoaded && !serverLoading)) && <button type="button" className="drawer-footer-btn-secondary" disabled={syncing || isGeneratingAiTitle} onClick={() => {
              if (!serverConflict || window.confirm("Обновить данные с сервера? Текущие несохранённые изменения будут заменены.")) setReloadEdit((value) => value + 1);
            }}>Обновить с сервера</button>}
            {!demoMode && serverLoaded && localDraft && !importedDraft && <button type="button" className="drawer-footer-btn-secondary" disabled={syncing || isGeneratingAiTitle} onClick={() => {
              if (serverVersion > 0 && !window.confirm("Загрузить локальный черновик в форму? При сохранении он заменит текущие серверные изменения.")) return;
              setTitle(localDraft.title); setTitleDraft(localDraft.title); setIsEditingTitle(false);
              isUserCategoryChangeRef.current = false;
              setCategory(localDraft.category); setAttributes(localDraft.attributes); attributesRef.current = localDraft.attributes;
              setAiFilledAttrs(new Set());
              setImportedDraft(localDraft); setSaveNotice("Локальные изменения загружены в форму. Сохраните их на сервере.");
            }}>Перенести локальный черновик</button>}
          </div>
        </div>

        {/* Image Zoom Portal */}
        <ImageHoverPreview preview={imgPreview} />
      </aside>
    </>
  );
}
