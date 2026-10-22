import { randomUUID } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { DemoReviewService, ReviewProblem } from "@/features/review/demo-service";
import { DemoQueueService } from "@/features/queue/demo-queue-service";
import { DemoWorkspaceService } from "@/features/demo/service";
import type { RestartAccount, RestartPeriod } from "@/features/api/types";
import type { ReviewAction, ReviewPatch } from "@/features/review/types";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const COOKIE = "ottorestart_demo_session";
const TTL = 60 * 60 * 1000;
// Bump when the in-memory demo service shape changes. Next dev keeps globalThis
// across HMR, so a cookie can otherwise point at objects created by old code.
const SESSION_REVISION = 2;
type Session = { revision: number; service: DemoReviewService; queue: DemoQueueService; workspace: DemoWorkspaceService; touched: number };
const globalStore = globalThis as typeof globalThis & { ottoRestartDemoSessions?: Map<string, Session> };
const sessions = globalStore.ottoRestartDemoSessions ??= new Map<string, Session>();

function isReviewProblem(cause: unknown): cause is { status: number; code: string; message: string } {
  if (!cause || typeof cause !== "object") return false;
  const problem = cause as { status?: unknown; code?: unknown; message?: unknown };
  return typeof problem.status === "number" && Number.isInteger(problem.status) && typeof problem.code === "string" && typeof problem.message === "string";
}

