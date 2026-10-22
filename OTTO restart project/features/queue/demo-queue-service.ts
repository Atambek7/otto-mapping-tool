import { randomUUID } from "node:crypto";
import type { RestartAccount } from "../api/types";
import { DemoReviewService, ReviewProblem } from "../review/demo-service";
import type { DemoApprovedSnapshot } from "../review/demo-service";
import type { DemoQueueEnqueueResult, DemoQueueEntry, DemoQueueSnapshot, DemoQueueTask, DemoQueueTaskStatus } from "./types";

type Controls = { paused: boolean; acceptingTasks: boolean };
type InternalTask = DemoQueueTask & { approvedPayload: DemoApprovedSnapshot["payload"] };
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
const ACCOUNTS = ["JV", "XL"] as const;

/** Single-process, session-scoped dry-run queue. It has no worker or external adapter. */
export class DemoQueueService {
  private tasks = new Map<string, InternalTask>();
  private controls = new Map<RestartAccount, Controls>(ACCOUNTS.map((account) => [account, { paused: false, acceptingTasks: true }]));

  constructor(private readonly clock: () => Date = () => new Date(), private readonly createId: () => string = randomUUID) {}

  private now() { return this.clock().toISOString(); }
  private control(account: RestartAccount) { return this.controls.get(account)!; }
  private task(account: RestartAccount, id: string) {
    const task = this.tasks.get(id);
    if (!task || task.account !== account) throw new ReviewProblem(404, "NOT_FOUND", "Задача не найдена в очереди выбранного аккаунта.");
    return task;
  }

  snapshot(account: RestartAccount): DemoQueueSnapshot {
    const tasks = [...this.tasks.values()].filter((task) => task.account === account).sort((a, b) => b.createdAt.localeCompare(a.createdAt));
    const counts: Record<DemoQueueTaskStatus, number> = { QUEUED: 0, DRY_RUN_COMPLETE: 0, BLOCKED: 0, CANCELLED: 0 };
    for (const task of tasks) counts[task.status] += 1;
    const { paused, acceptingTasks } = this.control(account);
    return { mode: "DEMO", account, publicationEnabled: false, paused, acceptingTasks, counts, tasks: clone(tasks.map(({ approvedPayload: _payload, ...task }) => task)) };
  }

  pause(account: RestartAccount) {
    this.control(account).paused = true;
    return this.snapshot(account);
  }

  resume(account: RestartAccount) {
    this.control(account).paused = false;
    return this.snapshot(account);
  }

  stopAccepting(account: RestartAccount) {
    this.control(account).acceptingTasks = false;
    return this.snapshot(account);
  }

  startAccepting(account: RestartAccount) {
    this.control(account).acceptingTasks = true;
    return this.snapshot(account);
  }

