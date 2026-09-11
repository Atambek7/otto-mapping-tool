"use client";

import { useState, useMemo } from "react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Check, ChevronDown, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";

// ── Otto category tree ────────────────────────────────────────────────────
export const OTTO_CATEGORIES: { group: string; items: string[] }[] = [
  {
    group: "Wohnzimmer",
    items: [
      "Wohnzimmer > Sofas & Couches",
      "Wohnzimmer > Sessel",
      "Wohnzimmer > Couchtische",
      "Wohnzimmer > Sideboards",
      "Wohnzimmer > Kommoden",
      "Wohnzimmer > TV-Möbel & Mediamöbel",
      "Wohnzimmer > Regale & Wandregale",
      "Wohnzimmer > Wohnwände",
      "Wohnzimmer > Wohnzimmer-Sets",
      "Wohnzimmer > Vitrinen",
      "Wohnzimmer > Beistelltische",
      "Wohnzimmer > Hocker & Poufs",
    ],
  },
  {
    group: "Schlafzimmer",
    items: [
      "Schlafzimmer > Betten",
      "Schlafzimmer > Kleiderschränke",
      "Schlafzimmer > Kommoden & Schubladen",
      "Schlafzimmer > Nachttische",
      "Schlafzimmer > Schlafzimmer-Sets",
      "Schlafzimmer > Schminktische",
      "Schlafzimmer > Bettwäsche",
      "Schlafzimmer > Matratzen",
    ],
  },
  {
    group: "Esszimmer",
    items: [
      "Esszimmer > Esstische",
      "Esszimmer > Essstühle",
      "Esszimmer > Esszimmer-Sets",
      "Esszimmer > Anrichten & Buffets",
      "Esszimmer > Bänke",
      "Esszimmer > Barhocker & Bartische",
    ],
  },
  {
    group: "Büro & Arbeitszimmer",
    items: [
      "Büro > Schreibtische",
      "Büro > Bürostühle & Chefsessel",
      "Büro > Büroregale",
      "Büro > Rollcontainer",
      "Büro > Aktenschränke",
      "Büro > Computermöbel-Sets",
    ],
  },
  {
    group: "Kinderzimmer",
    items: [
      "Kinderzimmer > Kinderbetten",
      "Kinderzimmer > Hochbetten & Etagenbetten",
      "Kinderzimmer > Kinderschränke",
      "Kinderzimmer > Kinderschreibtische",
      "Kinderzimmer > Kinderzimmer-Sets",
      "Kinderzimmer > Spielzeug-Aufbewahrung",
    ],
  },
  {
    group: "Badezimmer",
    items: [
      "Bad > Waschtische & Waschbeckenunterschränke",
      "Bad > Badezimmerschränke",
      "Bad > Spiegelschränke",
      "Bad > Badmöbel-Sets",
      "Bad > Badhocker",
    ],
  },
  {
    group: "Flur & Garderobe",
    items: [
      "Flur > Garderoben-Sets",
      "Flur > Garderobenhaken",
      "Flur > Schuhschränke",
      "Flur > Flurregale",
      "Flur > Sitzhocker",
    ],
  },
  {
    group: "Garten & Außen",
    items: [
      "Garten > Gartenmöbel-Sets",
      "Garten > Gartenstühle",
      "Garten > Gartentische",
      "Garten > Sonnenliegen",
      "Garten > Gartenbänke",
    ],
  },
];

const ALL_CATEGORIES = OTTO_CATEGORIES.flatMap((g) => g.items);

interface CategoryPickerProps {
  value: string;
  onChange: (value: string) => void;
}

export function CategoryPicker({ value, onChange }: CategoryPickerProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    if (!query.trim()) return OTTO_CATEGORIES;
    const q = query.toLowerCase();
    return OTTO_CATEGORIES
      .map((g) => ({
        ...g,
        items: g.items.filter((item) => item.toLowerCase().includes(q)),
      }))
      .filter((g) => g.items.length > 0);
  }, [query]);

  const select = (cat: string) => {
    onChange(cat === value ? "" : cat); // toggle off if same
    setOpen(false);
    setQuery("");
  };

  const clear = (e: React.MouseEvent) => {
    e.stopPropagation();
    onChange("");
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <button
            type="button"
            className={cn(
              "w-full flex items-center justify-between gap-1.5 px-2 py-1 rounded-md text-xs border transition-all duration-150 text-left",
              value
                ? "bg-indigo-50 border-indigo-300 text-indigo-800 hover:bg-indigo-100"
                : "bg-white border-slate-200 text-slate-400 hover:border-indigo-300 hover:bg-indigo-50/40"
            )}
          />
        }
      >
        <span className="truncate flex-1">
          {value || t("category_picker.placeholder", "Категория Otto...")}
        </span>
        <span className="flex items-center gap-0.5 shrink-0">
          {value && (
            <span
              role="button"
              tabIndex={0}
              onClick={clear}
              onKeyDown={(e) => e.key === "Enter" && clear(e as unknown as React.MouseEvent)}
              className="p-0.5 rounded hover:bg-indigo-200 text-indigo-400 hover:text-indigo-700"
            >
              <X className="w-3 h-3" />
            </span>
          )}
          <ChevronDown className={cn("w-3 h-3 opacity-40 transition-transform", open && "rotate-180")} />
        </span>
      </PopoverTrigger>

      <PopoverContent
        className="w-72 p-0 shadow-xl border-slate-200"
        align="start"
        side="bottom"
      >
        <Command shouldFilter={false}>
          <CommandInput
            placeholder={t("category_picker.search", "Поиск категории...")}
            value={query}
            onValueChange={setQuery}
            className="text-sm"
          />
          <CommandList className="max-h-72 overflow-y-auto">
            <CommandEmpty>{t("category_picker.not_found", "Категория не найдена")}</CommandEmpty>
            {filtered.map((group) => (
              <CommandGroup key={group.group} heading={group.group}>
                {group.items.map((cat) => (
                  <CommandItem
                    key={cat}
                    value={cat}
                    onSelect={() => select(cat)}
                    className="cursor-pointer text-xs py-1.5"
                  >
                    <Check
                      className={cn(
                        "mr-2 h-3.5 w-3.5 shrink-0 transition-opacity",
                        value === cat ? "opacity-100 text-indigo-600" : "opacity-0"
                      )}
                    />
                    {/* Show only the last part after > for readability */}
                    <span className="truncate">
                      {cat.split(" > ").pop()}
                    </span>
                  </CommandItem>
                ))}
              </CommandGroup>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
