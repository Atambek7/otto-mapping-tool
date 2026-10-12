"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  X,
  Loader2,
  Save,
  Layers,
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Sparkles,
  Wand2,
  Users,
  Search
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { AttributeCombobox } from "@/components/AttributeCombobox";
import { fetchWithAuth } from "@/lib/api";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

interface SharedAttr {
  id: string;
  name: string;
  type: string;
  required: boolean;
  options: string[] | null;
  from_multiple_cats: boolean;
  applicable_categories?: string[];
  applicable_eans_count?: number;
}

interface CategoryInfo {
  id: string;
  name: string;
  ean_count: number;
}

interface MassEditSchema {
  shared_attrs: SharedAttr[];
  other_attrs?: SharedAttr[];
  current_values: Record<string, Record<string, string>>;
  categories: CategoryInfo[];
  unmapped_eans: string[];
  total_eans: number;
  mapped_eans: number;
}

interface PresetsData {
  account: string;
  global: Record<string, string>;
  account_specific: Record<string, string>;
  merged_defaults: Record<string, string>;
  zielgruppe_options: string[];
  design_options?: string[];
}

interface Props {
  selectedEans: string[];
  account?: string;
  onClose: () => void;
  onSaved: (updatedEans: string[]) => void;
}

export function MassEditDrawer({ selectedEans, account = "JV", onClose, onSaved }: Props) {
  const [schema, setSchema] = useState<MassEditSchema | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [attrs, setAttrs] = useState<Record<string, string>>({});
  const [showOptional, setShowOptional] = useState(false);
  const [showOther, setShowOther] = useState(false);
  const [otherSearchQuery, setOtherSearchQuery] = useState("");
  const [presets, setPresets] = useState<PresetsData | null>(null);
  const [selectedTargetGroup, setSelectedTargetGroup] = useState<string>("Erwachsene ; Jugendliche");
  const attrsRef = useRef<Record<string, string>>({});

  useEffect(() => {
    fetchWithAuth(`/mapping/attribute-presets?account=${account || "JV"}`)
      .then((r) => r.json())
      .then((data: PresetsData) => {
        setPresets(data);
      })
      .catch(() => { });
  }, [account]);

  useEffect(() => {
    if (selectedEans.length === 0) return;
    let cancelled = false;
    setLoading(true);

    fetchWithAuth("/mapping/mass-edit/schema", {
      method: "POST",
      body: JSON.stringify({ product_eans: selectedEans, account: account || "JV" }),
    })
      .then((r) => r.json())
      .then((data: MassEditSchema) => {
        if (cancelled) return;
        setSchema(data);
        // Pre-fill attrs that are the same across ALL selected products
        const prefill: Record<string, string> = {};
        const allAttrs = [...data.shared_attrs, ...(data.other_attrs ?? [])];
        for (const attr of allAttrs) {
          const vals = new Set(
            selectedEans
              .map((ean) => data.current_values[ean]?.[attr.name] ?? "")
              .filter(Boolean)
          );
          if (vals.size === 1) {
            prefill[attr.name] = Array.from(vals)[0];
          }
        }
        setAttrs(prefill);
        attrsRef.current = prefill;
      })
      .catch(() => toast.error("Failed to load mass edit schema"))
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [selectedEans, account]);

  const handleAttrChange = useCallback((name: string, val: string) => {
    attrsRef.current = { ...attrsRef.current, [name]: val };
    setAttrs({ ...attrsRef.current });
  }, []);

  const handleApplyDefaults = () => {
    if (!presets || !schema) return;
    const defaults = { ...presets.merged_defaults };
    defaults["Zielgruppe"] = selectedTargetGroup;

    let filledCount = 0;
    const nextAttrs = { ...attrsRef.current };
    const allAttrs = [...schema.shared_attrs, ...(schema.other_attrs ?? [])];

    for (const attr of allAttrs) {
      const lowerName = attr.name.trim().toLowerCase();
      const foundEntry = Object.entries(defaults).find(
        ([k]) => k.trim().toLowerCase() === lowerName
      );
      if (foundEntry) {
        nextAttrs[attr.name] = foundEntry[1];
        filledCount++;
      }
    }

    attrsRef.current = nextAttrs;
    setAttrs(nextAttrs);
    setShowOptional(true);
    toast.success(`Заполнено ${filledCount} стандартных атрибутов (${account})`);
  };

  const handleSelectTargetGroup = (group: string) => {
    setSelectedTargetGroup(group);
    if (!schema) return;
    const allAttrs = [...schema.shared_attrs, ...(schema.other_attrs ?? [])];
    const targetAttr = allAttrs.find(
      (a) => a.name.trim().toLowerCase() === "zielgruppe"
    );
    if (targetAttr) {
      handleAttrChange(targetAttr.name, group);
    } else {
      handleAttrChange("Zielgruppe", group);
    }
  };

  const requiredAttrs = useMemo(
    () => schema?.shared_attrs.filter((a) => a.required) ?? [],
    [schema]
  );
  const optionalAttrs = useMemo(
    () => schema?.shared_attrs.filter((a) => !a.required) ?? [],
    [schema]
  );
  const otherAttrs = useMemo(
    () => schema?.other_attrs ?? [],
    [schema]
  );
  const filteredOtherAttrs = useMemo(() => {
    if (!otherSearchQuery.trim()) return otherAttrs;
    const q = otherSearchQuery.toLowerCase();
    return otherAttrs.filter(
      (a) =>
        a.name.toLowerCase().includes(q) ||
        a.applicable_categories?.some((c) => c.toLowerCase().includes(q))
    );
  }, [otherAttrs, otherSearchQuery]);

  // Detect attrs where existing values differ across selected products
  const mixedAttrs = useMemo(() => {
    if (!schema) return new Set<string>();
    const mixed = new Set<string>();
    const allAttrs = [...schema.shared_attrs, ...(schema.other_attrs ?? [])];
    for (const attr of allAttrs) {
      const vals = new Set(
        selectedEans.map((ean) => schema.current_values[ean]?.[attr.name] ?? "")
      );
      if (vals.size > 1) mixed.add(attr.name);
    }
    return mixed;
  }, [schema, selectedEans]);

  const handleSave = async () => {
    if (!schema) return;
    const toSave = Object.fromEntries(
      Object.entries(attrsRef.current).filter(([, v]) => v && v.trim())
    );
    if (Object.keys(toSave).length === 0) {
      toast.error("Fill at least one attribute before saving.");
      return;
    }
    setSaving(true);
    try {
      const eansList = selectedEans.filter((e) => !schema.unmapped_eans.includes(e));
      const res = await fetchWithAuth("/mapping/attributes/bulk", {
        method: "POST",
        body: JSON.stringify({ product_eans: eansList, attributes: toSave, account: account || "JV" }),
      });
      if (res.ok) {
        toast.success(`Saved ${Object.keys(toSave).length} attribute(s) for ${eansList.length} products`);
        onSaved(eansList);
        onClose();
      } else {
        const err = await res.json().catch(() => ({}));
        toast.error(err.detail || "Save failed");
      }
    } catch {
      toast.error("Network error while saving");
    } finally {
      setSaving(false);
    }
  };

  const hasAnyAttrs = schema && (schema.shared_attrs.length > 0 || (schema.other_attrs && schema.other_attrs.length > 0));

  return (
    <>
      <div
        className="fixed inset-0 z-40 bg-black/25 dark:bg-black/50 backdrop-blur-[2px] animate-in fade-in duration-200"
        onClick={onClose}
      />
      <div className="fixed right-0 top-0 bottom-0 z-50 w-[480px] max-w-[100vw] flex flex-col bg-white dark:bg-[#0E131F] border-l border-slate-200 dark:border-slate-800 shadow-2xl animate-in slide-in-from-right duration-250">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100 dark:border-slate-800 shrink-0">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-indigo-50 dark:bg-indigo-950/60 border border-indigo-200/80 dark:border-indigo-800/60 flex items-center justify-center shrink-0">
              <Layers className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
            </div>
            <div>
              <h2 className="text-sm font-bold text-slate-900 dark:text-white leading-tight">Mass Edit</h2>
              <p className="text-[11px] text-slate-400 mt-0.5">{selectedEans.length} products selected</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 rounded-lg flex items-center justify-center text-slate-400 hover:text-slate-700 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex flex-col items-center justify-center py-20 gap-3">
              <Loader2 className="w-8 h-8 animate-spin text-indigo-500" />
              <p className="text-sm text-slate-400">Loading attributes...</p>
            </div>
          ) : !schema || !hasAnyAttrs ? (
            <div className="flex flex-col items-center justify-center py-20 gap-4 px-8 text-center">
              <div className="w-14 h-14 rounded-2xl bg-amber-50 dark:bg-amber-950/40 flex items-center justify-center border border-amber-200/80 dark:border-amber-800/60">
                <AlertCircle className="w-7 h-7 text-amber-500" />
              </div>
              <div>
                <p className="font-semibold text-slate-800 dark:text-white text-sm">No attributes found</p>
                <p className="text-xs text-slate-400 mt-1.5 leading-relaxed">
                  {schema?.unmapped_eans?.length === selectedEans.length
                    ? "None of the selected products have a category mapping yet. Map them first."
                    : "Selected products belong to categories with no attributes available for editing."}
                </p>
              </div>
            </div>
          ) : (
            <div className="p-5 space-y-5">
              {/* Categories summary */}
              <div className="rounded-xl border border-slate-100 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-900/30 p-3 space-y-2">
                <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">Categories in selection</p>
                <div className="flex flex-wrap gap-1.5">
                  {schema.categories.map((c) => (
                    <span key={c.id} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-indigo-50 dark:bg-indigo-950/60 text-indigo-700 dark:text-indigo-300 border border-indigo-200/80 dark:border-indigo-800/60">
                      {c.name}
                      <span className="text-indigo-400 dark:text-indigo-500">×{c.ean_count}</span>
                    </span>
                  ))}
                </div>
                {schema.unmapped_eans.length > 0 && (
                  <p className="text-[11px] text-amber-600 dark:text-amber-400 flex items-center gap-1">
                    <AlertCircle className="w-3 h-3 shrink-0" />
                    {schema.unmapped_eans.length} unmapped product(s) will be skipped
                  </p>
                )}
                <p className="text-[11px] text-slate-400">
                  <span className="font-semibold text-indigo-600 dark:text-indigo-400">{schema.shared_attrs.length}</span> общих атрибутов
                  {otherAttrs.length > 0 && (
                    <span>, <span className="font-semibold text-indigo-600 dark:text-indigo-400">{otherAttrs.length}</span> остальных</span>
                  )}
                  {schema.categories.length > 1 ? " для выбранных категорий" : ""}
                </p>
              </div>

              {/* No shared attributes notice */}
              {schema.shared_attrs.length === 0 && otherAttrs.length > 0 && (
                <div className="flex items-start gap-2.5 p-3 rounded-xl bg-amber-50/80 dark:bg-amber-950/40 border border-amber-200/80 dark:border-amber-800/60">
                  <AlertCircle className="w-4 h-4 text-amber-500 shrink-0 mt-0.5" />
                  <p className="text-xs text-amber-700 dark:text-amber-300 leading-relaxed">
                    У выбранных категорий нет общих пересекающихся атрибутов. Вы можете заполнить атрибуты для отдельных категорий в блоке <strong>«Остальные атрибуты»</strong> ниже.
                  </p>
                </div>
              )}

              {/* Quick Presets Panel */}
              <div className="rounded-xl border border-indigo-100 dark:border-indigo-900/50 bg-gradient-to-br from-indigo-50/50 via-white to-slate-50 dark:from-indigo-950/20 dark:via-[#0E131F] dark:to-slate-900/40 p-3.5 space-y-3 shadow-xs">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5">
                    <Sparkles className="w-3.5 h-3.5 text-indigo-600 dark:text-indigo-400" />
                    <span className="text-xs font-semibold text-slate-800 dark:text-slate-200">
                      Стандартные константы & Пресеты
                    </span>
                  </div>
                  <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-md bg-indigo-100 dark:bg-indigo-950 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800">
                    {account}
                  </span>
                </div>

                {/* 1-Click fill button */}
                <div>
                  <Button
                    type="button"
                    size="sm"
                    onClick={handleApplyDefaults}
                    className="w-full h-8 text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 text-white shadow-xs gap-1.5"
                  >
                    <Wand2 className="w-3.5 h-3.5" />
                    <span>✨ Заполнить стандартные атрибуты</span>
                  </Button>
                </div>

                {/* Zielgruppe Selector */}
                <div className="space-y-1.5 pt-1.5 border-t border-slate-100 dark:border-slate-800">
                  <div className="flex items-center justify-between text-[11px] text-slate-500 dark:text-slate-400">
                    <span className="flex items-center gap-1 font-medium">
                      <Users className="w-3 h-3 text-slate-400" />
                      Zielgruppe (Целевая аудитория):
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-1.5">
                    <button
                      type="button"
                      onClick={() => handleSelectTargetGroup("Erwachsene ; Jugendliche")}
                      className={cn(
                        "h-7 px-2 text-[11px] font-medium rounded-lg border transition-all flex items-center justify-center gap-1",
                        (attrs["Zielgruppe"] === "Erwachsene ; Jugendliche" || (!attrs["Zielgruppe"] && selectedTargetGroup === "Erwachsene ; Jugendliche"))
                          ? "bg-indigo-50 dark:bg-indigo-950/60 border-indigo-300 dark:border-indigo-700 text-indigo-700 dark:text-indigo-300 shadow-xs font-semibold"
                          : "border-slate-200 dark:border-slate-800 text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800"
                      )}
                    >
                      <span>🧑 Взрослые / Подростки</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => handleSelectTargetGroup("Kinder")}
                      className={cn(
                        "h-7 px-2 text-[11px] font-medium rounded-lg border transition-all flex items-center justify-center gap-1",
                        attrs["Zielgruppe"] === "Kinder"
                          ? "bg-amber-50 dark:bg-amber-950/60 border-amber-300 dark:border-amber-700 text-amber-700 dark:text-amber-300 shadow-xs font-semibold"
                          : "border-slate-200 dark:border-slate-800 text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800"
                      )}
                    >
                      <span>👶 Дети (Kinder)</span>
                    </button>
                  </div>
                </div>
              </div>

              {/* Mixed values notice */}
              {mixedAttrs.size > 0 && (
                <div className="flex items-start gap-2 px-3 py-2.5 rounded-xl bg-amber-50 dark:bg-amber-950/30 border border-amber-200/80 dark:border-amber-800/60">
                  <AlertCircle className="w-3.5 h-3.5 text-amber-500 shrink-0 mt-0.5" />
                  <p className="text-xs text-amber-700 dark:text-amber-300">
                    <span className="font-semibold">{mixedAttrs.size} attribute(s)</span> have different values across products (shown as blank). Setting a value will overwrite all.
                  </p>
                </div>
              )}

              {/* Required attributes */}
              {requiredAttrs.length > 0 && (
                <div className="space-y-4">
                  <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
                    Required attributes ({requiredAttrs.length})
                  </p>
                  {requiredAttrs.map((attr) => (
                    <AttrField
                      key={attr.name}
                      attr={attr}
                      value={attrs[attr.name] ?? ""}
                      isMixed={mixedAttrs.has(attr.name)}
                      onChange={handleAttrChange}
                    />
                  ))}
                </div>
              )}

              {/* Optional attributes */}
              {optionalAttrs.length > 0 && (
                <div className="space-y-3">
                  <button
                    type="button"
                    onClick={() => setShowOptional((v) => !v)}
                    className="flex items-center gap-2 text-[10px] font-semibold text-slate-400 uppercase tracking-wider hover:text-slate-600 dark:hover:text-slate-300 transition-colors w-full"
                  >
                    <span className="flex-1 border-t border-dashed border-slate-200 dark:border-slate-700/50" />
                    Optional attributes ({optionalAttrs.length})
                    {showOptional ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                  </button>
                  {showOptional && (
                    <div className="space-y-4">
                      {optionalAttrs.map((attr) => (
                        <AttrField
                          key={attr.name}
                          attr={attr}
                          value={attrs[attr.name] ?? ""}
                          isMixed={mixedAttrs.has(attr.name)}
                          onChange={handleAttrChange}
                          dimmed
                        />
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* ── Остальные атрибуты (Non-shared / specific category attributes) ── */}
              {otherAttrs.length > 0 && (
                <div className="space-y-3 pt-2">
                  <button
                    type="button"
                    onClick={() => setShowOther((v) => !v)}
                    className={cn(
                      "flex items-center justify-between w-full px-3.5 py-2.5 rounded-xl border transition-all text-left",
                      showOther
                        ? "bg-slate-100/90 dark:bg-slate-800/80 border-slate-300 dark:border-slate-700 shadow-xs"
                        : "bg-slate-50/70 dark:bg-slate-900/40 border-slate-200/80 dark:border-slate-800 hover:bg-slate-100/70 dark:hover:bg-slate-800/50"
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <Layers className="w-3.5 h-3.5 text-indigo-500 dark:text-indigo-400 shrink-0" />
                      <span className="text-xs font-semibold text-slate-800 dark:text-slate-200">
                        Остальные атрибуты
                      </span>
                      <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-indigo-50 dark:bg-indigo-950/60 text-indigo-700 dark:text-indigo-300 border border-indigo-200/60 dark:border-indigo-800/60">
                        {otherAttrs.length}
                      </span>
                    </div>
                    <div className="flex items-center gap-1 text-slate-400">
                      <span className="text-[11px] font-medium">{showOther ? "Скрыть" : "Показать"}</span>
                      {showOther ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                    </div>
                  </button>

                  {showOther && (
                    <div className="space-y-3.5 p-3 rounded-xl border border-slate-100 dark:border-slate-800/80 bg-slate-50/40 dark:bg-slate-900/20">
                      <p className="text-[11px] text-slate-400 leading-normal">
                        Атрибуты, специфичные для отдельных категорий выбранных товаров. При сохранении применятся только к соответствующим товарам.
                      </p>

                      {otherAttrs.length > 5 && (
                        <div className="relative">
                          <Search className="w-3.5 h-3.5 text-slate-400 absolute left-2.5 top-1/2 -translate-y-1/2" />
                          <input
                            type="text"
                            placeholder="Поиск по остальным атрибутам..."
                            value={otherSearchQuery}
                            onChange={(e) => setOtherSearchQuery(e.target.value)}
                            className="w-full text-xs pl-8 pr-3 py-1.5 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-950 text-slate-800 dark:text-slate-200 placeholder:text-slate-400 focus:outline-hidden focus:ring-1 focus:ring-indigo-500"
                          />
                        </div>
                      )}

                      <div className="space-y-4">
                        {filteredOtherAttrs.map((attr) => (
                          <AttrField
                            key={attr.name}
                            attr={attr}
                            value={attrs[attr.name] ?? ""}
                            isMixed={mixedAttrs.has(attr.name)}
                            onChange={handleAttrChange}
                            categoryBadge={
                              attr.applicable_categories && attr.applicable_categories.length > 0
                                ? `${attr.applicable_categories.join(", ")} (${attr.applicable_eans_count ?? "?"} тов.)`
                                : undefined
                            }
                          />
                        ))}
                        {filteredOtherAttrs.length === 0 && (
                          <p className="text-xs text-slate-400 text-center py-2">Ничего не найдено</p>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        {!loading && hasAnyAttrs && (
          <div className="p-4 border-t border-slate-100 dark:border-slate-800 bg-white dark:bg-[#0E131F] shrink-0">
            <div className="flex items-center gap-2.5">
              <Button
                onClick={handleSave}
                disabled={saving}
                className="flex-1 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold h-10 gap-2 shadow-xs"
              >
                {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                {saving ? "Saving..." : `Apply to ${schema.mapped_eans} products`}
              </Button>
              <Button
                variant="outline"
                onClick={onClose}
                className="h-10 px-4 border-slate-200 dark:border-slate-800 text-slate-600 dark:text-slate-400"
              >
                Cancel
              </Button>
            </div>
            <p className="text-[10px] text-center text-slate-400 mt-2">
              Only filled fields will be saved. Blank fields are ignored.
            </p>
          </div>
        )}
      </div>
    </>
  );
}

function AttrField({
  attr,
  value,
  isMixed,
  onChange,
  dimmed,
  categoryBadge,
}: {
  attr: SharedAttr;
  value: string;
  isMixed: boolean;
  onChange: (name: string, val: string) => void;
  dimmed?: boolean;
  categoryBadge?: string;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-1.5">
        <label className={cn(
          "text-xs font-medium flex items-center gap-1.5 truncate",
          dimmed ? "text-slate-500 dark:text-slate-400" : "text-slate-700 dark:text-slate-300"
        )}>
          <span className="truncate">{attr.name}</span>
          {attr.required && <span className="text-red-500 text-[10px]" title="Required">★</span>}
        </label>
        <div className="flex items-center gap-1.5 shrink-0 ml-auto">
          {categoryBadge && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 border border-slate-200/60 dark:border-slate-700/60">
              {categoryBadge}
            </span>
          )}
          {value && !isMixed && <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />}
          {isMixed && !value && (
            <span className="text-[10px] font-normal text-amber-500 italic">mixed</span>
          )}
        </div>
      </div>
      <AttributeCombobox
        name={attr.name}
        value={value}
        options={attr.options ?? undefined}
        placeholder={isMixed ? "Different values — type to overwrite all" : "Leave blank to skip"}
        onChange={(v) => onChange(attr.name, v)}
        dimmed={dimmed}
      />
    </div>
  );
}
