import type { RestartAccount } from "@/features/api/types";
import { MOCK_FACTORIES, MOCK_CATALOG_PRODUCTS } from "./mock-data";
import type {
  AfterbuyFactoryItem,
  CatalogEanLinkRecord,
  CatalogProductItem,
  FactoryCompletionHistoryItem,
  FactoryCompletionStatus,
  FactoryMetrics,
  FactoryProductsMetrics,
} from "./types";

function computeEan13CheckDigit(twelveDigits: string): string {
  let sum = 0;
  for (let i = 0; i < 12; i++) {
    const digit = parseInt(twelveDigits[i], 10) || 0;
    sum += i % 2 === 0 ? digit : digit * 3;
  }
  const mod = sum % 10;
  return String(mod === 0 ? 0 : 10 - mod);
}

function generateValidEan13(account: RestartAccount, sequenceNumber: number): string {
  const prefix = account === "JV" ? "4260991" : "4260992";
  const body = String(sequenceNumber).padStart(5, "0").slice(-5);
  const twelve = `${prefix}${body}`;
  return `${twelve}${computeEan13CheckDigit(twelve)}`;
}

// Pricing rule: JV -10 EUR (1000 cents), XL -15 EUR (1500 cents)
export function calculateRestartPriceCents(oldPriceCents: number, account: RestartAccount): number {
  const discountCents = account === "JV" ? 1000 : 1500;
  return Math.max(0, oldPriceCents - discountCents);
}

export function formatPriceEur(cents: number): string {
  const euros = cents / 100;
  return euros.toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " €";
}

// Single Source of Truth for Computing Product Status in OTTO Restart
export function getProductComputedStatus(
  item: CatalogProductItem
): "READY" | "3M" | "12M" | "ERROR" | "PUBLISHED" {
  // 1. Published: ONLY if explicitly marked as PUBLISHED in Restart
  if (item.status === "PUBLISHED") {
    return "PUBLISHED";
  }

  const hasCategory = Boolean(
    item.category &&
      item.category.trim() !== "" &&
      item.category !== "Без категории" &&
      item.category !== "None"
  );
  const attrCount = Object.keys(item.attributes || {}).length;
  const isTitleOver70 = item.title.trim().length > 70;

  // 2. Error logic (OttoFlow parity):
  // If item is explicitly marked ERROR, or missing mandatory category, or missing attributes,
  // or title exceeds 70 characters (which OTTO strictly rejects with error 602016)
  if (
    item.status === "ERROR" ||
    item.status === "NO_CATEGORY" ||
    !hasCategory ||
    attrCount === 0 ||
    isTitleOver70
  ) {
    return "ERROR";
  }

  // 3. Ready logic:
  if (item.status === "READY") {
    return "READY";
  }

  // 4. Cohort statuses (3M / 12M candidate initial states)
  if (item.status === "3M") {
    return "3M";
  }
  if (item.status === "12M") {
    return "12M";
  }

  // 5. Default readiness: valid category, attributes filled, title <= 70 chars
  if (hasCategory && attrCount > 0 && !isTitleOver70) {
    return "READY";
  }

  // 6. Fallback to period
  return item.period === "12M" ? "12M" : "3M";
}

export class CatalogDemoService {
  private factories: AfterbuyFactoryItem[] = [...MOCK_FACTORIES];
  private products: CatalogProductItem[] = [...MOCK_CATALOG_PRODUCTS];
  private eanLinks: CatalogEanLinkRecord[] = [];
  private eanCounter = 10420;

  calculateRestartPriceCents(oldPriceCents: number, account: RestartAccount): number {
    return calculateRestartPriceCents(oldPriceCents, account);
  }

  formatPriceEur(cents: number): string {
    return formatPriceEur(cents);
  }

