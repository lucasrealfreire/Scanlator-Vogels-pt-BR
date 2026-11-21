/**
 * Local IndexedDB CacheManager storing translated page PNG blobs and negative
 * (no-text) markers keyed by URL + model + style + uppercase for instant chapter reloads.
 */

import {
  PROMPT_VERSION,
  getExtractionModeForLang,
  type ExtensionSettings,
} from "../shared/types";

const DB_NAME = "scanlator_vogels_cache_v1";
const STORE_NAME = "translated_pages";
const DB_VERSION = 1;
const MAX_ENTRIES = 250;

export interface CachedPageEntry {
  key: string;
  imageUrl: string;
  blob: Blob | null;
  noText: boolean;
  blocksCount: number;
  model: string;
  style: string;
  uppercase: boolean;
  createdAt: number;
}

export function buildCacheKey(
  imageUrl: string,
  settings: ExtensionSettings,
  glossaryHash = "g0"
): string {
  const mode = getExtractionModeForLang(settings.sourceLang);
  return `${PROMPT_VERSION}::${mode}::${imageUrl}::${settings.sourceLang}::${settings.readingDirection}::${settings.model}::${settings.style}::${settings.uppercase ? "U1" : "U0"}::${glossaryHash}`;
}

export class CacheManager {
  private dbPromise: Promise<IDBDatabase> | null = null;

  private openDb(): Promise<IDBDatabase> {
    if (this.dbPromise) return this.dbPromise;
    this.dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE_NAME)) {
          const store = db.createObjectStore(STORE_NAME, { keyPath: "key" });
          store.createIndex("createdAt", "createdAt", { unique: false });
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    return this.dbPromise;
  }

  async get(key: string): Promise<CachedPageEntry | null> {
    try {
      const db = await this.openDb();
      return await new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_NAME, "readonly");
        const req = tx.objectStore(STORE_NAME).get(key);
        req.onsuccess = () => resolve((req.result as CachedPageEntry | undefined) ?? null);
        req.onerror = () => reject(req.error);
      });
    } catch {
      return null;
    }
  }

  async put(entry: CachedPageEntry): Promise<void> {
    try {
      const db = await this.openDb();
      await new Promise<void>((resolve, reject) => {
        const tx = db.transaction(STORE_NAME, "readwrite");
        tx.objectStore(STORE_NAME).put(entry);
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
      await this.pruneIfNeeded();
    } catch {
      /* ignore quota/private-mode IndexedDB errors gracefully */
    }
  }

  async delete(key: string): Promise<void> {
    try {
      const db = await this.openDb();
      await new Promise<void>((resolve, reject) => {
        const tx = db.transaction(STORE_NAME, "readwrite");
        tx.objectStore(STORE_NAME).delete(key);
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
    } catch {
      /* ignore */
    }
  }

  async clearAll(): Promise<void> {
    try {
      const db = await this.openDb();
      await new Promise<void>((resolve, reject) => {
        const tx = db.transaction(STORE_NAME, "readwrite");
        tx.objectStore(STORE_NAME).clear();
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
    } catch {
      /* ignore */
    }
  }

  private async pruneIfNeeded(): Promise<void> {
    try {
      const db = await this.openDb();
      const allKeys = await new Promise<{ key: string; createdAt: number }[]>((resolve, reject) => {
        const tx = db.transaction(STORE_NAME, "readonly");
        const idx = tx.objectStore(STORE_NAME).index("createdAt");
        const items: { key: string; createdAt: number }[] = [];
        const cursorReq = idx.openCursor();
        cursorReq.onsuccess = () => {
          const cursor = cursorReq.result;
          if (cursor) {
            const val = cursor.value as CachedPageEntry;
            items.push({ key: val.key, createdAt: val.createdAt });
            cursor.continue();
          } else {
            resolve(items);
          }
        };
        cursorReq.onerror = () => reject(cursorReq.error);
      });

      if (allKeys.length <= MAX_ENTRIES) return;
      const toRemove = allKeys.slice(0, allKeys.length - MAX_ENTRIES);
      await new Promise<void>((resolve, reject) => {
        const tx = db.transaction(STORE_NAME, "readwrite");
        const store = tx.objectStore(STORE_NAME);
        for (const item of toRemove) {
          store.delete(item.key);
        }
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
    } catch {
      /* ignore */
    }
  }
}
