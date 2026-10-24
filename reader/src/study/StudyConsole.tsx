import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Logo } from "../site/Logo";
import { frequencyName, styleName } from "../reading/pictures";
import type { Book, PassageReadiness, ScheduledSession, StudyOverview, StudySession } from "../lib/types";

const PARTICIPANT_KEY = "imagine-reader:study-participant";
const REFRESH_MS = 5000; // while anything is still being prepared

function storedParticipant(): number | null {
  try {
    return Number(window.sessionStorage.getItem(PARTICIPANT_KEY)) || null;
  } catch {
    return null;
  }
}

function storeParticipant(value: number | null): void {
  try {
    if (value) window.sessionStorage.setItem(PARTICIPANT_KEY, String(value));
    else window.sessionStorage.removeItem(PARTICIPANT_KEY);
  } catch {
    // a convenience only: the experimenter types the number again
  }
}

/** Whether a passage's voice and the pictures of one setting are all prepared. */
function prepared(passage: PassageReadiness | undefined, style: string, words: number): boolean {
  const plan = passage?.plans.find((p) => p.style === style && p.words === words);
  return !!(plan?.complete && passage?.narration?.complete);
}

function Readiness({ ready }: { ready: boolean }) {
  return <span className={`study-ready${ready ? "" : " not"}`}>{ready ? "Prepared" : "Not prepared yet"}</span>;
}

/**
 * The experimenter's page in the study build (#/study): starts each participant's
 * practice and four sessions from the schedule, shows how far the passages are
 * prepared, and starts benchmark runs of the live engine.
 */