  getFactories(account: RestartAccount, search = "", cachedOnly = false): {
    items: AfterbuyFactoryItem[];
    metrics: FactoryMetrics;
  } {
    let items = this.factories.filter((f) => f.account === account);

    if (search.trim()) {
      const q = search.trim().toLowerCase();
      items = items.filter((f) => f.name.toLowerCase().includes(q) || f.id.includes(q));
    }

    if (cachedOnly) {
      items = items.filter((f) => f.isCached);
    }

    const allForAccount = this.factories.filter((f) => f.account === account);
    const metrics: FactoryMetrics = {
      totalCandidates: allForAccount.reduce((sum, f) => sum + f.totalCandidates, 0),
      totalFactories: allForAccount.length,
      count3m: allForAccount.reduce((sum, f) => sum + f.candidatesCount3m, 0),
      count12m: allForAccount.reduce((sum, f) => sum + f.candidatesCount12m, 0),
      cachedCount: allForAccount.filter((f) => f.isCached).length,
    };

    return { items, metrics };
  }

  async getFactoriesAsync(
    account: RestartAccount,
    search = "",
    statusFilter = "ALL",
    sortByNew = false
  ): Promise<{
    items: AfterbuyFactoryItem[];
    metrics: FactoryMetrics & {
      completeCount: number;
      notCompleteCount: number;
      unknownCount: number;
      totalNewCandidates: number;
    };
  }> {
    try {
      const url = `/restart/api/backend/factories?account=${account}&status=${encodeURIComponent(
        statusFilter
      )}&sort_by_new=${sortByNew ? "true" : "false"}`;
      const res = await fetch(url, { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        const backendItems: AfterbuyFactoryItem[] = (data.items || []).map((it: any) => ({
          id: String(it.id),
          name: it.name || `Фабрика ${it.id}`,
          account: it.account || account,
          totalProductsCatalog: it.totalCandidates || 0,
          candidatesCount3m: it.period3mCount || 0,
          candidatesCount12m: it.period12mCount || 0,
          totalCandidates: it.totalCandidates || 0,
          readyCount: it.readyCount || 0,
          needsAiCount: 0,
          errorCount: it.errorCount || 0,
          existsInOttoCount: it.publishedCount || 0,
          isCached: true,
          lastUpdated: it.lastCompletedAt || it.lastScannedAt || "2026-10-30",
          status: (it.status as FactoryCompletionStatus) || "UNKNOWN",
          newCandidatesCount: it.newCandidatesCount || 0,
          firstCompletedAt: it.firstCompletedAt,
          lastCompletedAt: it.lastCompletedAt,
          lastScannedAt: it.lastScannedAt,
          wasPreviouslyCompleted: Boolean(it.wasPreviouslyCompleted),
        }));

        let filtered = backendItems;
        if (search.trim()) {
          const q = search.trim().toLowerCase();
          filtered = filtered.filter(
            (f) => f.name.toLowerCase().includes(q) || f.id.includes(q)
          );
        }

        return {
          items: filtered,
          metrics: {
            totalCandidates: backendItems.reduce((sum, f) => sum + f.totalCandidates, 0),
            totalFactories: backendItems.length,
            count3m: backendItems.reduce((sum, f) => sum + f.candidatesCount3m, 0),
            count12m: backendItems.reduce((sum, f) => sum + f.candidatesCount12m, 0),
            cachedCount: backendItems.length,
            completeCount: data.completeCount || 0,
            notCompleteCount: data.notCompleteCount || 0,
            unknownCount: data.unknownCount || 0,
            totalNewCandidates: data.totalNewCandidates || 0,
          },
        };
      }
    } catch {
      // Graceful fallback to local mock data if backend unavailable
    }

    const fallback = this.getFactories(account, search);
    return {
      items: fallback.items,
      metrics: {
        ...fallback.metrics,
        completeCount: 0,
        notCompleteCount: fallback.items.length,
        unknownCount: 0,
        totalNewCandidates: 0,
      },
    };
  }

  async getFactoryCompletionHistoryAsync(
    account: RestartAccount,
    factoryId: string
  ): Promise<FactoryCompletionHistoryItem[]> {
    try {
      const res = await fetch(
        `/restart/api/backend/factories/${factoryId}/history?account=${account}`,
        { cache: "no-store" }
      );
      if (res.ok) {
        const data = await res.json();
        return data.items || [];
      }
    } catch {
      // Ignore errors
    }
    return [];
  }

  async completeFactoryAsync(
    account: RestartAccount,
    factoryId: string
  ): Promise<{ success: boolean; message?: string }> {
    try {
      const res = await fetch(
        `/restart/api/backend/factories/${factoryId}/complete?account=${account}`,
        { method: "POST", cache: "no-store" }
      );
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.success) {
        return { success: true };
      }
      return { success: false, message: data.detail || "Не удалось завершить фабрику" };
    } catch (err: any) {
      return { success: false, message: err?.message || "Ошибка сети" };
    }
  }

