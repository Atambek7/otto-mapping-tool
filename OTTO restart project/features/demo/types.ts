import type { RestartAccount, RestartPeriod } from "../api/types";
import type { ReviewStatus } from "../review/types";

export type DemoEanStatus = "AVAILABLE" | "RESERVED" | "USED" | "QUARANTINED";
export interface DemoBatchItem {
  id: string;
  rowNumber: number;
  sourceEan: string;
  title: string;
  status: ReviewStatus | "IMPORTED";
  newEan: string | null;
  standardPriceCents: number | null;
  newStandardPriceCents: number | null;
  issue: string | null;
}
export interface DemoBatch {
  id: string;
  account: RestartAccount;
  period: RestartPeriod;
  name: string;
  status: "DEMO_REVIEW" | "DEMO_IMPORTED";
  itemCount: number;
  validatedCount: number;
  errorCount: number;
  approvedCount: number;
  createdAt: string;
  fileName: string;
  demoOnly: true;
}
export interface DemoBatchDetails extends DemoBatch { items: DemoBatchItem[] }
export interface DemoBatchPreview {
  mode: "DEMO";
  account: RestartAccount;
  period: RestartPeriod;
  name: string;
  inputCount: number;
  uniqueCount: number;
  duplicateCount: 0;
  invalidCount: 0;
  sampleIds: string[];
  warnings: string[];
  writesPerformed: false;
}
export interface DemoEanEntry {
  ean: string;
  account: RestartAccount;
  status: DemoEanStatus;
  batchId: string | null;
  importedAt: string;
  demoOnly: true;
}
export interface DemoEvent {
  id: string;
  account: RestartAccount;
  eventType: string;
  entityType: string;
  entityId: string;
  summary: string;
  createdAt: string;
  demoOnly: true;
}
export interface DemoSettings {
  account: RestartAccount;
  defaultPeriod: RestartPeriod;
  maximumDemoBatchItems: number;
  strictAttributeChecks: boolean;
  restartPublishEnabled: false;
  priceRule: { source: "DEMO STANDARD PRICE"; JV: "−10.00 EUR"; XL: "−15.00 EUR" };
  updatedAt: string;
  demoOnly: true;
}
export interface DemoPage<T> {
  items: T[];
  page: { page: number; pageSize: number; totalItems: number; totalPages: number };
  mode: "DEMO";
}
export interface DemoOverview {
  account: RestartAccount;
  mode: "DEMO";
  generatedAt: string;
  counts: { candidates: number; awaitingReview: number; needsAttention: number; queued: number; onlineConfirmed: 0; availableEans: number; reservedEans: number };
  batches: DemoBatch[];
  statusBreakdown: { key: string; label: string; count: number }[];
  attention: { id: string; title: string; reason: string; status: string }[];
  activity: DemoEvent[];
}
