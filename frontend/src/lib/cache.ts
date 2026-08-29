// IndexedDB cache for factory products
// Two types of cache:
// - isPinned: true  → manually downloaded by user, persists across sessions
// - isPinned: false → auto-cached on visit, cleared when browser/tab closes

const DB_NAME = "otto_mapping_cache";
const DB_VERSION = 2;
const STORE_NAME = "factory_products";

// sessionStorage is cleared by the browser when all tabs of the site are closed.
// We track auto-cached factory IDs here.
const SESSION_KEY = "session_auto_cached";

function getSessionAutoCached(): Set<string> {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    return raw ? new Set(JSON.parse(raw)) : new Set();
  } catch {
    return new Set();
  }
}

function addSessionAutoCached(factoryId: string) {
  const ids = getSessionAutoCached();
  ids.add(factoryId);
  sessionStorage.setItem(SESSION_KEY, JSON.stringify([...ids]));
}

export interface CachedFactoryData {
  factoryId: string;
  products: unknown[];
  total: number;
  fetchedAt: number; // timestamp ms
  isPinned?: boolean; // true = manually cached by user, survives session
}

function openDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = (e) => {
      const db = (e.target as IDBOpenDBRequest).result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: "factoryId" });
      }
    };
    req.onsuccess = (e) => resolve((e.target as IDBOpenDBRequest).result);
    req.onerror = () => reject(req.error);
  });
}

export async function getCachedFactory(
  factoryId: string
): Promise<CachedFactoryData | null> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readonly");
    const req = tx.objectStore(STORE_NAME).get(factoryId);
    req.onsuccess = () => resolve((req.result as CachedFactoryData) ?? null);
    req.onerror = () => reject(req.error);
  });
}

export async function getAllCachedFactoryIds(): Promise<string[]> {
  const db = await openDB();
  const all: CachedFactoryData[] = await new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readonly");
    const req = tx.objectStore(STORE_NAME).getAll();
    req.onsuccess = () => resolve((req.result as CachedFactoryData[]) ?? []);
    req.onerror = () => reject(req.error);
  });
  // Show pinned always; show auto-cached only if active in this session
  const sessionIds = getSessionAutoCached();
  return all
    .filter(e => e.isPinned || sessionIds.has(e.factoryId))
    .map(e => e.factoryId);
}

/**
 * Save factory to cache.
 * @param data - factory data
 * @param pin  - if true, mark as pinned (manually downloaded, survives session)
 */
export async function setCachedFactory(data: CachedFactoryData, pin = false): Promise<void> {
  const db = await openDB();

  let isPinned = pin;
  if (!pin) {
    // Don't downgrade an already-pinned entry
    const existing = await getCachedFactory(data.factoryId);
    isPinned = existing?.isPinned ?? false;
    addSessionAutoCached(data.factoryId);
  }

  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    tx.objectStore(STORE_NAME).put({ ...data, isPinned });
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

/**
 * Pin a factory cache manually (survives session close).
 */
export async function pinCachedFactory(factoryId: string): Promise<void> {
  const existing = await getCachedFactory(factoryId);
  if (!existing) return;
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    tx.objectStore(STORE_NAME).put({ ...existing, isPinned: true });
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

/**
 * Unpin a factory (auto-cached only, will be cleared on next session close).
 */
export async function unpinCachedFactory(factoryId: string): Promise<void> {
  const existing = await getCachedFactory(factoryId);
  if (!existing) return;
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    tx.objectStore(STORE_NAME).put({ ...existing, isPinned: false });
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export async function deleteCachedFactory(factoryId: string): Promise<void> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    tx.objectStore(STORE_NAME).delete(factoryId);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

/**
 * Remove all non-pinned entries that are NOT in the current session.
 * Call once on app startup (layout.tsx) to clean up stale auto-caches.
 */
export async function cleanSessionCache(): Promise<void> {
  const db = await openDB();
  const all: CachedFactoryData[] = await new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readonly");
    const req = tx.objectStore(STORE_NAME).getAll();
    req.onsuccess = () => resolve((req.result as CachedFactoryData[]) ?? []);
    req.onerror = () => reject(req.error);
  });

  const sessionIds = getSessionAutoCached();
  const stale = all.filter(e => !e.isPinned && !sessionIds.has(e.factoryId));
  if (stale.length === 0) return;

  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    const store = tx.objectStore(STORE_NAME);
    stale.forEach(e => store.delete(e.factoryId));
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export function formatCacheAge(fetchedAt: number): string {
  const diff = Date.now() - fetchedAt;
  const minutes = Math.floor(diff / 60_000);
  const hours = Math.floor(diff / 3_600_000);
  const days = Math.floor(diff / 86_400_000);
  if (days > 0) return `${days} дн. назад`;
  if (hours > 0) return `${hours} ч. назад`;
  if (minutes > 0) return `${minutes} мин. назад`;
  return "только что";
}





