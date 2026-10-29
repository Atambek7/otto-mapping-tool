import type { RestartAccount } from "../api/types";

export type DemoQueueTaskStatus = "QUEUED" | "DRY_RUN_COMPLETE" | "BLOCKED" | "CANCELLED";

export interface DemoQueueTask {
  id: string;
  account: RestartAccount;
  itemId: string;
  batchId: string;
  title: string;
  oldSku: string;
  newEan: string;
  payloadVersion: number;
  priceSnapshotVersion: number;
  payloadHash: string;
  baseCents: number;
  discountCents: number;
  newCents: number;
  status: DemoQueueTaskStatus;
  createdAt: string;
  completedAt: string | null;
  attempts: number;
  result: string | null;
}

export interface DemoQueueSnapshot {
  mode: "DEMO";
  account: RestartAccount;
  publicationEnabled: false;
  paused: boolean;
  acceptingTasks: boolean;
  counts: Record<DemoQueueTaskStatus, number>;
  tasks: DemoQueueTask[];
}

export interface DemoQueueEntry { id: string; expectedVersion: number }
export interface DemoQueueEnqueueResult { id: string; task?: DemoQueueTask; error?: string }