  enqueueDryRun(account: RestartAccount, entries: DemoQueueEntry[], review: DemoReviewService): { queue: DemoQueueSnapshot; results: DemoQueueEnqueueResult[] } {
    if (!this.control(account).acceptingTasks) throw new ReviewProblem(409, "QUEUE_NOT_ACCEPTING", "Приём новых демо-задач остановлен.");
    if (!Array.isArray(entries) || entries.length < 1 || entries.length > 20 || entries.some((entry) => !entry || typeof entry.id !== "string" || !Number.isInteger(entry.expectedVersion)) || new Set(entries.map((entry) => entry.id)).size !== entries.length) throw new ReviewProblem(422, "VALIDATION_ERROR", "Передайте от 1 до 20 уникальных ID карточек и их версий.");

    const results = entries.map(({ id, expectedVersion }) => {
      try {
        const item = review.get(account, id);
        if (item.version !== expectedVersion) throw new ReviewProblem(409, "CONFLICT", "Карточка изменилась. Обновите список и повторите добавление.");
        if (item.status !== "APPROVED" || item.publishAllowed !== false || !item.approval?.demoOnly) throw new ReviewProblem(409, "INVALID_STATE", "В демо-очередь проверки можно добавить только утверждённую демо-карточку.");
        const approvedPayload = review.approvedPayload(account, id);
        if (!approvedPayload || approvedPayload.demoOnly !== true || approvedPayload.price.snapshotVersion !== item.approval.priceSnapshotVersion) throw new ReviewProblem(409, "INVALID_STATE", "Не найден целостный снимок утверждённой демо-карточки.");

        const duplicate = [...this.tasks.values()].find((task) => task.account === account && task.itemId === id && task.payloadHash === item.approval!.payloadHash && task.status !== "CANCELLED");
        if (duplicate) return { id, task: this.publicTask(duplicate) };
        if ([...this.tasks.values()].filter((task) => task.account === account).length >= 200) throw new ReviewProblem(429, "QUEUE_LIMIT", "Демо-очередь достигла лимита 200 задач для аккаунта.");

        const task: InternalTask = {
          id: this.createId(), account, itemId: item.id, batchId: item.batchId, title: approvedPayload.content.title,
          oldSku: item.old.sku, newEan: approvedPayload.identity.newEan,
          payloadVersion: item.approval.payloadVersion, priceSnapshotVersion: item.approval.priceSnapshotVersion,
          payloadHash: item.approval.payloadHash, baseCents: approvedPayload.price.baseCents,
          discountCents: approvedPayload.price.discountCents, newCents: approvedPayload.price.newCents,
          status: "QUEUED", createdAt: this.now(), completedAt: null, attempts: 0, result: null,
          approvedPayload: clone(approvedPayload),
        };
        this.tasks.set(task.id, task);
        return { id, task: this.publicTask(task) };
      } catch (cause) {
        return { id, error: cause instanceof ReviewProblem ? cause.message : "Не удалось добавить демо-задачу." };
      }
    });
    return { queue: this.snapshot(account), results };
  }

  runDryRun(account: RestartAccount, id: string, review: DemoReviewService) {
    const task = this.task(account, id);
    if (this.control(account).paused) throw new ReviewProblem(409, "QUEUE_PAUSED", "Обработка приостановлена. Возобновите демо-очередь перед сухой проверкой.");
    if (task.status !== "QUEUED") throw new ReviewProblem(409, "INVALID_STATE", "Сухая проверка доступна только для ожидающей задачи.");
    task.attempts += 1;
    const check = review.verifyApprovedSnapshot(account, task.itemId, task.payloadVersion, task.priceSnapshotVersion, task.payloadHash);
    if (!check.ok) {
      task.status = "BLOCKED";
      task.completedAt = this.now();
      task.result = check.message;
      return this.publicTask(task);
    }
    // Check that the immutable queue snapshot is the approved payload. No POST or network request is made.
    const contentHashPayload = task.approvedPayload;
    if (contentHashPayload.price.snapshotVersion !== task.priceSnapshotVersion || contentHashPayload.identity.newEan !== task.newEan || contentHashPayload.demoOnly !== true) {
      task.status = "BLOCKED";
      task.completedAt = this.now();
      task.result = "Снимок задачи не совпал с утверждённой демо-версией.";
      return this.publicTask(task);
    }
    task.status = "DRY_RUN_COMPLETE";
    task.completedAt = this.now();
    task.result = "Локальная сухая проверка пройдена. Запросы к OTTO не выполнялись; публикация отключена.";
    return this.publicTask(task);
  }

  cancel(account: RestartAccount, id: string) {
    const task = this.task(account, id);
    if (task.status !== "QUEUED") throw new ReviewProblem(409, "INVALID_STATE", "Отменить можно только ожидающую демо-задачу.");
    task.status = "CANCELLED";
    task.completedAt = this.now();
    task.result = "Задача отменена в демо-очереди; действие в OTTO не выполнялось.";
    return this.publicTask(task);
  }

  private publicTask(task: InternalTask): DemoQueueTask {
    const { approvedPayload: _payload, ...publicTask } = task;
    return clone(publicTask);
  }
}
