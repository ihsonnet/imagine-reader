import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { type AudioLike, type Clip, PlaybackController, type PlaybackState } from "./playback";

class FakeAudio implements AudioLike {
  src = "";
  preload = "";
  playbackRate = 1;
  currentTime = 0;
  playing = false;
  loads: string[] = [];
  private listeners: Record<string, (() => void)[]> = { ended: [], error: [] };
  constructor(private readonly played: string[]) {
    return new Proxy(this, {
      set(target, key, value) {
        if (key === "src") target.loads.push(value);
        return Reflect.set(target, key, value);
      },
    });
  }
  play() {
    this.playing = true;
    this.played.push(this.src);
    return Promise.resolve();
  }
  pause() {
    this.playing = false;
  }
  addEventListener(type: "ended" | "error", listener: () => void) {
    this.listeners[type].push(listener);
  }
  removeEventListener(type: "ended" | "error", listener: () => void) {
    this.listeners[type] = this.listeners[type].filter((l) => l !== listener);
  }
  finish() {
    this.playing = false;
    for (const listener of this.listeners.ended) listener();
  }
}

const clip = (order: number, ready = true, pauseAfter = 0.5): Clip => ({
  order,
  url: ready ? `/a/${order}` : null,
  status: ready ? "ready" : "pending",
  pauseAfter,
});

function setup(clips: Clip[]) {
  const played: string[] = [];
  const audios: FakeAudio[] = [];
  const states: PlaybackState[] = [];
  const controller = new PlaybackController(
    () => {
      const audio = new FakeAudio(played);
      audios.push(audio);
      return audio;
    },
    (state) => states.push(state),
  );
  controller.setClips(clips);
  /** The clip that is playing reaches its end. */
  const finish = () => audios.find((audio) => audio.playing)!.finish();
  return { controller, audios, played, finish, states };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("PlaybackController", () => {
  it("plays clips in order, waiting out each pause on the audio's schedule", () => {
    const { controller, played, finish, audios } = setup([clip(10), clip(11), clip(12)]);
    controller.play(0);
    expect(played).toEqual(["/a/10"]);
    expect(controller.state).toEqual({ status: "playing", index: 0 });

    finish();
    vi.advanceTimersByTime(499);
    expect(played).toEqual(["/a/10"]); // still in the pause after the sentence
    vi.advanceTimersByTime(1);
    expect(played).toEqual(["/a/10", "/a/11"]);

    controller.setRate(2); // pauses shrink with the speech
    finish();
    vi.advanceTimersByTime(250);
    expect(played.at(-1)).toBe("/a/12");
    expect(audios.find((audio) => audio.playing)!.playbackRate).toBe(2);

    finish();
    vi.advanceTimersByTime(500);
    expect(controller.state.status).toBe("ended");
  });

  it("loads the next clip on the other element and swaps to it without reloading", () => {
    const { controller, audios, finish } = setup([clip(0), clip(1), clip(2)]);
    controller.play(0);
    expect(audios[0].loads).toEqual(["/a/0"]);
    expect(audios[1].loads).toEqual(["/a/1"]); // fetched ahead while clip 0 plays
    finish();
    vi.advanceTimersByTime(500);
    expect(audios[1].playing).toBe(true);
    expect(audios[1].loads).toEqual(["/a/1"]); // played as loaded
    expect(audios[0].loads).toEqual(["/a/0", "/a/2"]); // and the first element now loads clip 2
  });

  it("pauses and resumes, including between clips", () => {
    const { controller, played, finish } = setup([clip(0), clip(1)]);
    controller.play(0);
    finish();
    controller.pause();
    vi.advanceTimersByTime(5000);
    expect(played).toEqual(["/a/0"]); // nothing moves while paused
    controller.play();
    expect(played).toEqual(["/a/0", "/a/1"]);
  });

  it("seeking jumps at once and ignores the old clip's pending pause", () => {
    const { controller, played, finish } = setup([clip(0), clip(1), clip(2), clip(3)]);
    controller.play(0);
    finish(); // the pause after clip 0 starts
    controller.seek(3);
    vi.advanceTimersByTime(1000);
    expect(played).toEqual(["/a/0", "/a/3"]);
    expect(controller.state).toEqual({ status: "playing", index: 3 });
  });

  it("seeking while paused moves without playing", () => {
    const { controller, played } = setup([clip(0), clip(1), clip(2)]);
    controller.play(0);
    controller.pause();
    controller.seek(2);
    expect(controller.state).toEqual({ status: "paused", index: 2 });
    expect(played).toEqual(["/a/0"]);
  });

  it("waits for a clip that is not ready, then continues", () => {
    const { controller, played, finish, audios } = setup([clip(0), clip(1, false)]);
    controller.play(0);
    expect(audios[1].loads).toEqual([]); // nothing to fetch ahead yet
    finish();
    vi.advanceTimersByTime(500);
    expect(controller.state).toEqual({ status: "waiting", index: 1 });
    controller.setClips([clip(0), clip(1)]);
    expect(played).toEqual(["/a/0", "/a/1"]);
    expect(controller.state.status).toBe("playing");
  });

  it("skips clips that failed", () => {
    const failed: Clip = { order: 1, url: null, status: "failed", pauseAfter: 0.5 };
    const { controller, played, finish } = setup([clip(0), failed, clip(2)]);
    controller.play(0);
    finish();
    vi.advanceTimersByTime(500);
    expect(played).toEqual(["/a/0", "/a/2"]);
  });
});