  async pasteImportContentAsync(
    account: RestartAccount,
    rawText: string,
    filename = "manual_import.txt",
    dryRun = false
  ): Promise<any> {
    const res = await fetch("/restart/api/backend/imports/paste", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        account,
        raw_text: rawText,
        filename,
        dry_run: dryRun,
      }),
    });
    return res.json();
  }

  async uploadImportFileAsync(
    account: RestartAccount,
    file: File,
    dryRun = false
  ): Promise<any> {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch(
      `/restart/api/backend/imports/upload?account=${account}&dry_run=${dryRun ? "true" : "false"}`,
      {
        method: "POST",
        body: formData,
      }
    );
    return res.json();
  }

  getFactoryById(factoryId: string, account: RestartAccount): AfterbuyFactoryItem | undefined {
    return this.factories.find((f) => f.id === factoryId && f.account === account);
  }

  getProductsByFactory(factoryId: string, account: RestartAccount, search = "", periodFilter = "", statusFilter = ""): {
    items: CatalogProductItem[];
    metrics: FactoryProductsMetrics;
  } {
    // If factory has dedicated products in mock, use them; otherwise synthesize realistic items for demo
    let factoryProducts = this.products.filter(
      (p) => p.factoryId === factoryId && p.account === account
    );

    const factory = this.getFactoryById(factoryId, account);

    // If it's one of the other factories without hardcoded items, synthesize realistic items
    if (factoryProducts.length === 0 && factory) {
      const count = Math.min(factory.totalCandidates, 12);
      factoryProducts = Array.from({ length: count }, (_, idx) => {
        const is3m = idx % 2 === 0;
        const eanNum = 4071489000000 + parseInt(factoryId, 10) * 100 + idx;
        const baseTitles = [
          "Design Ecksofa mit Schlaffunktion Bettkasten Modern Lounge Wohnzimmer Polster",
          "Boxspringbett 180x200 Anthrazit mit Matratze Topper LED Kopfteil Schlafzimmer",
          "Modern Lounge Sessel Drehbar Polster Braun Relaxsessel Wohnzimmer Komfort",
          "Chesterfield 3-Sitzer Sofa Echtleder Cognac Braun Knopfheftung Klassisch",
          "Massivholz Esstisch Eiche 200x100 Metallkufen Baumkante Esszimmer Modern",
        ];
        const title = baseTitles[idx % baseTitles.length];
        return {
          id: `synth-${factoryId}-${idx}`,
          sourceType: "AFTERBUY" as const,
          factoryId,
          factoryName: factory.name,
          account,
          ean: String(eanNum),
          sku: String(eanNum),
          title,
          imageUrl: MOCK_CATALOG_PRODUCTS[idx % MOCK_CATALOG_PRODUCTS.length].imageUrl,
          category: idx % 3 === 0 ? "Sessel" : idx % 3 === 1 ? "Polstergarnitur" : "Bett",
          oldStandardPriceCents: 99999 + idx * 25000,
          period: is3m ? ("3M" as const) : ("12M" as const),
          errorCode: is3m ? ("602016 - Nonseller" as const) : ("602016 - LOW_PERFORMER" as const),
          status: title.length > 70 ? ("ERROR" as const) : ("READY" as const),
          existsInOtto: true,
          attributes: { material: "Stoff / Holz", color: "Grau", width: "200 cm", height: "85 cm", depth: "90 cm" },
        };
      });
      this.products.push(...factoryProducts);
    }

    let filtered = [...factoryProducts];
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      filtered = filtered.filter((p) => p.title.toLowerCase().includes(q) || p.ean.includes(q));
    }
    if (periodFilter) {
      filtered = filtered.filter((p) => p.period === periodFilter);
    }
    if (statusFilter) {
      if (statusFilter === "3M" || statusFilter === "12M") {
        filtered = filtered.filter((p) => p.period === statusFilter);
      } else {
        filtered = filtered.filter((p) => getProductComputedStatus(p) === statusFilter);
      }
    }

    const metrics: FactoryProductsMetrics = {
      total: factoryProducts.length,
      ready: factoryProducts.filter((p) => getProductComputedStatus(p) === "READY").length,
      errors: factoryProducts.filter((p) => getProductComputedStatus(p) === "ERROR").length,
      published: factoryProducts.filter((p) => getProductComputedStatus(p) === "PUBLISHED").length,
      count3m: factoryProducts.filter((p) => p.period === "3M").length,
      count12m: factoryProducts.filter((p) => p.period === "12M").length,
      needsAi: factoryProducts.filter((p) => p.title.length > 70).length,
      noCategory: factoryProducts.filter((p) => !p.category || p.category === "Без категории").length,
      publishedOrExists: factoryProducts.filter((p) => p.existsInOtto).length,
    };

    return { items: filtered, metrics };
  }

  getDirectOttoProducts(account: RestartAccount, search = "", periodFilter = "", categoryFilter = "", statusFilter = ""): {
    items: CatalogProductItem[];
    metrics: FactoryProductsMetrics;
  } {
    let directItems = this.products.filter(
      (p) => p.sourceType === "DIRECT_OTTO" && p.account === account
    );

    if (search.trim()) {
      const q = search.trim().toLowerCase();
      directItems = directItems.filter((p) => p.title.toLowerCase().includes(q) || p.ean.includes(q));
    }
    if (periodFilter) {
      directItems = directItems.filter((p) => p.period === periodFilter);
    }
    if (categoryFilter) {
      directItems = directItems.filter((p) => p.category === categoryFilter);
    }
    if (statusFilter) {
      if (statusFilter === "3M" || statusFilter === "12M") {
        directItems = directItems.filter((p) => p.period === statusFilter);
      } else {
        directItems = directItems.filter((p) => getProductComputedStatus(p) === statusFilter);
      }
    }

    const allDirect = this.products.filter((p) => p.sourceType === "DIRECT_OTTO" && p.account === account);
    const metrics: FactoryProductsMetrics = {
      total: allDirect.length,
      ready: allDirect.filter((p) => getProductComputedStatus(p) === "READY").length,
      errors: allDirect.filter((p) => getProductComputedStatus(p) === "ERROR").length,
      published: allDirect.filter((p) => getProductComputedStatus(p) === "PUBLISHED").length,
      count3m: allDirect.filter((p) => p.period === "3M").length,
      count12m: allDirect.filter((p) => p.period === "12M").length,
      needsAi: allDirect.filter((p) => p.title.length > 70).length,
      noCategory: allDirect.filter((p) => !p.category || p.category === "Без категории").length,
      publishedOrExists: allDirect.filter((p) => p.existsInOtto).length,
    };

    return { items: directItems, metrics };
  }

  // Interactive action: Assign new EANs to selected products and record Aftercool EAN -> New OTTO EAN links
  async assignNewEansAsync(productIds: string[]): Promise<number> {
    const targetProducts = this.products.filter((p) => productIds.includes(p.id));
    if (targetProducts.length === 0) return 0;

    const account = targetProducts[0].account;
    const itemsPayload = targetProducts.map((p) => ({
      source_type: p.factoryId ? "AFTERCOOL" : "OTTO_DIRECT",
      aftercool_product_id: p.id,
      aftercool_sku: p.ean,
      aftercool_ean: p.sourceEan || p.ean,
      factory_id: p.factoryId || null,
      factory_name: p.factoryName || null,
      title: p.title,
      price_cents: p.oldStandardPriceCents,
      category: p.category,
      period: p.period || "3M",
    }));

    try {
      const res = await fetch("/restart/api/backend/library/assign-ean", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account, items: itemsPayload }),
      });

      if (res.ok) {
        const data = await res.json();
        const assignedMap = new Map<string, { newEan: string; linkId: string }>();
        for (const item of data.assigned || []) {
          assignedMap.set(item.sourceEan, { newEan: item.newEan, linkId: item.linkId });
        }

        const nowIso = new Date().toISOString();
        this.products = this.products.map((p) => {
          const originalEan = p.sourceEan || p.ean;
          const assigned = assignedMap.get(originalEan);
          if (assigned) {
            const newEan = assigned.newEan;
            const linkRecord: CatalogEanLinkRecord = {
              id: assigned.linkId,
              productId: p.id,
              account: p.account,
              factoryId: p.factoryId,
              factoryName: p.factoryName,
              title: p.title,
              sourceEan: originalEan,
              newEan,
              period: p.period,
              assignedAt: nowIso,
            };
            this.eanLinks = [linkRecord, ...this.eanLinks.filter((item) => item.productId !== p.id)];
            return {
              ...p,
              sourceEan: originalEan,
              ean: newEan,
              newEan,
              hasNewEan: true,
              eanAssignedAt: nowIso,
            };
          }
          return p;
        });

        return data.count || targetProducts.length;
      }
    } catch (err) {
      console.warn("[catalog-service] Backend assign-ean failed, falling back to local:", err);
    }

    return this.assignNewEans(productIds);
  }

  // Synchronous fallback
  assignNewEans(productIds: string[]): number {
    let updatedCount = 0;
    const nowIso = new Date().toISOString();

    this.products = this.products.map((p) => {
      if (productIds.includes(p.id)) {
        updatedCount++;
        this.eanCounter += 1;
        const originalEan = p.sourceEan || p.ean;
        const newEan = generateValidEan13(p.account, this.eanCounter);

        const linkRecord: CatalogEanLinkRecord = {
          id: `EAN-LINK-${this.eanCounter}`,
          productId: p.id,
          account: p.account,
          factoryId: p.factoryId,
          factoryName: p.factoryName,
          title: p.title,
          sourceEan: originalEan,
          newEan,
          period: p.period,
          assignedAt: nowIso,
        };

        this.eanLinks = [
          linkRecord,
          ...this.eanLinks.filter((item) => item.productId !== p.id),
        ];

        return {
          ...p,
          sourceEan: originalEan,
          ean: newEan,
          newEan,
          hasNewEan: true,
          eanAssignedAt: nowIso,
        };
      }
      return p;
    });

    return updatedCount;
  }

  getEanLinks(account: RestartAccount, query = ""): CatalogEanLinkRecord[] {
    let list = this.eanLinks.filter((item) => item.account === account);
    if (query.trim()) {
      const q = query.trim().toLowerCase();
      list = list.filter(
        (item) =>
          item.sourceEan.toLowerCase().includes(q) ||
          item.newEan.toLowerCase().includes(q) ||
          item.title.toLowerCase().includes(q) ||
          (item.factoryName || "").toLowerCase().includes(q)
      );
    }
    return list;
  }

  // Interactive demo action: Apply AI Title rewriting to <= 70 chars
  applyAiTitles(productIds: string[]): number {
    let updatedCount = 0;
    this.products = this.products.map((p) => {
      if (productIds.includes(p.id)) {
        updatedCount++;
        const newTitle = p.aiSuggestedTitle || p.title.slice(0, 68).trim();
        return {
          ...p,
          title: newTitle,
          status: "READY",
          hasAppliedAiTitle: true,
        };
      }
      return p;
    });
    return updatedCount;
  }

  // Interactive action: Apply AI Attributes for selected products
  applyAiAttributes(productIds: string[]): number {
    let updatedCount = 0;
    this.products = this.products.map((p) => {
      if (productIds.includes(p.id)) {
        updatedCount++;
        const titleLower = p.title.toLowerCase();
        let inferredMaterial = "Stoff / Massivholz";
        let inferredColor = "Anthrazit";
        let width = p.attributes.width || "200 cm";
        let height = p.attributes.height || "85 cm";
        let depth = p.attributes.depth || "90 cm";

        if (titleLower.includes("leder") || titleLower.includes("chesterfield")) {
          inferredMaterial = "Echtleder / Hartholzgestell";
          inferredColor = titleLower.includes("cognac") ? "Cognac Braun" : "Schwarz";
        } else if (titleLower.includes("eiche") || titleLower.includes("holz")) {
          inferredMaterial = "Massivholz Eiche geölt / Stahlkufen";
          inferredColor = "Wildeiche Natur";
          width = "200 cm";
          height = "76 cm";
          depth = "100 cm";
        } else if (titleLower.includes("bett") || titleLower.includes("boxspring")) {
          inferredMaterial = "Strukturstoff Melange / Massivholz";
          inferredColor = titleLower.includes("anthrazit") ? "Anthrazit" : "Hellgrau";
          width = "180 cm";
          height = "115 cm";
          depth = "208 cm";
        } else if (titleLower.includes("sessel") || titleLower.includes("lounge")) {
          inferredMaterial = "Polsterstoff Premium / Metallgestell";
          inferredColor = titleLower.includes("braun") ? "Warmbraun" : "Dunkelgrau";
          width = "84 cm";
          height = "98 cm";
          depth = "86 cm";
        }

        const newAttributes = {
          ...p.attributes,
          material: inferredMaterial,
          color: inferredColor,
          width,
          height,
          depth,
          "KI-Generiert": "Ja (GPT-4 Vision & Text)",
        };

        return {
          ...p,
          attributes: newAttributes,
          hasAiAttributes: true,
          status: (p.category && p.category !== "Без категории" ? "READY" : p.status) as typeof p.status,
        };
      }
      return p;
    });
    return updatedCount;
  }

  // Interactive action: Apply AI Categories for selected products
  applyAiCategories(productIds: string[]): number {
    let updatedCount = 0;
    this.products = this.products.map((p) => {
      if (productIds.includes(p.id)) {
        updatedCount++;
        const titleLower = p.title.toLowerCase();
        let determinedCategory = "Polstergarnitur";

        if (titleLower.includes("sessel") || titleLower.includes("lounge")) {
          determinedCategory = "Sessel";
        } else if (titleLower.includes("bett") || titleLower.includes("boxspring")) {
          determinedCategory = "Betten & Zubehör";
        } else if (titleLower.includes("esstisch") || titleLower.includes("tisch")) {
          determinedCategory = "Esstische";
        } else if (titleLower.includes("stuhl") || titleLower.includes("stühle")) {
          determinedCategory = "Stühle";
        } else if (titleLower.includes("sofa") || titleLower.includes("couch") || titleLower.includes("ecksofa")) {
          determinedCategory = "Polstergarnitur";
        } else if (titleLower.includes("schrank") || titleLower.includes("sideboard")) {
          determinedCategory = "Kommoden & Sideboards";
        }

        return {
          ...p,
          category: determinedCategory,
          hasAiCategory: true,
          status: "READY" as typeof p.status,
        };
      }
      return p;
    });
    return updatedCount;
  }

  // Core feature: Sync Categories + Attributes from existing OTTO catalog data
  syncFromOtto(productIds: string[]): number {
    let updatedCount = 0;
    const nowIso = new Date().toISOString();

    this.products = this.products.map((p) => {
      if (productIds.includes(p.id)) {
        updatedCount++;
        const titleLower = p.title.toLowerCase();
        const brand = p.account === "JV" ? "BEMENTE HOME" : "ROPEZ PREMIUM";

        let ottoCategory = "Möbel > Wohnzimmer > Polstermöbel > Sofas & Couches";
        let ottoAttributes: Record<string, string> = {
          Marke: brand,
          Material: "Premium Strukturstoff (100% Polyester) / Massivholz",
          Farbe: "Anthrazit Melange",
          Breite: p.attributes.width || "210 cm",
          Höhe: p.attributes.height || "88 cm",
          Tiefe: p.attributes.depth || "95 cm",
          Lieferzustand: "Teilmontiert, einfache Endmontage",
          Herkunftsland: "Türkei / EU",
          Garantie: "24 Monate Herstellergarantie",
          Qualitätsstandard: "Geprüfte OTTO Partner-Qualität",
          "FSC-Zertifizierung": "Ja (FSC® C123456)",
        };

        if (titleLower.includes("sessel") || titleLower.includes("lounge")) {
          ottoCategory = "Möbel > Wohnzimmer > Polstermöbel > Sessel & Relaxsessel";
          ottoAttributes = {
            Marke: brand,
            Material: "Echtleder / Metallgestell pulverbeschichtet",
            Farbe: "Dunkelbraun / Cognac",
            Breite: "85 cm",
            Höhe: "98 cm",
            Tiefe: "86 cm",
            Sitzhöhe: "45 cm",
            Drehbar: "360° Drehbar",
            Lieferzustand: "Zerlegt im Karton",
            Garantie: "24 Monate",
            Qualitätsstandard: "Geprüfte OTTO Partner-Qualität",
          };
        } else if (titleLower.includes("bett") || titleLower.includes("boxspring")) {
          ottoCategory = "Möbel > Schlafzimmer > Betten > Boxspringbetten";
          ottoAttributes = {
            Marke: brand,
            Material: "Webstoff Anthrazit / Massivholzrahmen",
            Farbe: "Anthrazit Dunkelgrau",
            Breite: "180 cm",
            Höhe: "118 cm",
            Tiefe: "210 cm",
            Liegefläche: "180 x 200 cm",
            Härtegrad: "H3 (bis 110 kg je Seite)",
            Matratzenart: "7-Zonen-Taschenfederkern",
            Lieferzustand: "Zerlegt, 3 Packstücke",
            Garantie: "24 Monate",
            Qualitätsstandard: "Geprüfte OTTO Partner-Qualität",
          };
        } else if (titleLower.includes("esstisch") || titleLower.includes("tisch")) {
          ottoCategory = "Möbel > Esszimmer > Tische > Esstische";
          ottoAttributes = {
            Marke: brand,
            Material: "Massivholz Eiche geölt / Stahlkufen schwarz",
            Farbe: "Wildeiche Natur",
            Breite: "200 cm",
            Höhe: "76 cm",
            Tiefe: "100 cm",
            Tischplattenstärke: "40 mm aufgedoppelt",
            Baumkante: "Mit natürlicher Baumkante",
            Lieferzustand: "Teilmontiert (Beine montieren)",
            Garantie: "24 Monate",
            Qualitätsstandard: "Geprüfte OTTO Partner-Qualität",
          };
        }

        return {
          ...p,
          category: ottoCategory,
          attributes: {
            ...p.attributes,
            ...ottoAttributes,
          },
          isSyncedWithOtto: true,
          hasAiCategory: false,
          hasAiAttributes: false,
          status: "READY" as typeof p.status,
          existsInOtto: true,
          syncedAt: nowIso,
        };
      }
      return p;
    });
    return updatedCount;
  }

  // Update product title (from inline edit or drawer)
  updateProductTitle(productId: string, newTitle: string): boolean {
    let found = false;
    this.products = this.products.map((p) => {
      if (p.id === productId) {
        found = true;
        const isOver70 = newTitle.trim().length > 70;
        const hasCat = Boolean(p.category && p.category.trim() !== "" && p.category !== "Без категории" && p.category !== "None");
        const hasAttrs = Object.keys(p.attributes || {}).length > 0;
        const newStatus = isOver70 ? "ERROR" : (hasCat && hasAttrs ? "READY" : p.status);
        return {
          ...p,
          title: newTitle,
          status: newStatus as typeof p.status,
        };
      }
      return p;
    });
    return found;
  }

  // Update product price (from inline edit or drawer)
  updateProductPrice(productId: string, newOldPriceCents: number): boolean {
    let found = false;
    this.products = this.products.map((p) => {
      if (p.id === productId) {
        found = true;
        return {
          ...p,
          oldStandardPriceCents: newOldPriceCents,
        };
      }
      return p;
    });
    return found;
  }

  // Update product category (from inline combobox or drawer)
  updateProductCategory(productId: string, newCategory: string): boolean {
    let found = false;
    this.products = this.products.map((p) => {
      if (p.id === productId) {
        found = true;
        const hasCat = Boolean(newCategory && newCategory.trim() !== "" && newCategory !== "Без категории" && newCategory !== "None");
        const isOver70 = p.title.trim().length > 70;
        const hasAttrs = Object.keys(p.attributes || {}).length > 0;
        const newStatus = (!hasCat || isOver70) ? "ERROR" : (hasAttrs ? "READY" : p.status);
        return {
          ...p,
          category: newCategory,
          status: newStatus as typeof p.status,
        };
      }
      return p;
    });
    return found;
  }

  // Full product update from drawer
  updateProduct(updated: CatalogProductItem): boolean {
    let found = false;
    this.products = this.products.map((p) => {
      if (p.id === updated.id) {
        found = true;
        return {
          ...p,
          ...updated,
        };
      }
      return p;
    });
    return found;
  }
}

export const catalogDemoService = new CatalogDemoService();
