import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { PictureChoice } from "./pictures";
import type { SketchManifest } from "../lib/types";

interface Options {
  bookId: string;
  sectionId: string | undefined;
  nextSectionId: string | undefined;
  activeOrder: number; // the sentence being read: drawing starts there
  choice: PictureChoice; // the picture style and how often pictures change
  enabled: boolean; // pictures are shown
}

// Sentences before a section's end when the next section's pictures are asked for: two or three minutes of a story
// told slowly, time to plan the next chapter and draw its first pictures, without paying for chapters never reached.
export const PREPARE_AHEAD = 30;

/**
 * Sketches for the section being read: requested as soon as the section is
 * open (drawing starts at the reader's sentence), polled while scenes are
 * planned and drawn, and the next section is requested when the reader is
 * near this one's end, so pictures stay ahead of the reader.
 */
export function useSketches({ bookId, sectionId, nextSectionId, activeOrder, choice, enabled }: Options) {
  const { style, words } = choice;
  const [manifest, setManifest] = useState<SketchManifest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const preparedAhead = useRef<string | null>(null);
  const active = useRef(activeOrder);
  active.current = activeOrder;

  useEffect(() => {
    if (!enabled || !sectionId) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let retryDelay = 2000;
    const poll = async (first: boolean) => {
      try {
        const next = first
          ? await api.requestSketches(bookId, sectionId, { style, words }, active.current)
          : await api.sketches(bookId, sectionId, { style, words });
        if (stopped) return;
        setManifest(next);
        setError(null);
        retryDelay = 2000;
        if (next.status === "planning" || next.status === "drawing") {
          timer = setTimeout(() => poll(false), 1500);
        } else if (next.status === "failed" && next.error_code === "plan_failed") {
          timer = setTimeout(() => poll(true), 60000); // this site's AI could not plan it just now: asked again
        }
      } catch (e) {
        if (stopped) return;
        setError((e as Error).message);
        timer = setTimeout(() => poll(first), retryDelay);
        retryDelay = Math.min(retryDelay * 2, 15000);
      }
    };
    poll(true);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [enabled, bookId, sectionId, nextSectionId, style, words]);

  // Near this section's end, the next section's pictures are asked for.
  const lastSentence = manifest && manifest.section_id === sectionId ? manifest.scenes.at(-1)?.last_sentence : undefined;
  useEffect(() => {
    if (!enabled || !nextSectionId || lastSentence === undefined || activeOrder < lastSentence - PREPARE_AHEAD) return;
    const key = `${nextSectionId}/${style}/${words}`;
    if (preparedAhead.current === key) return;
    preparedAhead.current = key;
    api.requestSketches(bookId, nextSectionId, { style, words }, undefined).catch(() => {});
  }, [enabled, bookId, nextSectionId, lastSentence, activeOrder, style, words]);

  const current = manifest && manifest.section_id === sectionId && manifest.style === style && manifest.words === words;
  return { manifest: current ? manifest : null, error };
}

/** Whether a sentence's picture is still on its way (so "wait for each picture" should hold it). */
export function pictureOutstanding(manifest: SketchManifest | null, order: number): boolean {
  if (!manifest) return true;
  if (manifest.status === "failed") return false;
  const scene = manifest.scenes.find((s) => s.first_sentence <= order && order <= s.last_sentence);
  if (!scene) return manifest.status === "planning";
  return scene.status === "pending";
}
