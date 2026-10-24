/**
 * The single playback controller: audio drives the position.
 *
 * Narration is one clip per sentence. The current clip's end starts the pause
 * that follows it, and the pause's end starts the next clip, so the highlight
 * always follows the audio. Pauses shrink with the playback speed, the same
 * way the speech does. A clip that is not prepared yet puts playback in a
 * waiting state until it arrives; a clip that failed is skipped.
 *
 * Two audio elements take turns: while one plays, the other loads the next
 * clip, so a sentence starts without waiting for its file to load.
 */

export interface Clip {
  order: number; // sentence order in the book
  url: string | null; // null until the clip is ready
  status: "pending" | "ready" | "failed" | "skipped";
  pauseAfter: number; // seconds
}

/** The parts of HTMLAudioElement the controller uses, so tests can supply a fake. */
export interface AudioLike {
  src: string;
  preload: string;
  playbackRate: number;
  currentTime: number;
  play(): Promise<void>;
  pause(): void;
  addEventListener(type: "ended" | "error", listener: () => void): void;
  removeEventListener(type: "ended" | "error", listener: () => void): void;
}

export type PlaybackStatus = "idle" | "playing" | "paused" | "waiting" | "ended";

export interface PlaybackState {
  status: PlaybackStatus;
  index: number; // position in the clip list
}

export class PlaybackController {
  private clips: Clip[] = [];
  private index = 0;
  private status: PlaybackStatus = "idle";
  private rate = 1;
  private betweenClips = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private generation = 0; // bumps on every start or seek; stale events are ignored
  private current: AudioLike; // the element playing (or about to play) the current clip
  private standby: AudioLike; // the element loading the next clip
  private readonly listeners: [AudioLike, () => void][] = [];

  constructor(
    createAudio: () => AudioLike,
    private readonly onChange: (state: PlaybackState) => void,
  ) {
    this.current = createAudio();
    this.standby = createAudio();
    for (const audio of [this.current, this.standby]) {
      audio.preload = "auto";
      const finished = () => {
        if (audio === this.current) this.onEnded();
      };
      audio.addEventListener("ended", finished);
      audio.addEventListener("error", finished); // an unplayable clip is skipped
      this.listeners.push([audio, finished]);
    }
  }

  get state(): PlaybackState {
    return { status: this.status, index: this.index };
  }

  /** Replace the clip list (same section, newer readiness) without disturbing playback. */
  setClips(clips: Clip[]): void {
    this.clips = clips;
    if (this.status === "waiting") this.start();
    else if (this.status === "playing") this.prefetch();
  }

  play(fromIndex = this.index): void {
    if (fromIndex !== this.index || this.status === "idle" || this.status === "ended") {
      this.seek(fromIndex, true);
      return;
    }
    if (this.status === "paused") {
      if (this.betweenClips) this.advance();
      else {
        this.set("playing");
        this.current.play().catch(this.blocked(this.generation));
      }
    }
  }

  pause(): void {
    if (this.status !== "playing" && this.status !== "waiting") return;
    this.clearTimer();
    this.current.pause();
    this.set("paused");
  }

  /** Move to a clip; keeps playing if playing (or `andPlay`), otherwise just moves. */
  seek(index: number, andPlay = false): void {
    const keepPlaying = andPlay || this.status === "playing" || this.status === "waiting";
    this.generation += 1;
    this.clearTimer();
    this.betweenClips = false;
    this.current.pause();
    this.index = Math.max(0, Math.min(index, this.clips.length));
    if (keepPlaying) this.start();
    else this.set(this.status === "idle" ? "idle" : "paused");
  }

  setRate(rate: number): void {
    this.rate = rate;
    this.current.playbackRate = rate;
  }

  destroy(): void {
    this.generation += 1;
    this.clearTimer();
    for (const [audio, finished] of this.listeners) {
      audio.pause();
      audio.removeEventListener("ended", finished);
      audio.removeEventListener("error", finished);
    }
  }

  private start(): void {
    while (this.index < this.clips.length && ["failed", "skipped"].includes(this.clips[this.index].status)) {
      this.index += 1;
    }
    const clip = this.clips[this.index];
    if (!clip) {
      this.set("ended");
      return;
    }
    if (!clip.url) {
      this.set("waiting");
      return;
    }
    const generation = ++this.generation;
    this.betweenClips = false;
    if (this.standby.src.endsWith(clip.url)) {
      [this.current, this.standby] = [this.standby, this.current]; // already loaded
    } else {
      this.current.src = clip.url;
    }
    this.current.currentTime = 0;
    this.current.playbackRate = this.rate;
    this.set("playing");
    this.current.play().catch(this.blocked(generation));
    this.prefetch();
  }

  private onEnded(): void {
    if (this.status !== "playing") return;
    const generation = this.generation;
    const clip = this.clips[this.index];
    this.betweenClips = true;
    this.timer = setTimeout(
      () => {
        if (generation === this.generation) this.advance();
      },
      ((clip?.pauseAfter ?? 0) * 1000) / this.rate,
    );
  }

  private advance(): void {
    this.timer = null;
    this.index += 1;
    this.start();
  }

  private prefetch(): void {
    const upcoming = this.clips.slice(this.index + 1).find((clip) => clip.status !== "failed" && clip.status !== "skipped");
    if (upcoming?.url && !this.standby.src.endsWith(upcoming.url)) this.standby.src = upcoming.url;
  }

  /** The browser refused to start audio (e.g. no user gesture yet): show as paused. */
  private blocked(generation: number) {
    return () => {
      if (generation === this.generation && this.status === "playing") this.set("paused");
    };
  }

  private clearTimer(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }

  private set(status: PlaybackStatus): void {
    this.status = status;
    this.onChange(this.state);
  }
}
