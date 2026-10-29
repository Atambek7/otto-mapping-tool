import type { RestartAccount, RestartPeriod } from "@/features/api/types";

export type ProductSourceType = "AFTERBUY" | "DIRECT_OTTO";

export type ProductRestartStatus =
  | "3M"
  | "12M"
  | "READY"
  | "ERROR"
  | "PUBLISHED"
  | "NEEDS_AI"
  | "RESERVED"
  | "QUEUED"
  | "NO_CATEGORY";

export interface AfterbuyFactoryItem {
  id: string;
  name: string;
  account: RestartAccount;
  totalProductsCatalog: number;
  candidatesCount3m: number;
  candidatesCount12m: number;
  totalCandidates: number;
  readyCount: number;
  needsAiCount: number;
  errorCount: number;
  existsInOttoCount: number;
  isCached: boolean;
  lastUpdated: string;
}

export interface CatalogProductItem {
  id: string;
  sourceType: ProductSourceType;
  factoryId?: string;
  factoryName?: string;
  account: RestartAccount;
  ean: string;
  sku: string;
  title: string;
  aiSuggestedTitle?: string;
  hasAppliedAiTitle?: boolean;
  hasAiCategory?: boolean;
  hasAiAttributes?: boolean;
  isSyncedWithOtto?: boolean;
  syncedAt?: string;
  imageUrl: string;
  category: string;
  oldStandardPriceCents: number;
  period: RestartPeriod;
  errorCode: "602016 - Nonseller" | "602016 - LOW_PERFORMER";
  status: ProductRestartStatus;
  existsInOtto: boolean;
  attributes: {
    material?: string;
    color?: string;
    width?: string;
    height?: string;
    depth?: string;
    [key: string]: string | undefined;
  };
}

export interface FactoryMetrics {
  totalCandidates: number;
  totalFactories: number;
  count3m: number;
  count12m: number;
  cachedCount: number;
}

export interface FactoryProductsMetrics {
  total: number;
  ready: number;
  count3m: number;
  count12m: number;
  needsAi: number;
  errors: number;
  published: number;
  noCategory: number;
  publishedOrExists: number;
}
