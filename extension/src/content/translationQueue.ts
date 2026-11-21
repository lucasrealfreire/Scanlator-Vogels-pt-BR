/**
 * Hybrid smart translation queue:
 * - Fast lane (`priority >= 50` or `forceRetranslate`): visible / manual / retry pages go
 *   immediately as a single `/translate-page` call for minimal first-page latency.
 * - Batch lane (`priority < 50`): Auto Translate prefetch (next 3–4 pages) and remaining
 *   chapter pages are grouped into batches of up to 4 pages (`POST /translate-batch`) in
 *   1 Gemini Free Tier API call!
 * - Strict concurrency = 1 on Gemini API + lookahead peek for pipelined `/prepare-page`.
 */

import type { DetectedMangaPage } from "./adapters/types";

export interface QueueTask {
  page: DetectedMangaPage;
  priority: number; // 100 = manual/retry/visible-chapter, 50 = visible-auto, 30 = chapter batch, 10 = auto prefetch
  forceRetranslate: boolean;
}

export class TranslationQueue {
  private readonly singleWorkerFn: (task: QueueTask, nextTasks: QueueTask[]) => Promise<void>;
  private readonly batchWorkerFn: (tasks: QueueTask[], nextTasks: QueueTask[]) => Promise<void>;
  private readonly onQueueStateChange: (
    pending: number,
    activeCount: number,
    chapterActive: boolean
  ) => void;
  private readonly maxBatchSize: number;

  private queue: QueueTask[] = [];
  private activeUrls = new Set<string>();
  private running = false;
  private paused = false;
  private chapterActive = false;
  private generation = 0;

  constructor(
    singleWorkerFn: (task: QueueTask, nextTasks: QueueTask[]) => Promise<void>,
    batchWorkerFn: (tasks: QueueTask[], nextTasks: QueueTask[]) => Promise<void>,
    onQueueStateChange: (pending: number, activeCount: number, chapterActive: boolean) => void,
    maxBatchSize = 4
  ) {
    this.singleWorkerFn = singleWorkerFn;
    this.batchWorkerFn = batchWorkerFn;
    this.onQueueStateChange = onQueueStateChange;
    this.maxBatchSize = Math.max(1, Math.min(4, maxBatchSize));
  }

  enqueue(page: DetectedMangaPage, priority: number, forceRetranslate = false): void {
    if (this.paused && priority < 90) {
      return;
    }
    if (this.activeUrls.has(page.imageUrl) && !forceRetranslate) {
      return;
    }

    const existing = this.queue.find((t) => t.page.imageUrl === page.imageUrl);
    if (existing) {
      existing.priority = Math.max(existing.priority, priority);
      existing.forceRetranslate = existing.forceRetranslate || forceRetranslate;
      existing.page = page;
    } else {
      this.queue.push({ page, priority, forceRetranslate });
    }

    this.sortQueue();
    this.emitState();
    void this.pump();
  }

  enqueueChapter(pages: DetectedMangaPage[], visiblePage?: DetectedMangaPage | null): void {
    this.paused = false;
    this.chapterActive = true;
    const visibleUrl = visiblePage?.imageUrl ?? null;

    for (const p of pages) {
      if (this.activeUrls.has(p.imageUrl)) continue;
      const prio = visibleUrl && p.imageUrl === visibleUrl ? 100 : 30;
      const existing = this.queue.find((t) => t.page.imageUrl === p.imageUrl);
      if (existing) {
        existing.priority = Math.max(existing.priority, prio);
        existing.page = p;
      } else {
        this.queue.push({ page: p, priority: prio, forceRetranslate: false });
      }
    }

    this.sortQueue();
    this.emitState();
    void this.pump();
  }

  pauseAndClearPrefetch(): void {
    this.paused = true;
    this.chapterActive = false;
    this.queue = [];
    this.emitState();
  }

  cancelAll(): void {
    this.paused = true;
    this.chapterActive = false;
    this.generation += 1;
    this.queue = [];
    this.activeUrls.clear();
    this.emitState();
  }

  resume(): void {
    this.paused = false;
    this.emitState();
    void this.pump();
  }

  isPaused(): boolean {
    return this.paused;
  }

  isChapterActive(): boolean {
    return this.chapterActive && (this.queue.length > 0 || this.activeUrls.size > 0);
  }

  getPendingCount(): number {
    return this.queue.length + this.activeUrls.size;
  }

  private sortQueue(): void {
    this.queue.sort((a, b) => b.priority - a.priority || a.page.index - b.page.index);
  }

  private emitState(): void {
    const activeCount = this.activeUrls.size;
    if (activeCount === 0 && this.queue.length === 0) {
      this.chapterActive = false;
    }
    this.onQueueStateChange(this.queue.length, activeCount, this.chapterActive);
  }

  private async pump(): Promise<void> {
    if (this.running) return;
    this.running = true;
    const myGen = this.generation;

    try {
      while (this.queue.length > 0 && myGen === this.generation) {
        const head = this.queue[0];
        if (this.paused && head.priority < 90) {
          break;
        }

        // Fast lane: visible page, manual click, or retry -> immediate single-page call!
        if (head.priority >= 50 || head.forceRetranslate) {
          const single = this.queue.shift()!;
          const upcoming = !this.paused ? this.queue.slice(0, 2) : [];
          this.activeUrls.add(single.page.imageUrl);
          this.emitState();

          try {
            await this.singleWorkerFn(single, upcoming);
          } finally {
            if (myGen === this.generation) {
              this.activeUrls.delete(single.page.imageUrl);
              this.emitState();
            }
          }
          continue;
        }

        // Batch lane: Auto prefetch (3–4 pages) or remaining chapter pages -> up to 4 pages / 1 Gemini call!
        const batch: QueueTask[] = [];
        while (
          batch.length < this.maxBatchSize &&
          this.queue.length > 0 &&
          this.queue[0].priority < 50 &&
          !this.queue[0].forceRetranslate
        ) {
          const item = this.queue.shift()!;
          batch.push(item);
          this.activeUrls.add(item.page.imageUrl);
        }

        const upcoming = !this.paused ? this.queue.slice(0, 2) : [];
        this.emitState();

        try {
          if (batch.length >= 2) {
            await this.batchWorkerFn(batch, upcoming);
          } else if (batch.length === 1) {
            await this.singleWorkerFn(batch[0], upcoming);
          }
        } finally {
          if (myGen === this.generation) {
            for (const item of batch) {
              this.activeUrls.delete(item.page.imageUrl);
            }
            this.emitState();
          }
        }
      }
    } finally {
      this.running = false;
      this.emitState();
    }
  }
}
