"use client";

import { useState, useEffect } from "react";
import { X, Save, AlertCircle, Wand2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CategoryAutocomplete } from "@/components/CategoryAutocomplete";
import { fetchWithAuth } from "@/lib/api";
import { toast } from "sonner";

interface BulkEditorDrawerProps {
  selectedEans: string[];
  factoryId: string;
  sampleProduct?: any;
  onClose: () => void;
  onSave: (category: string, categoryName: string, attrs: Record<string, string>) => void;
}

export function BulkEditorDrawer({ selectedEans, factoryId, sampleProduct, onClose, onSave }: BulkEditorDrawerProps) {
  const [category, setCategory] = useState("");
  const [categoryName, setCategoryName] = useState("");
  const [attributes, setAttributes] = useState<Record<string, string>>({});
  const [schema, setSchema] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [isPredictingCategory, setIsPredictingCategory] = useState(false);
  const [isExtractingAttributes, setIsExtractingAttributes] = useState(false);

  // Fetch Category Schema when category changes
  useEffect(() => {
    if (!category) {
      setSchema([]);
      return;
    }
    const loadSchema = async () => {
      setLoading(true);
      try {
        const res = await fetchWithAuth(`/mapping/categories/${category}/attributes`);
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

  const handleAttrChange = (name: string, val: string) => {
    setAttributes((prev) => ({ ...prev, [name]: val }));
  };

  const getAiProductData = () => {
    const row = sampleProduct?.row || {};
    let specificsStr = "";
    const rawSpec = row.CustomItemSpecifics || row.ItemSpecifics || "";
    if (rawSpec) {
      try {
          const parsed = typeof rawSpec === "string" ? JSON.parse(rawSpec) : rawSpec;
          if (Array.isArray(parsed)) {
              specificsStr = parsed.map((i: any) => `${i.Name}: ${i.Value}`).join(", ");
          } else if (typeof parsed === "object") {
              specificsStr = Object.entries(parsed).map(([k, v]) => `${k}: ${v}`).join(", ");
          }
      } catch(e) {
          specificsStr = String(rawSpec);
      }
    }
    return {
      ean: sampleProduct?.ean || row.EAN || "",
      title: row.Artikelbeschreibung || sampleProduct?.name || "",
      specifics: specificsStr,
      gallery_url: row.GalleryURL || "",
      picture_urls: row.PictureURLs || row.PictureURL || ""
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
          setAttributes(data.attributes);
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

  const handleSave = async () => {
    setLoading(true);
    try {
      // 1. Assign Category
      if (category) {
        await fetchWithAuth("/mapping/category/bulk", {
          method: "POST",
          body: JSON.stringify({ product_eans: selectedEans, factory_id: factoryId, category_id: category }),
        });
      }

      // 2. Assign Attributes
      if (Object.keys(attributes).length > 0) {
        await fetchWithAuth("/mapping/attributes/bulk", {
          method: "POST",
          body: JSON.stringify({ product_eans: selectedEans, attributes }),
        });
      }

      onSave(category, categoryName, attributes);
      onClose();
    } catch (e) {
      console.error(e);
      alert("Failed to save bulk mapping");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-y-0 right-0 w-full sm:w-[450px] bg-white dark:bg-[#0B0F19] border-l border-slate-200 dark:border-slate-800 shadow-2xl z-50 flex flex-col transform transition-transform duration-300">
      
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-slate-100 dark:border-slate-800/50 bg-indigo-50/50 dark:bg-indigo-900/10">
        <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-100">Mass Edit ({selectedEans.length} products)</h2>
        <button onClick={onClose} className="p-1.5 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 rounded-md hover:bg-slate-100 dark:hover:bg-slate-800/50">
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="p-4 bg-amber-50 dark:bg-amber-950/30 border-b border-amber-100 dark:border-amber-900/50 text-amber-800 dark:text-amber-200/90 text-xs flex gap-2">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <p>
            Changes made here will be applied to <strong>all {selectedEans.length} selected products</strong>. 
            Empty attribute fields will be ignored and won't overwrite existing product values.
          </p>
        </div>

        {/* Stage 1: Category */}
        <div className="p-5 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400 dark:text-slate-500">1. Common Category</h3>
            <Button 
              variant="outline" 
              size="sm" 
              onClick={handlePredictCategory} 
              disabled={isPredictingCategory || !sampleProduct}
              className="h-7 text-xs border-indigo-200 text-indigo-600 hover:bg-indigo-50 dark:border-indigo-900/50 dark:text-indigo-400 dark:hover:bg-indigo-900/20"
            >
              <Wand2 className="w-3 h-3 mr-1" />
              {isPredictingCategory ? "Predicting..." : "Auto AI"}
            </Button>
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
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400 dark:text-slate-500">2. Common Attributes</h3>
            <div className="flex gap-2 items-center">
              {category && schema.length > 0 && (
                <>
                  <Button 
                    variant="outline" 
                    size="sm" 
                    onClick={() => handleExtractAttributes(false)} 
                    disabled={isExtractingAttributes || !sampleProduct}
                    className="h-7 text-xs border-emerald-200 text-emerald-600 hover:bg-emerald-50 dark:border-emerald-900/50 dark:text-emerald-400 dark:hover:bg-emerald-900/20"
                  >
                    <Wand2 className="w-3 h-3 mr-1" />
                    {isExtractingAttributes ? "Extracting..." : "Auto AI"}
                  </Button>
                  <Button 
                    variant="outline" 
                    size="sm" 
                    onClick={() => handleExtractAttributes(true)} 
                    disabled={isExtractingAttributes || !sampleProduct}
                    className="h-7 text-xs border-emerald-200 text-emerald-600 hover:bg-emerald-50 dark:border-emerald-900/50 dark:text-emerald-400 dark:hover:bg-emerald-900/20"
                    title="Использовать Vision для анализа по фото"
                  >
                    <Wand2 className="w-3 h-3 mr-1" />
                    Vision AI
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
              Select a category to see common attributes.
            </div>
          ) : schema.length === 0 ? (
            <div className="text-sm text-slate-400 dark:text-slate-500 p-4 border border-dashed dark:border-slate-800 rounded-xl text-center">
              Loading schema...
            </div>
          ) : (
            <div className="space-y-4">
              {schema.map((attr) => (
                <div key={attr.name} className="space-y-1.5">
                  <label className="text-xs font-medium text-slate-700 dark:text-slate-300">
                    {attr.name} {attr.required && <span className="text-red-500">*</span>}
                  </label>
                  {attr.type === "select" ? (
                    <select
                      className="w-full h-8 text-sm border-slate-200 dark:border-slate-800 rounded-md px-2 bg-white dark:bg-[#0B0F19] dark:text-slate-200"
                      value={attributes[attr.name] || ""}
                      onChange={(e) => handleAttrChange(attr.name, e.target.value)}
                    >
                      <option value="">Leave unchanged / Do not set</option>
                      {attr.options?.map((opt: string) => (
                        <option key={opt} value={opt}>{opt}</option>
                      ))}
                    </select>
                  ) : (
                    <Input
                      value={attributes[attr.name] || ""}
                      onChange={(e) => handleAttrChange(attr.name, e.target.value)}
                      className="h-8 text-sm"
                      placeholder={`Leave blank to ignore...`}
                    />
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Footer / Save */}
      <div className="p-4 border-t border-slate-100 dark:border-slate-800 bg-slate-50 dark:bg-[#0B0F19]/50 backdrop-blur-md">
        <Button onClick={handleSave} disabled={loading || !category} className="w-full bg-indigo-600 hover:bg-indigo-700">
          <Save className="w-4 h-4 mr-2" />
          {loading ? "Applying..." : `Apply to ${selectedEans.length} Products`}
        </Button>
      </div>

    </div>
  );
}
