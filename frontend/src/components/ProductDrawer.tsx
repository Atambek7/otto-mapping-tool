"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { X, Check, Save, Wand2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CategoryAutocomplete } from "@/components/CategoryAutocomplete";
import { AttributeCombobox } from "@/components/AttributeCombobox";
import { fetchWithAuth } from "@/lib/api";
import { toast } from "sonner";

interface ProductDrawerProps {
  product: any;
  factoryId: string;
  onClose: () => void;
  onSave: (ean: string, category: string, categoryName: string, attrs: Record<string, string>, status: string) => void;
}

export function ProductDrawer({ product, factoryId, onClose, onSave }: ProductDrawerProps) {
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

  const row = product?.row || {};
  const ean = product?.ean || row.EAN || "";
  const name = row.Artikelbeschreibung || product?.name || "";
  const priceStr = row.Startpreis || row.SofortkaufenPreis || "0";
  const price = parseFloat(priceStr.replace(',', '.')) > 0 ? priceStr : (row.SofortkaufenPreis || "");
  const img = row.GalleryURL || row.PictureURL || "";

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
    setAttributes(attributesRef.current);
    // Once user manually edits a field, it's no longer AI-filled
    setAiFilledAttrs(prev => { const next = new Set(prev); next.delete(name); return next; });
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
          toast.error("Отсутствует ключ ChatGPT", {
            id: "openai-error-toast",
            description: "Перейдите в настройки и укажите ваш OpenAI API ключ.",
            duration: 7000,
          });
        } else if (errorCode === "OPENAI_API_ERROR") {
          toast.error("Ошибка API ChatGPT", {
            id: "openai-error-toast",
            description: "Ошибка при обращении к OpenAI — проверьте баланс или лимиты.",
            duration: 7000,
          });
        } else {
          toast.error("AI не смог определить категорию", { id: "openai-error-toast" });
        }
      }
    } catch (e) {
      console.error(e);
      toast.error("Ошибка соединения с сервером", { id: "network-error-toast" });
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
          toast.error("Отсутствует ключ ChatGPT", {
            id: "openai-error-toast",
            description: "Перейдите в настройки и укажите ваш OpenAI API ключ.",
            duration: 7000,
          });
        } else if (errorCode === "OPENAI_API_ERROR") {
          toast.error("Ошибка API ChatGPT", {
            id: "openai-error-toast",
            description: "Ошибка при обращении к OpenAI — проверьте баланс или лимиты.",
            duration: 7000,
          });
        } else {
          toast.error("AI не смог заполнить атрибуты", { id: "openai-error-toast" });
        }
      }
    } catch (e) {
      console.error(e);
      toast.error("Ошибка соединения с сервером", { id: "network-error-toast" });
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
          account: "JV" // Fallback, could pass account if needed
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
            if (fetchdata.category_id) {
              setCategory(fetchdata.category_id);
              if (fetchdata.category_name) {
                setCategoryName(fetchdata.category_name);
              }
            }
            if (fetchdata.attributes && Object.keys(fetchdata.attributes).length > 0) {
              const cleanAttrs: Record<string, string> = {};
              for (const [k, v] of Object.entries(fetchdata.attributes)) {
                if (v && String(v).toLowerCase() !== "null") {
                  cleanAttrs[k] = String(v);
                }
              }
              setAttributes(cleanAttrs);
              attributesRef.current = cleanAttrs;
            }
          }
        } else if (r && r.status === "not_found") {
          alert("Product not found in OKB.");
        } else {
          alert("Error syncing from OKB.");
        }
      }
    } catch (e: any) {
      alert("Failed to sync OKB");
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
    <div className="fixed inset-y-0 right-0 w-full sm:w-[450px] bg-white dark:bg-[#0B0F19] border-l border-slate-200 dark:border-slate-800 shadow-2xl z-50 flex flex-col transform transition-transform duration-300">
      
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-slate-100 dark:border-slate-800/50">
        <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-100">Product Mapping</h2>
        <button onClick={onClose} className="p-1.5 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 rounded-md hover:bg-slate-100 dark:hover:bg-slate-800/50">
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* Product Info */}
        <div className="p-5 border-b border-slate-100 dark:border-slate-800/50 bg-slate-50/50 dark:bg-[#0B0F19]">
          <div className="flex gap-4">
            <div className="w-20 h-20 bg-white rounded-xl border border-slate-200 dark:border-slate-800 overflow-hidden shrink-0">
              {img && <img src={img} alt="" className="w-full h-full object-contain" />}
            </div>
            <div>
              <div className="text-xs text-slate-400 font-mono mb-1">{ean}</div>
              <div className="text-sm font-medium text-slate-800 dark:text-slate-200 leading-snug line-clamp-3">{name}</div>
              <div className="text-sm text-slate-500 dark:text-slate-400 mt-1 font-mono">{price} €</div>
            </div>
          </div>
        </div>

        {/* Stage 1: Category */}
        <div className="p-5 space-y-3">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400 dark:text-slate-500">1. Otto Category</h3>
            <div className="flex gap-2">
              <Button 
                variant="outline" 
                size="sm" 
                onClick={handleSyncOkb} 
                disabled={isSyncingOkb}
                className="h-7 text-xs border-emerald-200 text-emerald-600 hover:bg-emerald-50 dark:border-emerald-900/50 dark:text-emerald-400 dark:hover:bg-emerald-900/20"
              >
                {isSyncingOkb ? "Wait..." : "Sync OKB Data"}
              </Button>
              <Button 
                variant="outline" 
                size="sm" 
                onClick={handlePredictCategory} 
                disabled={isPredictingCategory}
                className="h-7 text-xs border-indigo-200 text-indigo-600 hover:bg-indigo-50 dark:border-indigo-900/50 dark:text-indigo-400 dark:hover:bg-indigo-900/20"
              >
                <Wand2 className="w-3 h-3 mr-1" />
                {isPredictingCategory ? "Predicting..." : "Auto AI"}
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
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400 dark:text-slate-500">2. Required Attributes</h3>
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
                    {isExtractingAttributes ? "Wait..." : "Auto AI"}
                  </Button>
                  <Button 
                    variant="outline" 
                    size="sm" 
                    onClick={() => handleExtractAttributes(true)} 
                    disabled={isExtractingAttributes}
                    className="h-7 text-xs border-indigo-200 text-indigo-600 hover:bg-indigo-50 dark:border-indigo-900/50 dark:text-indigo-400 dark:hover:bg-indigo-900/20"
                    title="Использовать Vision для анализа по фото"
                  >
                    <Wand2 className="w-3 h-3 mr-1" />
                    AI Vision
                  </Button>
                </>
              )}
              {category && schema.length > 0 && (
                <span className="text-[10px] bg-emerald-100 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-400 px-2 py-0.5 rounded-full font-medium">
                  Loaded
                </span>
              )}
            </div>
          </div>

          {!category ? (
            <div className="text-sm text-slate-400 dark:text-slate-500 p-4 border border-dashed dark:border-slate-800 rounded-xl text-center">
            </div>
          ) : schema.length === 0 ? (
            <div className="text-sm text-slate-400 dark:text-slate-500 p-4 border border-dashed dark:border-slate-800 rounded-xl text-center">
              Loading schema...
            </div>
          ) : (
            <div className="space-y-4">
              {/* Required attributes (HIGH relevance) */}
              {schema.filter((a: any) => a.required).map((attr: any) => {
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
                        <span className="ml-auto text-[10px] text-emerald-500 font-normal">✓ заполнено</span>
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
              {schema.filter((a: any) => !a.required).length > 0 && (
                <details className="group">
                  <summary className="cursor-pointer text-xs font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider py-2 flex items-center gap-2 hover:text-slate-600 dark:hover:text-slate-300 transition-colors select-none">
                    <span className="flex-1 border-t border-dashed border-slate-200 dark:border-slate-700/50" />
                    Дополнительные ({schema.filter((a: any) => !a.required).length})
                    <span className="transition-transform group-open:rotate-180 text-[10px]">▼</span>
                  </summary>
                  <div className="space-y-4 mt-3">
                    {schema
                      .filter((a: any) => !a.required)
                      .sort((a: any, b: any) => {
                        const hasA = attributes[a.name] ? 1 : 0;
                        const hasB = attributes[b.name] ? 1 : 0;
                        if (hasA !== hasB) return hasB - hasA;
                        return a.name.localeCompare(b.name);
                      })
                      .map((attr: any) => {
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
                            placeholder="Оставить пустым..." 
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
              Source Data (Raw JSON)
            </summary>
            <pre className="mt-2 text-[10px] p-3 rounded-xl bg-slate-800 text-slate-300 overflow-x-auto">
              {JSON.stringify(row, null, 2)}
            </pre>
          </details>
        </div>
      </div>

      {/* Footer / Save */}
      <div className="p-4 border-t border-slate-100 dark:border-slate-800 bg-slate-50 dark:bg-[#0B0F19]/50 backdrop-blur-md">
        <Button onClick={handleSave} disabled={loading || !category} className="w-full bg-indigo-600 hover:bg-indigo-700">
          <Save className="w-4 h-4 mr-2" />
          {loading ? "Saving..." : "Save Mapping"}
        </Button>
      </div>

    </div>
  );
}
