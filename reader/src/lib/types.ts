// Shapes returned by the API (server/src/imagine_reader_server/api.py and reading.py).

export type BookStatus = "queued" | "processing" | "ready" | "failed";

export interface Book {
  id: string;
  filename: string;
  status: BookStatus;
  error_code: string | null;
  error_message: string | null;
  page_count: number | null;
  word_count: number | null;
  created_at: string;
  start_section: string | null; // where the story begins, past the title page and notes
  cover_url: string | null; // page 1, small, once the book is read
  preparation: Preparation | null;
  shared: boolean; // a sample: comes with the reader, not yours to change or delete
  mine: boolean; // one you added: yours to change or delete
  picture_style: string; // the pictures its opening chapter is prepared with
  picture_words: number;
}

/** How far the voice and pictures of the chapter a book opens at have come. */
export interface Preparation {
  section_id: string;
  pictures_ready: number;
  pictures_planned: number; // 0 until the scenes are planned
  clips_ready: number;
  clips_planned: number;
  active: boolean;
  stopped: string | null; // why it stopped early: daily_limit, provider_account, or budget_exceeded
}

/** [page, x0, top, x1, bottom]: fractions of the visible page, origin top-left. */
export type Box = [number, number, number, number, number];

export interface ReadingPage {
  number: number;
  width: number;
  height: number;
  method: "native" | "needs_ocr" | "empty";
}

export interface Section {
  id: string;
  title: string;
  first_paragraph: number;
  end_paragraph: number;
  page: number;
}

export interface Paragraph {
  id: string;
  order: number;
  text: string;
  heading: boolean;
}

export interface Sentence {
  id: string;
  order: number;
  paragraph_id: string;
  text: string;
  boxes: Box[];
}

export interface NarrationSegment {
  sentence_id: string;
  order: number;
  status: "pending" | "ready" | "failed" | "skipped";
  duration: number | null;
  pause_after: number;
  audio_url: string | null;
  error_code: string | null;
}

export interface NarrationManifest {
  schema_version: string;
  book_id: string;
  section_id: string;
  provider: string;
  voice: string;
  segments: NarrationSegment[];
  characters_used: number;
  character_budget: number;
  complete: boolean;
  preparing: boolean; // a job is working on this section now
  error_code: string | null; // why preparation last stopped early
}

/** Who makes what: OpenRouter with the key, and Cloudflare for the pictures with its account; else placeholders. */
export interface Services {
  planning: "openrouter" | "rules";
  voice: "openrouter" | "placeholders";
  pictures: "openrouter" | "cloudflare" | "placeholders";
}

/** What the reader should know: who makes the voice and pictures, the choices, and the limits. */
export interface ServiceInfo {
  ai_status: "ok" | "daily_limit" | "unavailable"; // whether voice and pictures can be made now (unavailable: a key refused)
  services: Services;
  max_upload_mb: number;
  max_pages: number;
  daily_limit_usd: number;
  sample_book_id: string | null;
  samples: { id: string; title: string; language: string }[]; // one in English, two in Bangla
  picture_styles: string[]; // the styles offered, e.g. pencil, color, color-hd, ghibli
  picture_words: number[]; // how often a new picture may appear, in words (0: Auto)
  default_style: string;
  default_words: number;
}

export interface SceneImage {
  scene_id: string;
  first_sentence: number;
  last_sentence: number;
  status: "pending" | "ready" | "failed" | "skipped";
  image_url: string | null;
  caption: string;
  error_code: string | null;
  prompt: string; // what the image model is asked for
  rendered_style: string | null; // the drawing style accepted: another than the plan's when the first was declined
  drawn_at: string | null; // when the picture was drawn (UTC)
  draw_seconds: number | null; // how long the image model took, declined tries included
}

export interface SketchManifest {
  schema_version: string;
  book_id: string;
  section_id: string;
  brain: string;
  provider: string;
  style: string;
  words: number;
  status: "idle" | "planning" | "drawing" | "complete" | "failed";
  error_code: string | null;
  scenes: SceneImage[];
  sketches_used: number;
  sketch_budget: number;
}

export interface Reading {
  book_id: string;
  schema_version: string;
  pages: ReadingPage[];
  sections: Section[];
  paragraphs: Paragraph[];
  sentences: Sentence[];
  warnings: string[];
  start_section: string | null;
  narration_style: "story" | "plain";
  language?: string; // "en", "bn", …
}
