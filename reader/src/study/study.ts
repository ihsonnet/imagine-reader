import { useCallback, useEffect, useMemo, useRef } from "react";
import { api } from "../lib/api";
import type { PlaybackStatus } from "../reading/playback";
import type { SceneImage, SketchManifest, StudySession } from "../lib/types";
import { pictureOutstanding } from "../reading/useSketches";

/**
 * The study build's interaction log (docs/study.md). The reader records what a
 * participant does; the server stamps each event with the participant, the
 * condition, the build and the models, and appends it to their log.
 */

export type StudyEventType =
  | "session_start"
  | "session_end"
  | "sentence_start"
  | "picture_shown"
  | "picture_ready"
  | "narration_wait"
  | "pause"
  | "resume"
  | "replay"
  | "skip"
  | "pictures_hidden"
  | "pictures_shown"
  | "setting_change";

export interface StudyEvent {
  type: StudyEventType;
  narration_ms: number; // the narration clock when it happened
  at: string; // this browser's clock, ISO 8601
  data: Record<string, unknown>;
}

/**
 * How long narration has played in this session. It runs while the voice is
 * playing, the pauses between sentences included, and stops while the reader
 * has paused or narration waits for a clip or a picture, so the same sentence
 * falls at the same time for every participant.
 */
export class NarrationClock {
  private total = 0;
  private since: number | null = null;

  constructor(private readonly now: () => number = () => performance.now()) {}

  set(running: boolean): void {
    if (running && this.since === null) this.since = this.now();
    else if (!running && this.since !== null) {
      this.total += this.now() - this.since;
      this.since = null;
    }
  }

  get ms(): number {
    return Math.round(this.total + (this.since === null ? 0 : this.now() - this.since));
  }
}

const BATCH = 500; // the most events the server takes at once

/** One session's events, kept in order and sent in batches; a batch that fails is sent again later. */
export class StudyLog {
  private queue: StudyEvent[] = [];
  private sending = false;

  constructor(
    private readonly send: (events: StudyEvent[]) => Promise<void>,
    readonly clock = new NarrationClock(),
    private readonly wallClock: () => Date = () => new Date(),
  ) {}

  add(type: StudyEventType, data: Record<string, unknown> = {}): void {
    this.queue.push({ type, narration_ms: this.clock.ms, at: this.wallClock().toISOString(), data });
  }

  get waiting(): number {
    return this.queue.length;
  }

  async flush(): Promise<void> {
    if (this.sending) return;
    this.sending = true;
    try {
      while (this.queue.length) {
        const batch = this.queue.slice(0, BATCH);
        await this.send(batch);
        this.queue.splice(0, batch.length);
      }
    } catch {
      // kept, in order, for the next flush
    } finally {
      this.sending = false;
    }
  }

  /** The page is closing: hand what is left to the browser to deliver. */
  beacon(url: string): void {
    if (!this.queue.length || typeof navigator.sendBeacon !== "function") return;
    const body = new Blob([JSON.stringify({ events: this.queue.slice(0, BATCH) })], { type: "application/json" });
    if (navigator.sendBeacon(url, body)) this.queue.splice(0, BATCH);
  }
}

const FLUSH_EVERY_MS = 2000;

/** Whether a picture was drawn before the session started (prepared ahead) rather than while it ran (live). */
export function drawnBefore(scene: SceneImage, session: StudySession | undefined): boolean {
  if (!scene.drawn_at || !session) return true; // found already drawn, under the same request
  return Date.parse(scene.drawn_at) < Date.parse(session.created_at);
}

/** The scene a sentence belongs to, in a set of pictures. */
function sceneAt(pictures: SketchManifest | null, order: number): SceneImage | undefined {
  return pictures?.scenes.find((scene) => scene.first_sentence <= order && order <= scene.last_sentence);
}

interface Options {
  session: StudySession | undefined;
  open: boolean; // the passage is loaded and on screen
  total: number; // sentences in the book
  order: number | undefined; // the sentence the reader is on
  status: PlaybackStatus;
  pictures: SketchManifest | null; // the pictures being shown
  holdForPictures: boolean; // narration waits for a picture that is not ready
}

/**
 * Logs a session's events while a study session is open; does nothing outside one.
 * Returns the calls the reader makes for what only it sees: controls, the picture shown, the end.
 */
