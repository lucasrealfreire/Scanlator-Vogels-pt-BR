/**
 * Decoupled Reader Adapter interface so site-specific adapters can be registered
 * in future stages without touching the universal detection or translation core.
 */

export interface DetectedMangaPage {
  element: HTMLImageElement;
  imageUrl: string;
  index: number;
}

export interface ReaderAdapter {
  readonly name: string;
  matches(url: URL, doc: Document): boolean;
  resolveImageUrl(img: HTMLImageElement): string | null;
  isComicPageCandidate(img: HTMLImageElement): boolean;
  findCandidateImages(doc: Document): HTMLImageElement[];
}
