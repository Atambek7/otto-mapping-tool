(() => {
  const UI_SOURCE = "ottorestart-ui";
  const EXT_SOURCE = "ottorestart-extension";

  function postToPage(payload) {
    window.postMessage(
      {
        source: EXT_SOURCE,
        ...payload,
      },
      window.location.origin,
    );
  }

  async function fetchJsonSameOrigin(path, options = {}) {
    const headers = {
      Accept: "application/json",
      ...(options.headers || {}),
    };
    const response = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
      ...options,
      headers,
    });
    const text = await response.text();
    let parsed = null;
    try {
      parsed = text ? JSON.parse(text) : null;
    } catch {
      parsed = null;
    }
    if (!response.ok) {
      const msg =
        (parsed && typeof parsed.message === "string" && parsed.message) ||
        `HTTP ${response.status} (${path})`;
      throw new Error(msg);
    }
    return parsed;
  }

  async function getCsrfToken() {
    const data = await fetchJsonSameOrigin("/restart/api/auth/csrf");
    if (!data || typeof data.token !== "string" || !data.token) {
      throw new Error("Не удалось получить CSRF-токен сессии OttoRestart.");
    }
    return data.token;
  }

  async function fetchMissingItems(account, includeResolved = false) {
    const params = new URLSearchParams({
      account: account || "JV",
      sourceType: "OTTO_DIRECT",
    });
    if (includeResolved) params.set("includeResolved", "true");
    return fetchJsonSameOrigin(`/restart/api/backend/media/missing?${params.toString()}`);
  }

  async function savePortalAssets(account, items, overwrite = false) {
    const csrfToken = await getCsrfToken();
    const idempotencyKey = `ext-pm-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    return fetchJsonSameOrigin(
      `/restart/api/backend/media/portal-assets?account=${encodeURIComponent(account || "JV")}`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify({
          account: account || "JV",
          overwrite: Boolean(overwrite),
          items,
        }),
      },
    );
  }

  async function runPageTriggeredSync({ requestId, account }) {
    postToPage({
      type: "OTTO_RESTART_PORTAL_MEDIA_ACK",
      requestId,
      message: `Получение списка товаров без фото (${account})...`,
    });

    const missing = await fetchMissingItems(account, false);
    const missingItems = Array.isArray(missing?.items) ? missing.items : [];
    if (missingItems.length === 0) {
      postToPage({
        type: "OTTO_RESTART_PORTAL_MEDIA_RESULT",
        requestId,
        ok: true,
        updatedCount: 0,
        savedAssetsCount: 0,
        totalMissing: 0,
      });
      return;
    }

    postToPage({
      type: "OTTO_RESTART_PORTAL_MEDIA_PROGRESS",
      requestId,
      message: `Поиск фото в OTTO Partner Connect для ${missingItems.length} шт....`,
    });

    const scrapeResp = await chrome.runtime.sendMessage({
      type: "SCRAPE_PORTAL_MEDIA",
      requestId,
      account,
      items: missingItems,
    });

    if (!scrapeResp || !scrapeResp.ok) {
      throw new Error(scrapeResp?.error || "Фоновый скрипт расширения не смог опросить OTTO Portal.");
    }

    const scrapedItems = Array.isArray(scrapeResp.items) ? scrapeResp.items : [];
    postToPage({
      type: "OTTO_RESTART_PORTAL_MEDIA_PROGRESS",
      requestId,
      message: `Сохранение найденных изображений (${scrapedItems.length}) в OttoRestart...`,
    });

    const saved = await savePortalAssets(account, scrapedItems, false);
    postToPage({
      type: "OTTO_RESTART_PORTAL_MEDIA_RESULT",
      requestId,
      ok: true,
      totalMissing: missingItems.length,
      updatedCount: Number(saved?.updatedCount ?? 0),
      savedAssetsCount: Number(saved?.savedAssetsCount ?? 0),
      results: saved?.results || [],
    });
  }

  window.addEventListener("message", (event) => {
    if (event.source !== window || event.origin !== window.location.origin) return;
    const data = event.data;
    if (!data || data.source !== UI_SOURCE) return;

    if (data.type === "OTTO_RESTART_EXTENSION_PING") {
      postToPage({ type: "OTTO_RESTART_EXTENSION_PONG", version: "1.0.0" });
      return;
    }

    if (data.type === "OTTO_RESTART_FETCH_PORTAL_MEDIA") {
      const requestId = String(data.requestId || "");
      const account = String(data.account || "JV").toUpperCase();
      runPageTriggeredSync({ requestId, account }).catch((err) => {
        postToPage({
          type: "OTTO_RESTART_PORTAL_MEDIA_RESULT",
          requestId,
          ok: false,
          error: err instanceof Error ? err.message : String(err),
        });
      });
    }
  });

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg || typeof msg !== "object") return false;

    if (msg.type === "BRIDGE_GET_MISSING") {
      fetchMissingItems(String(msg.account || "JV").toUpperCase(), Boolean(msg.includeResolved))
        .then((data) => sendResponse({ ok: true, data }))
        .catch((err) => sendResponse({ ok: false, error: err instanceof Error ? err.message : String(err) }));
      return true;
    }

    if (msg.type === "BRIDGE_SAVE_PORTAL_MEDIA") {
      const account = String(msg.account || "JV").toUpperCase();
      savePortalAssets(account, Array.isArray(msg.items) ? msg.items : [], Boolean(msg.overwrite))
        .then((data) => {
          postToPage({
            type: "OTTO_RESTART_PORTAL_MEDIA_RESULT",
            requestId: String(msg.requestId || ""),
            ok: true,
            totalMissing: Number(msg.totalMissing ?? 0),
            updatedCount: Number(data?.updatedCount ?? 0),
            savedAssetsCount: Number(data?.savedAssetsCount ?? 0),
          });
          sendResponse({ ok: true, data });
        })
        .catch((err) => sendResponse({ ok: false, error: err instanceof Error ? err.message : String(err) }));
      return true;
    }

    return false;
  });

  postToPage({ type: "OTTO_RESTART_EXTENSION_READY", version: "1.0.0" });
})();
