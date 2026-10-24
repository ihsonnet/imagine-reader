import { describe, expect, it } from "vitest";
import type { SceneImage, SketchManifest } from "../lib/types";
import { pictureOutstanding } from "./useSketches";

const scene = (first: number, last: number, status: SceneImage["status"]): SceneImage => ({
  scene_id: `scene-${first}`,
  first_sentence: first,
  last_sentence: last,
  status,
  image_url: status === "ready" ? `/img/${first}` : null,
  caption: "A scene.",
  error_code: null,
  prompt: "Pencil sketch. Scene: a scene.",
  rendered_style: null,
  drawn_at: null,
  draw_seconds: null,
});

const manifest = (status: SketchManifest["status"], scenes: SceneImage[]): SketchManifest => ({
  schema_version: "sketches-0.1",
  book_id: "b",
  section_id: "sec-001",
  brain: "heuristic-4-w25",
  provider: "mock-sketch-2",
  style: "pencil",
  words: 25,
  status,
  error_code: null,
  scenes,
  sketches_used: 0,
  sketch_budget: 60,
});

describe("pictureOutstanding", () => {
  it("holds sentences until their scene's picture is drawn", () => {
    const drawing = manifest("drawing", [scene(0, 4, "ready"), scene(5, 9, "pending")]);
    expect(pictureOutstanding(drawing, 3)).toBe(false);
    expect(pictureOutstanding(drawing, 7)).toBe(true);
  });

  it("holds everything while scenes are being planned or not yet requested", () => {
    expect(pictureOutstanding(null, 0)).toBe(true);
    expect(pictureOutstanding(manifest("planning", []), 0)).toBe(true);
  });

  it("never holds for pictures that will not come", () => {
    expect(pictureOutstanding(manifest("failed", []), 0)).toBe(false);
    const stopped = manifest("complete", [scene(0, 4, "failed"), scene(5, 9, "skipped")]);
    expect(pictureOutstanding(stopped, 2)).toBe(false);
    expect(pictureOutstanding(stopped, 6)).toBe(false);
  });
});
