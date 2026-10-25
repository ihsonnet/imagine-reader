import type { PictureChoice } from "../reading/pictures";
import type { Book, NarrationManifest, Reading, ServiceInfo, SketchManifest } from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = typeof body.detail === "string" ? body.detail : response.statusText;
    throw new ApiError(response.status, detail);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

export const api = {
  info: () => request<ServiceInfo>("/api/info"),
  listBooks: () => request<Book[]>("/api/books"),
  getBook: (id: string) => request<Book>(`/api/books/${id}`),
  reading: (id: string) => request<Reading>(`/api/books/${id}/reading`),
  sourceUrl: (id: string) => `/api/books/${id}/source.pdf`,
  deleteBook: (id: string) => request<void>(`/api/books/${id}`, { method: "DELETE" }),
  /** Prepare a section's narration, starting at the reader's sentence when given. */
  requestNarration: (id: string, sectionId: string, fromSentence?: number) =>
    request<NarrationManifest>(`/api/books/${id}/narration`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ section_id: sectionId, from_sentence: fromSentence ?? null }),
    }),
  narration: (id: string, sectionId: string) =>
    request<NarrationManifest>(`/api/books/${id}/narration?section_id=${encodeURIComponent(sectionId)}`),
  /** Draw a section's pictures in the chosen style, nearest the reader's sentence first when given. */
  requestSketches: (id: string, sectionId: string, choice: PictureChoice, fromSentence?: number) =>
    request<SketchManifest>(`/api/books/${id}/sketches`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ section_id: sectionId, from_sentence: fromSentence ?? null, ...choice }),
    }),
  sketches: (id: string, sectionId: string, choice: PictureChoice) =>
    request<SketchManifest>(
      `/api/books/${id}/sketches?${new URLSearchParams({ section_id: sectionId, style: choice.style, words: String(choice.words) })}`,
    ),
  /** Read a book that was too long again, up to the page and word limits. */
  trim: (id: string) => request<Book>(`/api/books/${id}/trim`, { method: "POST" }),
  /** Stop preparing narration and sketches for a book. */
  cancel: (id: string) => request<{ cancelled: number }>(`/api/books/${id}/cancel`, { method: "POST" }),
  /** Add a PDF; its first chapter is prepared with the chosen pictures. */
  upload: (file: File, choice: PictureChoice) => {
    const form = new FormData();
    form.append("file", file);
    form.append("style", choice.style);
    form.append("words", String(choice.words));
    return request<Book>("/api/books", { method: "POST", body: form });
  },
};
