"use client";

import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { X, Check, Save, Wand2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CategoryAutocomplete } from "@/components/CategoryAutocomplete";
import { AttributeCombobox } from "@/components/AttributeCombobox";
import { fetchWithAuth } from "@/lib/api";
import { toast } from "sonner";
import { ImageHoverPreview, HoverImagePreviewState } from "@/components/ImageHoverPreview";
import { useTranslation } from "react-i18next";
import { useAccount } from "@/lib/useAccount";

interface ProductDrawerProps {
  product: any;
  factoryId: string;
  onClose: () => void;
  onSave: (ean: string, category: string, categoryName: string, attrs: Record<string, string>, status: string) => void;
}

export function ProductDrawer({ product, factoryId, onClose, onSave }: ProductDrawerProps) {
  const { t } = useTranslation();
  const [category, setCategory] = useState("");
  const [categoryName, setCategoryName] = useState("");
  const [attributes, setAttributes] = useState<Record<string, string>>({});
  const attributesRef = useRef<Record<string, string>>({});
  // Track which fields were filled by AI (yellow highlight)
  const [aiFilledAttrs, setAiFilledAttrs] = useState<Set<string>>(new Set());
  const [schema, setSchema] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [isPredictingCategory, setIsPredictingCategory] = useState(false);
  const [isExtractingAttributes, setIsExtractingAttributes] = useState(false);
  const [isSyncingOkb, setIsSyncingOkb] = useState(false);
  const [imgPreview, setImgPreview] = useState<HoverImagePreviewState | null>(null);

  const row = product?.row || {};
  const ean = product?.ean || row.EAN || "";
  const name = row.Artikelbeschreibung || product?.name || "";
  const priceStr = row.Startpreis || row.SofortkaufenPreis || "0";
  const price = parseFloat(priceStr.replace(',', '.')) > 0 ? priceStr : (row.SofortkaufenPreis || "");
  const img = row.GalleryURL || row.PictureURL || product?.img || product?.image_url || "";

  // 1. Fetch Draft/Mapping Data on mount
  useEffect(() => {
    if (!ean) return;
    const fetchExisting = async () => {
      setLoading(true);
      try {
        const res = await fetchWithAuth(`/mapping/product/${ean}`);
        if (res.ok) {
          const data = await res.json();
          if (data.category_id) {
            setCategory(data.category_id);
            if (data.category_name) {
              setCategoryName(data.category_name);
            }
          }
          if (data.attributes && Object.keys(data.attributes).length > 0) {
            const cleanAttrs: Record<string, string> = {};
            for (const [k, v] of Object.entries(data.attributes)) {
              if (v && String(v).toLowerCase() !== "null") {
                cleanAttrs[k] = String(v);
              }
            }
            setAttributes(cleanAttrs);
            attributesRef.current = cleanAttrs;
          }
        }
      } catch (e) {
        console.error("Failed to load existing mapping", e);
      } finally {
        setLoading(false);
      }
    };
    fetchExisting();
  }, [ean]);

  // 2. Fetch Category Schema when category changes
  useEffect(() => {
    if (!category) {
      setSchema([]);
      return;
    }
    const loadSchema = async () => {
      setLoading(true);
      try {
        const res = await fetchWithAuth(`/mapping/categories/${encodeURIComponent(category)}/attributes`);
        if (res.ok) {
          const data = await res.json();
          setSchema(data);
        }
      } catch (e) {
        console.error("Failed to load schema", e);
      } finally {
        setLoading(false);
      }
    };
    loadSchema();
  }, [category]);

  const handleAttrChange = useCallback((name: string, val: string) => {
    attributesRef.current = { ...attributesRef.current, [name]: val };
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
      gallery_url: row.GalleryURL || "",
      picture_urls: row.PictureURL || ""
    };
  };

  // Memoize schema splits to avoid re-filtering on every render
  const requiredAttrs = useMemo(() => schema.filter((a: any) => a.required), [schema]);
  const sortedOptionalAttrs = useMemo(() =>
    schema
      .filter((a: any) => !a.required)
      .sort((a: any, b: any) => {
        const hasA = attributes[a.name] ? 1 : 0;
        const hasB = attributes[b.name] ? 1 : 0;
        if (hasA !== hasB) return hasB - hasA;
        return a.name.localeCompare(b.name);
      }),
  [schema, attributes]);

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
          setCategory(data.category_id);
          setCategoryName(data.category_name || data.category_id);
        }
      } else {
        const errorCode = res.headers.get("x-error-code");
        if (errorCode === "OPENAI_API_KEY_MISSING") {
          toast.error(t("product_drawer.toast_openai_missing_title"), {
            id: "openai-error-toast",
            description: t("product_drawer.toast_openai_missing_desc"),
            duration: 7000,
          });
        } else if (errorCode === "OPENAI_API_ERROR") {
          toast.error(t("product_drawer.toast_openai_error_title"), {
            id: "openai-error-toast",
            description: t("product_drawer.toast_openai_error_desc"),
            duration: 7000,
          });
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

  const handleExtractAttributes = async (useVision: boolean = false) => {
    if (!category) return;
    setIsExtractingAttributes(true);
    try {
      const res = await fetchWithAuth("/mapping/extract-attributes", {
        method: "POST",
        body: JSON.stringify({ ...getAiProductData(), category_id: category, use_vision: useVision })
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
        if (errorCode === "OPENAI_API_KEY_MISSING") {
          toast.error(t("product_drawer.toast_openai_missing_title"), {
            id: "openai-error-toast",
            description: t("product_drawer.toast_openai_missing_desc"),
            duration: 7000,
          });
        } else if (errorCode === "OPENAI_API_ERROR") {
          toast.error(t("product_drawer.toast_openai_error_title"), {
            id: "openai-error-toast",
            description: t("product_drawer.toast_openai_error_desc"),
            duration: 7000,
          });
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

  const { account } = useAccount();

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
          // Reload the mapping data
          const fetchRes = await fetchWithAuth(`/mapping/product/${ean}`);
          if (fetchRes.ok) {
            const fetchdata = await fetchRes.json();
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
              setAttributes(cleanAttrs);
              attributesRef.current = cleanAttrs;
            }
            // Update parent table mapping state immediately
            if (newCatId) {
              onSave(ean, newCatId, newCatName, cleanAttrs, fetchdata.status || "Ready");
            }
          }
        } else if (r && (r.status === "not_found" || r.status === "unresolved")) {
          alert(t("product_drawer.sync_not_found"));
        } else {
          alert(t("product_drawer.sync_error"));
        }
      }
    } catch (e: any) {
      alert(t("product_drawer.sync_failed"));
    } finally {
      setIsSyncingOkb(false);
    }
  };

  const handleSave = async () => {
    setLoading(true);
    try {
      // 1. Assign Category
      if (category) {
        await fetchWithAuth("/mapping/category", {
          method: "POST",
          body: JSON.stringify({ product_ean: ean, factory_id: factoryId, category_id: category }),
        });
      }

      // 2. Assign Attributes — read back the real status from server
      const currentAttrs = attributesRef.current;
      let realStatus = "Need Attributes";
      if (Object.keys(currentAttrs).length > 0) {
        const attrRes = await fetchWithAuth("/mapping/attributes", {
          method: "POST",
          body: JSON.stringify({ product_ean: ean, attributes: currentAttrs }),
        });
        if (attrRes.ok) {
          const attrData = await attrRes.json();
          // server returns { status: "success", errors: [] }
          realStatus = (attrData.errors && attrData.errors.length > 0) ? "Error" : "Ready";
        }
      }

      onSave(ean, category, categoryName, currentAttrs, realStatus);
      onClose();
    } catch (e) {
      console.error(e);
      alert("Failed to save mapping");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-y-0 right-0 w-full sm:w-[480px] bg-white dark:bg-card border-l border-slate-200 dark:border-slate-800 shadow-2xl z-50 flex flex-col transform transition-transform duration-300">
      
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100 dark:border-slate-800 bg-white dark:bg-card">
        <h2 className="text-sm font-bold text-slate-900 dark:text-white">{t("product_drawer.title")}</h2>
        <button
          onClick={onClose}
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
                  price: price ? `${price}` : "",
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
              <span className="text-[11px] text-slate-500 font-mono bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 rounded border border-slate-200/60 dark:border-slate-700/50 inline-block mb-1">
                {ean || t("product_drawer.no_ean")}
              </span>
              <p className="text-xs font-medium text-slate-900 dark:text-white leading-snug line-clamp-2">{name}</p>
              <div className="text-xs font-semibold text-slate-700 dark:text-slate-300 mt-1 tabular-nums">{price ? `${price} €` : "—"}</div>
            </div>
          </div>
        </div>

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
            onChange={(id, name) => {
              setCategory(id);
              setCategoryName(name);
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
                    onClick={() => handleExtractAttributes(false)} 
                    disabled={isExtractingAttributes}
                    className="h-7 text-xs border-emerald-200 text-emerald-600 hover:bg-emerald-50 dark:border-emerald-900/50 dark:text-emerald-400 dark:hover:bg-emerald-900/20"
                  >
                    <Wand2 className="w-3 h-3 mr-1" />
                    {isExtractingAttributes ? t("common.loading") : t("product_drawer.auto_ai")}
                  </Button>
                  <Button 
                    variant="outline" 
                    size="sm" 
                    onClick={() => handleExtractAttributes(true)} 
                    disabled={isExtractingAttributes}
                    className="h-7 text-xs border-indigo-200 text-indigo-600 hover:bg-indigo-50 dark:border-indigo-900/50 dark:text-indigo-400 dark:hover:bg-indigo-900/20"
                    title={t("product_drawer.ai_vision_hint")}
                  >
                    <Wand2 className="w-3 h-3 mr-1" />
                    {t("product_drawer.ai_vision")}
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
                return (
                  <div key={attr.name} className="space-y-1.5">
                    <label className="text-xs font-medium text-slate-700 dark:text-slate-300 flex items-center gap-1">
                      {attr.name}
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
                      onChange={(v) => handleAttrChange(attr.name, v)}
                      isAiFilled={isAiFilled}
                    />
                  </div>
                );
              })}

              {/* Optional attributes */}
              {sortedOptionalAttrs.length > 0 && (
                <details className="group">
                  <summary className="cursor-pointer text-xs font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider py-2 flex items-center gap-2 hover:text-slate-600 dark:hover:text-slate-300 transition-colors select-none">
                    <span className="flex-1 border-t border-dashed border-slate-200 dark:border-slate-700/50" />
                    {t("product_drawer.optional_attributes", { count: sortedOptionalAttrs.length })}
                    <span className="transition-transform group-open:rotate-180 text-[10px]">▼</span>
                  </summary>
                  <div className="space-y-4 mt-3">
                    {sortedOptionalAttrs.map((attr: any) => {
                      const isAiFilled = aiFilledAttrs.has(attr.name);
                      return (
                        <div key={attr.name} className="space-y-1.5">
                          <label className="text-xs font-medium text-slate-500 dark:text-slate-400 flex items-center gap-1">
                            {attr.name}
                            {isAiFilled && (
                              <span className="ml-1 text-[9px] bg-amber-100 dark:bg-amber-900/40 text-amber-600 dark:text-amber-400 px-1.5 py-0.5 rounded-full font-semibold tracking-wide">✦ AI</span>
                            )}
                            {attributes[attr.name] && (
                              <span className="ml-auto text-[10px] text-emerald-500 font-normal">✓</span>
                            )}
                          </label>
                          <AttributeCombobox
                            name={attr.name}
                            value={attributes[attr.name] || ""}
                            options={attr.options}
                            placeholder={t("product_drawer.leave_empty")}
                            onChange={(v) => handleAttrChange(attr.name, v)}
                            dimmed
                            isAiFilled={isAiFilled}
                          />
                        </div>
                      );
                    })}
                  </div>
                </details>
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
          disabled={loading || !category}
          className="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-semibold shadow-xs h-10"
        >
          <Save className="w-4 h-4 mr-2" />
          {loading ? t("product_drawer.saving") : t("product_drawer.save_mapping")}
        </Button>
      </div>

      <ImageHoverPreview preview={imgPreview} />

    </div>
  );
}