type Context = { params: Promise<{ segments: string[] }> };
async function handle(request: NextRequest, context: Context) {
  try {
    const { segments } = await context.params;
    const account = request.nextUrl.searchParams.get("account");
    if (account !== "JV" && account !== "XL") throw new ReviewProblem(422, "VALIDATION_ERROR", "Укажите аккаунт JV или XL.");
    const scopedAccount: RestartAccount = account;
    if (request.method !== "GET") {
      const origin = request.headers.get("origin");
      const host = request.headers.get("host");
      let originHost = "";
      try { originHost = origin ? new URL(origin).host : ""; } catch { /* Invalid/null origins cannot write. */ }
      if (!originHost || originHost !== host || request.headers.get("x-ottorestart-demo") !== "1" || !request.headers.get("content-type")?.startsWith("application/json")) throw new ReviewProblem(403, "FORBIDDEN", "Демо-действия доступны только из интерфейса OttoRestart на том же адресе.");
    }
    for (const [key, value] of sessions) if (Date.now() - value.touched > TTL) sessions.delete(key);
    let sessionId = request.cookies.get(COOKIE)?.value;
    let session = sessionId ? sessions.get(sessionId) : undefined;
    if (session && session.revision !== SESSION_REVISION) {
      sessions.delete(sessionId!);
      session = undefined;
    }
    if (!session) {
      if (request.method !== "GET") throw new ReviewProblem(409, "SESSION_EXPIRED", "Демо-сессия истекла. Обновите список товаров.");
      if (sessions.size >= 100) sessions.delete(sessions.keys().next().value!);
      sessionId = randomUUID();
      session = { revision: SESSION_REVISION, service: new DemoReviewService(), queue: new DemoQueueService(), workspace: new DemoWorkspaceService(), touched: Date.now() };
      sessions.set(sessionId, session);
    }
    session.touched = Date.now();
    let result: unknown;
    const path = segments.join("/");
    if (request.method === "GET" && segments.join("/") === "items") {
      result = session.service.list(scopedAccount, request.nextUrl.searchParams.get("q")?.slice(0, 200), request.nextUrl.searchParams.get("status") ?? "", Number(request.nextUrl.searchParams.get("page") ?? 1), Number(request.nextUrl.searchParams.get("pageSize") ?? 20));
    } else if (request.method === "GET" && segments.join("/") === "queue") {
      result = session.queue.snapshot(scopedAccount);
    } else if (request.method === "GET" && path === "overview") {
      result = session.workspace.overview(scopedAccount, session.service, session.queue);
    } else if (request.method === "GET" && path === "batches") {
      result = session.workspace.listBatches(scopedAccount, session.service, request.nextUrl.searchParams.get("q")?.slice(0, 120) ?? "", request.nextUrl.searchParams.get("status") ?? "", Number(request.nextUrl.searchParams.get("page") ?? 1), Number(request.nextUrl.searchParams.get("pageSize") ?? 20), request.nextUrl.searchParams.get("period") ?? "");
    } else if (request.method === "GET" && segments.length === 2 && segments[0] === "batches") {
      result = session.workspace.getBatchSummary(scopedAccount, segments[1], session.service);
    } else if (request.method === "GET" && segments.length === 3 && segments[0] === "batches" && segments[2] === "items") {
      result = session.workspace.listBatchItems(scopedAccount, segments[1], session.service, request.nextUrl.searchParams.get("q")?.slice(0, 200) ?? "", Number(request.nextUrl.searchParams.get("page") ?? 1), Number(request.nextUrl.searchParams.get("pageSize") ?? 50));
    } else if (request.method === "GET" && path === "ean-pool") {
      result = session.workspace.listEans(scopedAccount, session.service, request.nextUrl.searchParams.get("status") ?? "", request.nextUrl.searchParams.get("q")?.slice(0, 120) ?? "");
    } else if (request.method === "GET" && path === "events") {
      result = session.workspace.listEvents(scopedAccount, session.service, request.nextUrl.searchParams.get("q")?.slice(0, 120) ?? "", session.queue);
    } else if (request.method === "GET" && path === "settings") {
      result = session.workspace.getSettings(scopedAccount);
    } else if (request.method === "GET" && segments.length === 2 && segments[0] === "items") {
      result = session.service.get(scopedAccount, segments[1]);
    } else if (request.method === "GET") {
      throw new ReviewProblem(404, "NOT_FOUND", "Демо-маршрут не найден.");
    } else {
      const text = await request.text();
      if (text.length > 10_000) throw new ReviewProblem(413, "VALIDATION_ERROR", "Слишком большой запрос.");
      let body: { expectedVersion?: number; patch?: ReviewPatch; items?: { id: string; expectedVersion: number }[]; period?: RestartPeriod; name?: string; itemCount?: number; count?: number; defaultPeriod?: RestartPeriod; maximumDemoBatchItems?: number; strictAttributeChecks?: boolean };
      try { body = JSON.parse(text); } catch { throw new ReviewProblem(422, "VALIDATION_ERROR", "Некорректный JSON."); }
      if (!body || typeof body !== "object" || Array.isArray(body)) throw new ReviewProblem(422, "VALIDATION_ERROR", "Некорректное тело запроса.");
      if (path === "batches" && request.method === "POST") {
        result = session.workspace.createBatch(scopedAccount, { period: body.period!, name: body.name!, itemCount: body.itemCount }, session.service);
      } else if (path === "batches/preview" && request.method === "POST") {
        result = session.workspace.previewBatch(scopedAccount, { period: body.period!, name: body.name!, itemCount: body.itemCount });
      } else if (path === "ean-pool/demo-import" && request.method === "POST") {
        result = session.workspace.addDemoEans(scopedAccount, body.count ?? 4);
      } else if (path === "settings" && request.method === "PATCH") {
        result = session.workspace.patchSettings(scopedAccount, { defaultPeriod: body.defaultPeriod, maximumDemoBatchItems: body.maximumDemoBatchItems, strictAttributeChecks: body.strictAttributeChecks });
        session.service.setStrictAttributeChecks(scopedAccount, (result as { strictAttributeChecks: boolean }).strictAttributeChecks);
      } else if (path === "reset" && request.method === "POST") {
        session.service = new DemoReviewService(); session.queue = new DemoQueueService(); session.workspace = new DemoWorkspaceService();
        result = { mode: "DEMO", account: scopedAccount, message: "Демо-данные текущей сессии сброшены." };
      } else if (segments[0] === "queue" && request.method === "POST" && segments.join("/") === "queue/enqueue-dry-run") {
        result = session.queue.enqueueDryRun(scopedAccount, body.items as { id: string; expectedVersion: number }[], session.service);
      } else if (segments.join("/") === "queue/pause" && request.method === "POST") {
        result = session.queue.pause(scopedAccount);
      } else if (segments.join("/") === "queue/resume" && request.method === "POST") {
        result = session.queue.resume(scopedAccount);
      } else if (segments.join("/") === "queue/stop-accepting" && request.method === "POST") {
        result = session.queue.stopAccepting(scopedAccount);
      } else if (segments.join("/") === "queue/start-accepting" && request.method === "POST") {
        result = session.queue.startAccepting(scopedAccount);
      } else if (segments[0] === "queue" && segments[1] === "tasks" && segments.length === 4 && request.method === "POST" && segments[3] === "dry-run") {
        result = session.queue.runDryRun(scopedAccount, segments[2], session.service);
      } else if (segments[0] === "queue" && segments[1] === "tasks" && segments.length === 4 && request.method === "POST" && segments[3] === "cancel") {
        result = session.queue.cancel(scopedAccount, segments[2]);
      } else if (segments.join("/") === "approve" && request.method === "POST") {
        if (!Array.isArray(body.items) || !body.items.length || body.items.length > 20 || body.items.some((item) => !item || typeof item.id !== "string" || !Number.isInteger(item.expectedVersion)) || new Set(body.items.map((item) => item.id)).size !== body.items.length) throw new ReviewProblem(422, "VALIDATION_ERROR", "Передайте до 20 уникальных ID и их версий.");
        result = body.items.map((entry) => {
          try { return { id: entry.id, item: session!.service.act(scopedAccount, entry.id, entry.expectedVersion, "approve") }; }
          catch (cause) { return { id: entry.id, error: isReviewProblem(cause) ? cause.message : "Не удалось утвердить запись." }; }
        });
      } else if (segments[0] === "items" && segments.length === 2 && request.method === "PATCH") {
        result = session.service.patch(scopedAccount, segments[1], body.expectedVersion!, body.patch!);
      } else if (segments[0] === "items" && segments.length === 3 && request.method === "POST") {
        const actions: ReviewAction[] = ["prepare", "suggest", "refresh-price", "validate", "approve", "simulate-price-change"];
        if (!actions.includes(segments[2] as ReviewAction)) throw new ReviewProblem(404, "NOT_FOUND", "Действие недоступно. Публикация в демо отсутствует.");
        result = session.service.act(scopedAccount, segments[1], body.expectedVersion!, segments[2] as ReviewAction);
      } else throw new ReviewProblem(404, "NOT_FOUND", "Демо-маршрут не найден.");
    }
    const response = NextResponse.json(result, { headers: { "Cache-Control": "no-store", "X-OttoRestart-Mode": "DEMO" } });
    response.cookies.set(COOKIE, sessionId!, { httpOnly: true, sameSite: "strict", secure: request.nextUrl.protocol === "https:", path: "/restart/api/demo", maxAge: TTL / 1000 });
    return response;
  } catch (cause) {
    // Structural check also recognizes ReviewProblem instances retained across
    // Next.js hot reloads, where instanceof may fail across module versions.
    const requestId = randomUUID();
    if (!isReviewProblem(cause)) console.error(`[OttoRestart demo API ${requestId}] ${request.method} ${request.nextUrl.pathname}`, cause);
    const error = isReviewProblem(cause)
      ? cause
      : { status: 500, code: "INTERNAL_ERROR", message: "Не удалось обработать демо-действие." };
    return NextResponse.json({ code: error.code, message: error.message, requestId }, { status: error.status, headers: { "Cache-Control": "no-store" } });
  }
}
export const GET = handle;
export const POST = handle;
export const PATCH = handle;