export function useStudyLog({ session, open, total, order, status, pictures, holdForPictures }: Options) {
  const log = useMemo(() => (session ? new StudyLog((events) => api.logStudyEvents(session.id, events)) : null), [session]);
  const started = useRef<number | null>(null); // wall-clock time of session_start
  const ended = useRef(false);
  const heard = useRef(new Set<number>());
  const shown = useRef(0);
  const lastSentence = useRef<number | null>(null);
  const waiting = useRef<{ at: number; sentence: number; reason: string } | null>(null);
  const reported = useRef(new Set<string>()); // live pictures already logged as ready
  const current = useRef(order);
  current.current = order;
  const setKey = pictures ? `${pictures.section_id}/${pictures.style}/${pictures.words}` : "";

  // Start once the passage is open; send every couple of seconds and when the page closes.
  useEffect(() => {
    if (!log || !session || !open) return;
    if (started.current === null) {
      started.current = Date.now();
      log.add("session_start", { sentences: total, first_sentence: current.current ?? 0 });
    }
    const timer = window.setInterval(() => void log.flush(), FLUSH_EVERY_MS);
    const leave = () => log.beacon(api.studyEventsUrl(session.id));
    window.addEventListener("pagehide", leave);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("pagehide", leave);
      void log.flush();
    };
  }, [log, session, open, total]);

  // The narration clock, and how long narration waited.
  useEffect(() => {
    if (!log) return;
    log.clock.set(status === "playing");
    const sentence = current.current ?? 0;
    if (status === "waiting" && !waiting.current) {
      const forPicture = holdForPictures && pictureOutstanding(pictures, sentence);
      waiting.current = { at: Date.now(), sentence, reason: forPicture ? "picture" : "voice" };
    } else if (status !== "waiting" && waiting.current) {
      const { at, sentence: heldAt, reason } = waiting.current;
      waiting.current = null;
      log.add("narration_wait", {
        sentence: heldAt,
        reason,
        started_at: new Date(at).toISOString(),
        duration_ms: Date.now() - at,
      });
    }
  }, [log, status]);

  // Each sentence as narration reaches it.
  useEffect(() => {
    if (!log || order === undefined || status !== "playing" || lastSentence.current === order) return;
    lastSentence.current = order;
    heard.current.add(order);
    log.add("sentence_start", { sentence: order, scene: sceneAt(pictures, order)?.scene_id ?? null });
  }, [log, order, status]);

  // Each picture drawn while the session ran (live), once, when the reader first learns it is ready.
  useEffect(() => {
    if (!log || !pictures) return;
    for (const scene of pictures.scenes) {
      const id = `${setKey}/${scene.scene_id}`;
      if (scene.status === "ready" && !drawnBefore(scene, session) && !reported.current.has(id)) {
        reported.current.add(id);
        log.add("picture_ready", {
          scene: scene.scene_id,
          first_sentence: scene.first_sentence,
          style: pictures.style,
          words: pictures.words,
          rendered_style: scene.rendered_style,
          drawn_at: scene.drawn_at,
          draw_seconds: scene.draw_seconds,
        });
      }
    }
  }, [log, pictures, setKey, session]);

  const note = useCallback(
    (type: StudyEventType, data: Record<string, unknown> = {}) => log?.add(type, { sentence: current.current, ...data }),
    [log],
  );

  const pictureShown = useCallback(
    (scene: SceneImage) => {
      if (!log || !pictures) return;
      shown.current += 1;
      log.add("picture_shown", {
        picture: scene.image_url?.split("/").pop() ?? null,
        scene: scene.scene_id,
        scene_first_sentence: scene.first_sentence,
        sentence: current.current,
        source: drawnBefore(scene, session) ? "cache" : "live",
        style: pictures.style,
        words: pictures.words,
        rendered_style: scene.rendered_style,
        drawn_at: scene.drawn_at,
      });
    },
    [log, pictures, session],
  );

  /** Close the session's log: once, however the session ends. */
  const end = useCallback(
    async (reason: "finished" | "ended") => {
      if (!log || ended.current) return;
      ended.current = true;
      log.add("session_end", {
        reason,
        sentence: current.current,
        sentences_heard: heard.current.size,
        pictures_shown: shown.current,
        duration_ms: started.current === null ? 0 : Date.now() - started.current,
      });
      await log.flush();
    },
    [log],
  );

  return { active: !!log, note, pictureShown, end };
}