export function StudyConsole({ onStart }: { onStart: (session: StudySession) => void }) {
  const [overview, setOverview] = useState<StudyOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [participant, setParticipant] = useState<number | null>(storedParticipant);
  const [schedule, setSchedule] = useState<ScheduledSession[] | null>(null);
  const [books, setBooks] = useState<Book[]>([]);
  const [run, setRun] = useState({ book: "", style: "pencil", words: 25, label: "" });
  const [starting, setStarting] = useState(false);

  const unfinished =
    !overview || [overview.practice, ...overview.passages].some((p) => p.status !== "missing" && !p.plans.every((x) => x.complete));

  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    const load = () =>
      api.studyOverview().then(
        (next) => {
          if (stopped) return;
          setOverview(next);
          setError(null);
        },
        (e: Error) => !stopped && setError(e.message),
      );
    load();
    if (unfinished) timer = window.setInterval(load, REFRESH_MS);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [unfinished]);

  useEffect(() => {
    api.listBooks().then((list) => {
      const ready = list.filter((book) => book.status === "ready");
      setBooks(ready);
      setRun((current) => (current.book || !ready.length ? current : { ...current, book: ready[0].id }));
    }, () => {});
  }, []);

  useEffect(() => {
    storeParticipant(participant);
    setSchedule(null);
    if (!participant) return;
    let current = true;
    api.studySchedule(participant).then(
      (next) => current && setSchedule(next),
      (e: Error) => current && setError(e.message),
    );
    return () => {
      current = false;
    };
  }, [participant]);

  const start = async (body: Parameters<typeof api.startStudySession>[0]) => {
    setStarting(true);
    setError(null);
    try {
      onStart(await api.startStudySession(body));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStarting(false);
    }
  };

  const passage = (letter: string) => overview?.passages.find((p) => p.letter === letter);
  const practiceReady = !!overview && prepared(overview.practice, overview.practice.style, overview.practice.words);
  const block = schedule?.[0]?.block;

  return (
    <main className="study-console">
      <header className="study-header">
        <Logo size="small" />
        <h1>Study console</h1>
      </header>
      {overview && (
        <p className="study-build">
          Build <strong>{overview.build}</strong> · Narration: {overview.narration} · Pictures: {overview.pictures} ·
          Visual bible: {overview.visual_bible}
        </p>
      )}
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}

      <section>
        <h2>Start a session</h2>
        <label className="study-field">
          Participant number
          <input
            type="number"
            min={1}
            max={999}
            value={participant ?? ""}
            onChange={(event) => setParticipant(Number(event.target.value) || null)}
          />
        </label>
        {participant && schedule && (
          <>
            <p className="study-note">
              P{String(participant).padStart(3, "0")} is in block {block}, slot {schedule[0].slot}
              {block && block % 2 === 0 ? "; this block runs the schedule's sessions in reverse" : ""}.
            </p>
            <ol className="study-sessions">
              <li>
                <button disabled={starting || !practiceReady} onClick={() => start({ kind: "practice", participant })}>
                  Practice
                </button>
                <span>
                  The Lantern Keeper · {styleName("pencil", true)} · {frequencyName(25)}
                </span>
                <Readiness ready={practiceReady} />
              </li>
              {schedule.map((planned) => {
                const ready = prepared(passage(planned.passage), planned.style, planned.words);
                return (
                  <li key={planned.session}>
                    <button
                      className="primary"
                      disabled={starting || !ready}
                      onClick={() => start({ kind: "study", participant, session: planned.session })}
                    >
                      Session {planned.session}
                    </button>
                    <span>
                      Passage {planned.passage} · {styleName(planned.style, true)} · {frequencyName(planned.words)}
                    </span>
                    <Readiness ready={ready} />
                  </li>
                );
              })}
            </ol>
          </>
        )}
      </section>

      <section>
        <h2>Passages</h2>
        <table className="study-table">
          <thead>
            <tr>
              <th scope="col">Passage</th>
              <th scope="col">Status</th>
              <th scope="col">Voice</th>
              <th scope="col">Picture settings ready</th>
              <th scope="col">Declined scenes</th>
              <th scope="col">Drawn in watercolour instead</th>
            </tr>
          </thead>
          <tbody>
            {overview &&
              [{ ...overview.practice, letter: "Practice" }, ...overview.passages].map((item) => (
                <tr key={item.letter}>
                  <th scope="row">{item.letter}</th>
                  <td>{item.status}</td>
                  <td>{item.narration ? `${item.narration.ready} / ${item.narration.planned}` : "—"}</td>
                  <td>
                    {item.plans.length ? `${item.plans.filter((p) => p.complete).length} / ${item.plans.length}` : "—"}
                  </td>
                  <td>{item.plans.reduce((sum, p) => sum + p.refused, 0)}</td>
                  <td>{item.plans.reduce((sum, p) => sum + p.fallbacks, 0)}</td>
                </tr>
              ))}
          </tbody>
        </table>
        {unfinished && overview && (
          <p className="study-note">
            Prepare what is missing on the server with <code>python -m imagine_reader_server.study prepare</code>; this
            page updates by itself.
          </p>
        )}
      </section>

      <section>
        <h2>Benchmark run</h2>
        <p className="study-note">
          Reads any book in this browser with pictures drawn live, and may change them while reading. Clear a book's
          pictures first with <code>study clear-pictures BOOK_ID</code>.
        </p>
        <div className="study-run">
          <label className="study-field">
            Book
            <select value={run.book} onChange={(event) => setRun({ ...run, book: event.target.value })}>
              {books.map((book) => (
                <option key={book.id} value={book.id}>
                  {book.filename} ({book.id.slice(0, 8)}…)
                </option>
              ))}
            </select>
          </label>
          <label className="study-field">
            Style
            <select value={run.style} onChange={(event) => setRun({ ...run, style: event.target.value })}>
              {(overview?.styles ?? []).map((style) => (
                <option key={style} value={style}>
                  {styleName(style, true)}
                </option>
              ))}
            </select>
          </label>
          <label className="study-field">
            New picture
            <select value={run.words} onChange={(event) => setRun({ ...run, words: Number(event.target.value) })}>
              {(overview?.frequencies ?? []).map((words) => (
                <option key={words} value={words}>
                  {frequencyName(words)}
                </option>
              ))}
            </select>
          </label>
          <label className="study-field">
            Run name
            <input
              value={run.label}
              maxLength={40}
              placeholder="chapter 2, run 1"
              onChange={(event) => setRun({ ...run, label: event.target.value })}
            />
          </label>
          <button
            disabled={starting || !run.book}
            onClick={() =>
              start({ kind: "benchmark", book_id: run.book, style: run.style, words: run.words, label: run.label || undefined })
            }
          >
            Start run
          </button>
        </div>
      </section>

      <section>
        <h2>The log</h2>
        <p className="study-note">
          Each participant's events are written on the server to <code>data/study/events/P001.jsonl</code> and so on;
          benchmark runs to <code>benchmark-&lt;run name&gt;.jsonl</code>. See docs/study.md for every field.
        </p>
      </section>
    </main>
  );
}
