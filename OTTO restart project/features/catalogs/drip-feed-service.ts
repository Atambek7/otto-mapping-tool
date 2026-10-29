import type { RestartAccount } from "@/features/api/types";
import { catalogDemoService, formatPriceEur } from "./service";
import type { CatalogProductItem } from "./types";

export interface DripPlan {
  id: number;
  factory_id: string;
  factory_name?: string;
  account_name: RestartAccount;
  total_products: number;
  total_days: number;
  current_day: number;
  daily_time: string;
  status: "active" | "paused" | "completed" | "cancelled";
  executor_type: string;
  created_at: string;
  progress?: {
    published: number;
    processing?: number;
    failed: number;
    pending: number;
    existing?: number;
    percentage: number;
  };
}

export interface DripQueueItem {
  id: number;
  product_ean: string;
  product_name?: string;
  image_url?: string;
  price?: string;
  category_id?: string;
  category_name?: string;
  scheduled_day: number;
  scheduled_date: string;
  status: "pending" | "processing" | "published" | "failed";
  attempts: number;
  error_message?: string;
  missing_attributes?: string[];
  published_at?: string;
  otto_product_exists?: boolean | null;
  otto_checked_at?: string;
  originalProduct?: CatalogProductItem;
}

export class DripFeedService {
  // Store plans per factory and account. Initially empty: no drip feed is running by default.
  private plansByFactory: Map<string, DripPlan[]> = new Map();
  private itemsByPlan: Map<number, DripQueueItem[]> = new Map();

  private getCacheKey(factoryId: string, account: RestartAccount): string {
    return `${account}:${factoryId}`;
  }

  /**
   * Get all plans for factory & account.
   * If none created, returns an empty array [].
   */
  getPlans(factoryId: string, account: RestartAccount): DripPlan[] {
    const key = this.getCacheKey(factoryId, account);
    return this.plansByFactory.get(key) || [];
  }

  /**
   * Get active plan if one is running or paused.
   * By default, returns undefined because no Drip Feed is launched yet.
   */
  getActivePlan(factoryId: string, account: RestartAccount): DripPlan | undefined {
    const plans = this.getPlans(factoryId, account);
    return plans.find((p) => p.status === "active" || p.status === "paused");
  }

  /**
   * Get queue items for a specific plan.
   */
  getPlanItems(planId: number): DripQueueItem[] {
    return this.itemsByPlan.get(planId) || [];
  }

  /**
   * Create a new Drip Feed plan (when triggered via modal or demo).
   */
  createPlan(
    factoryId: string,
    account: RestartAccount,
    options: {
      days?: number;
      dailyTime?: string;
      executorType?: string;
      selectedEans?: string[];
    } = {}
  ): DripPlan {
    const key = this.getCacheKey(factoryId, account);
    const existing = this.plansByFactory.get(key) || [];

    const factory = catalogDemoService.getFactoryById(factoryId, account);
    const { items: factoryProducts } = catalogDemoService.getProductsByFactory(factoryId, account);

    let targetProducts = factoryProducts;
    if (options.selectedEans && options.selectedEans.length > 0) {
      targetProducts = factoryProducts.filter((p) => options.selectedEans!.includes(p.ean));
    }

    const totalCount = targetProducts.length > 0 ? targetProducts.length : 12;
    const days = options.days || 7;
    const dailyTime = options.dailyTime || "08:00";
    const planId = Date.now();
    const now = new Date();

    const plan: DripPlan = {
      id: planId,
      factory_id: factoryId,
      factory_name: factory?.name || `Фабрика ${factoryId}`,
      account_name: account,
      total_products: totalCount,
      total_days: days,
      current_day: 1,
      daily_time: dailyTime,
      status: "active",
      executor_type: options.executorType || "otto_api",
      created_at: now.toISOString(),
      progress: {
        published: 0,
        processing: 0,
        failed: 0,
        pending: totalCount,
        existing: 0,
        percentage: 0,
      },
    };

    // Generate queue items distributed across days
    const items: DripQueueItem[] = [];
    const perDay = Math.ceil(totalCount / days);

    for (let i = 0; i < totalCount; i++) {
      const prod = targetProducts[i % targetProducts.length];
      const ean = prod?.ean || String(4071489000000 + i * 137);
      const title = prod?.title || `Möbelstück Modern Design ${i + 1}`;
      const imageUrl = prod?.imageUrl || "";
      const priceFormatted = prod
        ? formatPriceEur(catalogDemoService.calculateRestartPriceCents(prod.oldStandardPriceCents, account))
        : "899,00 €";
      const category = prod?.category || "Sessel & Relaxsessel";

      const day = Math.min(days, Math.floor(i / perDay) + 1);
      const schedDate = new Date(now.getTime() + (day - 1) * 86400000);
      schedDate.setHours(8, 0, 0, 0);

      items.push({
        id: 10000 + i,
        product_ean: ean,
        product_name: title,
        image_url: imageUrl,
        price: priceFormatted,
        category_id: category,
        category_name: category,
        scheduled_day: day,
        scheduled_date: schedDate.toISOString(),
        status: "pending",
        attempts: 0,
        originalProduct: prod,
      });
    }

    this.itemsByPlan.set(planId, items);
    this.recalculatePlanProgress(plan, items);

    const updatedPlans = [plan, ...existing];
    this.plansByFactory.set(key, updatedPlans);
    return plan;
  }

