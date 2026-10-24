import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { type Clip, PlaybackController, type PlaybackState } from "./playback";
import type { NarrationManifest } from "../lib/types";

// Sentences before a section's end when the next section's voice is asked for: two or three minutes of a story told
// slowly, time for a narrator that reads a minute at a time, without paying for chapters never reached.
const PREPARE_AHEAD = 30;

interface Options {
  bookId: string;
  sectionId: string | undefined;
  nextSectionId: string | undefined;
  activeOrder: number;
  /** Playback reached a sentence: move the highlight (not a seek). */
  onPosition: (order: number) => void;
  /** The section finished playing. Returns the sentence to continue at, if any. */
  onSectionEnd: () => number | null;
  /** Sentences that must not play yet (their picture is still being drawn). */
  blocked?: (order: number) => boolean;
  /** Changes whenever the answers of `blocked` may have changed. */
  blockedKey?: string;
}

/**
 * Narration for the section being read. Nothing is prepared until the first
 * Play; after that, the current section's manifest is requested and polled
 * until every clip is ready, and the next section is prepared ahead.
 */
export function useNarration(options: Options) {
  const { bookId, sectionId, nextSectionId, activeOrder, onPosition, onSectionEnd, blocked, blockedKey } = options;
  const [engaged, setEngaged] = useState(false);
  const [cancelled, setCancelled] = useState(false);
  const [manifest, setManifest] = useState<NarrationManifest | null>(null);
  const [playback, setPlayback] = useState<PlaybackState>({ status: "idle", index: 0 });
  const [rate, setRateState] = useState(1);
  const [error, setError] = useState<string | null>(null);

  const controller = useRef<PlaybackController | null>(null);
  const clips = useRef<Clip[]>([]);
  const clipsSection = useRef<string | null>(null);
  const startAt = useRef<number | null>(null); // sentence to play from once its section's clips arrive
  const preparedAhead = useRef<string | null>(null);
  const active = useRef(activeOrder);
  active.current = activeOrder;
  const isBlocked = useRef(blocked);
  isBlocked.current = blocked;

  useEffect(() => {
    const instance = new PlaybackController(() => new Audio(), setPlayback);
    controller.current = instance;
    return () => instance.destroy();
  }, []);

  // Request the section's narration, then poll until every clip is ready.
  useEffect(() => {
    if (!engaged || cancelled || !sectionId) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let retryDelay = 2000;
    const poll = async (first: boolean) => {
      try {
        const next = first
          ? await api.requestNarration(bookId, sectionId, active.current)
          : await api.narration(bookId, sectionId);
        if (stopped) return;
        setManifest(next);
        setError(null);
        retryDelay = 2000;
        if (next.preparing) timer = setTimeout(() => poll(false), 1000); // the server is still working
      } catch (e) {
        if (stopped) return;
        setError((e as Error).message);
        timer = setTimeout(() => poll(first), retryDelay); // networks drop requests; keep trying, more slowly
        retryDelay = Math.min(retryDelay * 2, 15000);
      }
    };
    poll(true);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [engaged, cancelled, bookId, sectionId]);

  const shown = manifest;

  // Give the controller the section's clips as they become ready.
  useEffect(() => {
    const player = controller.current;
    if (!player || !shown || shown.section_id !== sectionId) return;
    const manifest = shown;
    clips.current = manifest.segments.map((segment) => {
      const held = segment.status === "ready" && (isBlocked.current?.(segment.order) ?? false);
      return {
        order: segment.order,
        url: held ? null : segment.audio_url, // a held clip looks unprepared, so playback waits for it
        status: held ? "pending" : segment.status,
        pauseAfter: segment.pause_after,
      };
    });
    const newSection = clipsSection.current !== manifest.section_id;
    clipsSection.current = manifest.section_id;
    player.setClips(clips.current);
    if (startAt.current !== null) {
      const index = clips.current.findIndex((clip) => clip.order >= startAt.current!);
      startAt.current = null;
      player.play(Math.max(0, index));
    } else if (newSection) {
      player.seek(Math.max(0, clips.current.findIndex((clip) => clip.order >= active.current)));
    }
  }, [manifest, sectionId, blockedKey]);

  // Playback moves the highlight; reaching the end continues, unless the budget stopped narration.
  useEffect(() => {
    const { status, index } = playback;
    if (status === "playing" || status === "waiting") {
      const clip = clips.current[index];
      if (clip) onPosition(clip.order);
      if (nextSectionId && index >= clips.current.length - PREPARE_AHEAD && preparedAhead.current !== nextSectionId) {
        preparedAhead.current = nextSectionId;
        api.requestNarration(bookId, nextSectionId, undefined).catch(() => {});
      }
    } else if (status === "ended" && !clips.current.some((clip) => clip.status === "skipped")) {
      startAt.current = onSectionEnd(); // plays once the next section's clips arrive
    }
  }, [playback]); // only playback changes matter here; the callbacks are fresh each render

  const inCurrentSection = (order: number) =>
    clipsSection.current === sectionId ? clips.current.findIndex((clip) => clip.order === order) : -1;

  const playing = playback.status === "playing" || playback.status === "waiting";

  const toggle = useCallback(() => {
    const player = controller.current;
    if (!player) return;
    if (playing) {
      player.pause();
      return;
    }
    const index = inCurrentSection(active.current);
    if (index >= 0) player.play(index);
    else startAt.current = active.current; // plays when this section's clips arrive
    setEngaged(true);
    setCancelled(false);
  }, [playing, sectionId]);

  /** The reader moved (button, key, click, section picker). Playback follows if it is running. */
  const seekTo = useCallback(
    (order: number) => {
      const player = controller.current;
      if (!player) return;
      const index = inCurrentSection(order);
      if (index >= 0) player.seek(index);
      else if (playing) {
        player.pause();
        startAt.current = order;
      }
    },
    [playing, sectionId],
  );

  const setRate = useCallback((value: number) => {
    controller.current?.setRate(value);
    setRateState(value);
  }, []);

  const cancel = useCallback(async () => {
    controller.current?.pause();
    setCancelled(true); // stops polling; Play asks again and resumes preparation
    await api.cancel(bookId).catch(() => {});
  }, [bookId]);

  const current = shown && shown.section_id === sectionId ? shown : null;
  return {
    engaged,
    cancelled,
    playing,
    status: playback.status,
    manifest: current,
    rate,
    error,
    toggle,
    seekTo,
    setRate,
    cancel,
  };
}
