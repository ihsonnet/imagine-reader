import { describe, expect, it } from "vitest";
import { NarrationClock, type StudyEvent, StudyLog } from "./study";

describe("NarrationClock", () => {
  it("runs only while narration plays", () => {
    let now = 0;
    const clock = new NarrationClock(() => now);
    now = 500;
    clock.set(true);
    now = 1700;
    expect(clock.ms).toBe(1200);
    clock.set(false); // paused, or waiting for a clip or a picture
    now = 9000;
    expect(clock.ms).toBe(1200);
    clock.set(true);
    clock.set(true); // saying so twice changes nothing
    now = 9300;
    expect(clock.ms).toBe(1500);
  });
});

describe("StudyLog", () => {
  it("stamps each event with the narration clock and the browser's clock, and sends them in order", async () => {
    let now = 0;
    const clock = new NarrationClock(() => now);
    const sent: StudyEvent[][] = [];
    const log = new StudyLog(
      async (events) => void sent.push(events),
      clock,
      () => new Date("2026-10-10T09:00:00Z"),
    );
    log.add("session_start", { sentences: 55 });
    clock.set(true);
    now = 4300;
    log.add("picture_shown", { scene: "sec-001-scn-002" });
    await log.flush();
    expect(sent).toEqual([
      [
        { type: "session_start", narration_ms: 0, at: "2026-10-10T09:00:00.000Z", data: { sentences: 55 } },
        { type: "picture_shown", narration_ms: 4300, at: "2026-10-10T09:00:00.000Z", data: { scene: "sec-001-scn-002" } },
      ],
    ]);
    expect(log.waiting).toBe(0);
  });

  it("keeps events it could not send, in order, for the next flush", async () => {
    let offline = true;
    const sent: string[] = [];
    const log = new StudyLog(async (events) => {
      if (offline) throw new Error("offline");
      sent.push(...events.map((event) => event.type));
    });
    log.add("pause");
    await log.flush();
    expect(log.waiting).toBe(1);
    log.add("resume");
    offline = false;
    await log.flush();
    expect(sent).toEqual(["pause", "resume"]);
    expect(log.waiting).toBe(0);
  });

  it("sends a long session in batches the server accepts", async () => {
    const sizes: number[] = [];
    const log = new StudyLog(async (events) => void sizes.push(events.length));
    for (let sentence = 0; sentence < 1200; sentence++) log.add("sentence_start", { sentence });
    await log.flush();
    expect(sizes).toEqual([500, 500, 200]);
  });
});
