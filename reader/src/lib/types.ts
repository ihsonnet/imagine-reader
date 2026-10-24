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

/** Who makes what with the keys set: openrouter, gemini, openai, cloudflare; or none yet. */
export interface Services {
  planning: "openrouter" | "openai" | "rules";
  voice: "openrouter" | "gemini" | "openai" | "placeholders" | "none";
  pictures: "cloudflare" | "openrouter" | "openai" | "placeholders";
}

/** What the reader should know: who makes the voice and pictures, the choices, and the limits. */
export interface ServiceInfo {
  ai_status: "ok" | "daily_limit" | "unavailable"; // whether voice and pictures can be made now (unavailable: a key refused)
  services: Services;
  uploads: boolean; // off on a public demo that offers only the samples
  max_upload_mb: number;
  max_pages: number;
  daily_limit_usd: number;
  sample_book_id: string | null;
  samples: { id: string; title: string; language: string }[]; // one in English, two in Bangla
  picture_styles: string[]; // the styles offered, e.g. pencil, color, color-hd, ghibli
  picture_words: number[]; // how often a new picture may appear, in words (0: Auto)
  default_style: string;
  default_words: number;
  study: boolean; // the study build: its console is at #/study
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

/** A study session: its passage and pictures are fixed when it starts (server study.py). */
export interface StudySession {
  id: string;
  kind: "study" | "practice" | "benchmark";
  participant: number | null;
  session: number | null; // 1 to 4, for a study session
  attempt: number; // 2 or more when the same session was started again
  label: string | null; // names a benchmark run
  passage: string; // A to D, "practice", or "benchmark"
  book_id: string;
  style: string;
  words: number;
  block: number | null;
  slot: number | null;
  column: number | null;
  locked: boolean; // the pictures cannot be changed while reading
  created_at: string;
  models: Record<string, string>;
}

/** How far one setting of a passage's pictures is prepared. */
export interface PlanReadiness {
  style: string;
  words: number;
  scenes: number;
  ready: number;
  refused: number; // declined in every style: the previous picture stays on screen
  fallbacks: number; // drawn in another style than asked
  complete: boolean;
}

export interface PassageReadiness {
  book_id: string;
  status: string; // missing, queued, processing, failed, or ready
  narration: { ready: number; planned: number; complete: boolean } | null;
  plans: PlanReadiness[];
}

/** The study console's view of the build and its passages (GET /api/study). */
export interface StudyOverview {
  build: string;
  narration: string;
  pictures: string;
  visual_bible: string;
  styles: string[];
  frequencies: number[];
  practice: PassageReadiness & { passage: string; style: string; words: number };
  passages: (PassageReadiness & { letter: string })[];
}

/** One of a participant's four sessions, from the schedule. */
export interface ScheduledSession {
  session: number;
  passage: string;
  style: string;
  words: number;
  block: number;
  slot: number;
  column: number;
}