  private recalculatePlanProgress(plan: DripPlan, items: DripQueueItem[]) {
    let published = 0;
    let existing = 0;
    let processing = 0;
    let failed = 0;
    let pending = 0;

    for (const it of items) {
      if (it.status === "failed" && it.otto_product_exists) existing++;
      else if (it.status === "published") published++;
      else if (it.status === "processing") processing++;
      else if (it.status === "failed") failed++;
      else pending++;
    }

    const total = items.length || 1;
    const completedCount = published + existing;
    const percentage = Math.min(100, Math.round((completedCount / total) * 100));

    plan.progress = {
      published,
      existing,
      processing,
      failed,
      pending,
      percentage,
    };
  }

  // Trigger publication now for pending items in active plan
  triggerNow(planId: number): boolean {
    const items = this.itemsByPlan.get(planId);
    if (!items) return false;

    let modified = false;
    const nowIso = new Date().toISOString();

    for (const item of items) {
      if (item.status === "pending" && item.scheduled_day <= 1) {
        item.status = "published";
        item.published_at = nowIso;
        item.attempts += 1;
        modified = true;
      }
    }

    // Find plan and recalculate
    for (const [, plans] of this.plansByFactory.entries()) {
      const plan = plans.find((p) => p.id === planId);
      if (plan) {
        this.recalculatePlanProgress(plan, items);
        break;
      }
    }
    return modified;
  }

  // Retry failed items
  retryFailed(planId: number): boolean {
    const items = this.itemsByPlan.get(planId);
    if (!items) return false;

    let modified = false;
    for (const item of items) {
      if (item.status === "failed" && !item.otto_product_exists) {
        item.status = "processing";
        item.attempts += 1;
        item.error_message = undefined;
        item.missing_attributes = undefined;
        modified = true;
      }
    }

    for (const [, plans] of this.plansByFactory.entries()) {
      const plan = plans.find((p) => p.id === planId);
      if (plan) {
        this.recalculatePlanProgress(plan, items);
        break;
      }
    }
    return modified;
  }

  // Pause / Resume plan
  togglePlanPause(planId: number): "active" | "paused" | null {
    for (const [, plans] of this.plansByFactory.entries()) {
      const plan = plans.find((p) => p.id === planId);
      if (plan) {
        plan.status = plan.status === "active" ? "paused" : "active";
        return plan.status;
      }
    }
    return null;
  }

  // Update item data from ProductDrawer
  syncProductUpdate(product: CatalogProductItem): void {
    for (const [, items] of this.itemsByPlan.entries()) {
      for (const item of items) {
        if (item.product_ean === product.ean || item.originalProduct?.id === product.id) {
          item.product_name = product.title;
          item.category_id = product.category;
          item.category_name = product.category;
          item.price = formatPriceEur(
            catalogDemoService.calculateRestartPriceCents(product.oldStandardPriceCents, product.account)
          );
          item.image_url = product.imageUrl;
          item.originalProduct = product;
          if (product.title.length <= 70 && product.category) {
            if (item.status === "failed" && !item.otto_product_exists) {
              item.status = "pending";
              item.error_message = undefined;
              item.missing_attributes = undefined;
            }
          }
        }
      }
    }
  }
}

export const dripFeedService = new DripFeedService();
