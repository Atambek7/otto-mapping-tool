const PORTAL_TAB_PATTERNS = [
  "https://portal.otto.market/*",
  "https://*.otto.market/*",
];

const OTTORESTART_TAB_PATTERNS = [
  "https://omt.automatonsoft.de/restart*",
  "http://localhost:3002/restart*",
];

async function findBestPortalTab() {
  const portalTabs = await chrome.tabs.query({ url: PORTAL_TAB_PATTERNS });
  if (!portalTabs.length) return null;
  const preferred = portalTabs.find((tab) => {
    const url = String(tab.url || "");
    return url.includes("portal.otto.market");
  });
  return preferred || portalTabs[0];
}

async function findOttoRestartTab() {
  const tabs = await chrome.tabs.query({ url: OTTORESTART_TAB_PATTERNS });
  if (!tabs.length) return null;
  return tabs.find((t) => t.active) || tabs[0];
}

/**
 * Executed in the active OTTO Partner Connect browser tab (`portal.otto.market`).
 * Keeps all cookies/session inside the user's own browser context.
 */
async function portalScrapeInTab(missingItems) {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const cache = new Map();
  const output = [];

  async function getJson(path) {
    const response = await fetch(path, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    if (response.status === 401 || response.status === 403) {
      throw new Error("AUTH_REQUIRED");
    }
    const contentType = response.headers.get("content-type") || "";
    if (!response.ok) {
      throw new Error(`HTTP_${response.status}`);
    }
    if (!contentType.includes("application/json")) {
      throw new Error("NOT_JSON_RESPONSE");
    }
    return response.json();
  }

  for (const item of missingItems) {
    const searchKeys = [];
    if (item.ean) searchKeys.push(String(item.ean).trim());
    if (item.sku && String(item.sku).trim() !== String(item.ean || "").trim()) {
      searchKeys.push(String(item.sku).trim());
    }
    if (!searchKeys.length) continue;

    let matchedVariation = null;
    let matchedProductUuid = "";
    let matchedProductBrandId = "";
    const expectedBrandId = String(item.expectedBrandId || "").trim();

    for (const term of searchKeys) {
      const params = new URLSearchParams({
        currentPage: "1",
        pageSize: "10",
        searchFilter: "",
        searchTerm: term,
        searchedAttributesScope: "ALL",
      });

      const search = await getJson(`/products/api/portal/products/paged/search?${params.toString()}`);
      const products = Array.isArray(search?.content) ? search.content : [];

      for (const product of products) {
        const uuid = product?.productUuid;
        if (!uuid) continue;

        if (!cache.has(uuid)) {
          const variations = await getJson(`/products/api/portal/products/product/${encodeURIComponent(uuid)}`);
          cache.set(uuid, Array.isArray(variations) ? variations : []);
          await sleep(300);
        }

        const variations = cache.get(uuid) || [];
        const matchCandidates = variations.filter((v) => {
          const vSku = String(v?.sku ?? "").trim();
          const vEan = String(v?.ean ?? "").trim();
          return searchKeys.includes(vSku) || searchKeys.includes(vEan);
        });

        if (matchCandidates.length) {
          const brandExact = expectedBrandId
            ? matchCandidates.find(
                (v) => String(v?.brandId || product?.brandId || "").trim() === expectedBrandId,
              )
            : null;
          matchedVariation = brandExact || matchCandidates[0];
          matchedProductUuid = String(uuid);
          matchedProductBrandId = String(
            matchedVariation?.brandId || product?.brandId || "",
          ).trim();
          break;
        }
      }

      if (matchedVariation) break;
      await sleep(300);
    }

    if (!matchedVariation) continue;

    const rawAssets = Array.isArray(matchedVariation.mediaAssets) ? matchedVariation.mediaAssets : [];
    const assets = rawAssets
      .filter((a) => String(a?.type || "").toUpperCase() === "IMAGE" && (a?.assetId || a?.url || a?.location))
      .sort((a, b) => Number(a?.position ?? 0) - Number(b?.position ?? 0))
      .map((a) => ({
        assetId: String(a?.assetId || "").trim(),
        url: String(a?.url || a?.location || (a?.assetId ? `https://i.otto.de/i/otto/${a.assetId}` : "")).trim(),
        position: Number(a?.position ?? 0),
        filename: String(a?.fileName ?? a?.filename ?? "").trim(),
      }))
      .filter((a) => a.url);

    output.push({
      sourceProductId: item.sourceProductId,
      sku: item.sku || undefined,
      ean: item.ean || undefined,
      productUuid: matchedProductUuid,
      brandId: matchedProductBrandId || expectedBrandId,
      assets,
    });
  }

  return output;
}

async function scrapePortalForItems(items) {
  const portalTab = await findBestPortalTab();
  if (!portalTab || typeof portalTab.id !== "number") {
    throw new Error(
      "Откройте и авторизуйтесь во вкладке OTTO Partner Connect (https://portal.otto.market), затем повторите синхронизацию.",
    );
  }

  try {
    const execResults = await chrome.scripting.executeScript({
      target: { tabId: portalTab.id },
      func: portalScrapeInTab,
      args: [items],
    });
    const first = execResults?.[0]?.result;
    if (!Array.isArray(first)) {
      throw new Error("Не удалось прочитать ответ OTTO Partner Connect со страницы портала.");
    }
    return first;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (msg.includes("AUTH_REQUIRED") || msg.includes("NOT_JSON_RESPONSE") || msg.includes("HTTP_404")) {
      throw new Error(
        "Вкладка OTTO Partner Connect не авторизована или открыт другой раздел. Перейдите на https://portal.otto.market и войдите в аккаунт.",
      );
    }
    throw err;
  }
}

async function handlePopupSync(account, includeResolved = false) {
  const ottoTab = await findOttoRestartTab();
  if (!ottoTab || typeof ottoTab.id !== "number") {
    throw new Error(
      "Откройте вкладку OttoRestart (omt.automatonsoft.de/restart или localhost:3002/restart) в этом браузере.",
    );
  }

  let missingResp;
  try {
    missingResp = await chrome.tabs.sendMessage(ottoTab.id, {
      type: "BRIDGE_GET_MISSING",
      account,
      includeResolved,
    });
  } catch {
    throw new Error("Обновите вкладку OttoRestart (F5), чтобы активировать мост расширения.");
  }

  if (!missingResp?.ok) {
    throw new Error(missingResp?.error || "Не удалось получить список товаров без фото из OttoRestart.");
  }

  const missingItems = Array.isArray(missingResp.data?.items) ? missingResp.data.items : [];
  if (!missingItems.length) {
    return {
      account,
      totalMissing: 0,
      matchedCount: 0,
      updatedCount: 0,
      savedAssetsCount: 0,
    };
  }

  const scrapedItems = await scrapePortalForItems(missingItems);
  const saveResp = await chrome.tabs.sendMessage(ottoTab.id, {
    type: "BRIDGE_SAVE_PORTAL_MEDIA",
    account,
    items: scrapedItems,
    overwrite: includeResolved,
    totalMissing: missingItems.length,
    requestId: `popup-${Date.now()}`,
  });

  if (!saveResp?.ok) {
    throw new Error(saveResp?.error || "Не удалось сохранить изображения в OttoRestart.");
  }

  const summary = {
    account,
    totalMissing: missingItems.length,
    matchedCount: scrapedItems.length,
    updatedCount: Number(saveResp.data?.updatedCount ?? 0),
    savedAssetsCount: Number(saveResp.data?.savedAssetsCount ?? 0),
  };
  await chrome.storage.local.set({ lastSyncSummary: { ...summary, at: new Date().toISOString() } });
  return summary;
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || typeof msg !== "object") return false;

  if (msg.type === "SCRAPE_PORTAL_MEDIA") {
    const items = Array.isArray(msg.items) ? msg.items : [];
    scrapePortalForItems(items)
      .then((scraped) => sendResponse({ ok: true, items: scraped }))
      .catch((err) => sendResponse({ ok: false, error: err instanceof Error ? err.message : String(err) }));
    return true;
  }

  if (msg.type === "RUN_SYNC_FROM_POPUP") {
    const account = String(msg.account || "JV").toUpperCase();
    handlePopupSync(account, Boolean(msg.includeResolved))
      .then((summary) => sendResponse({ ok: true, summary }))
      .catch((err) => sendResponse({ ok: false, error: err instanceof Error ? err.message : String(err) }));
    return true;
  }

  if (msg.type === "CHECK_TABS_STATUS") {
    Promise.all([findOttoRestartTab(), findBestPortalTab()])
      .then(([restartTab, portalTab]) => {
        sendResponse({
          ok: true,
          hasRestartTab: Boolean(restartTab),
          restartUrl: restartTab?.url || "",
          hasPortalTab: Boolean(portalTab),
          portalUrl: portalTab?.url || "",
        });
      })
      .catch((err) => sendResponse({ ok: false, error: err instanceof Error ? err.message : String(err) }));
    return true;
  }

  return false;
});
