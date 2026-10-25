import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, ChevronLeft, ChevronRight, Image, ImageOff, Pause, Play } from "lucide-react";
import { ApiError, api } from "../lib/api";
import { BookNav, bookIdentity } from "./BookNav";
import { Splitter } from "./Splitter";
import { EndOfBook, ReaderFooter } from "../site/project";
import { Logo } from "../site/Logo";
import { paragraphOrders, sectionIndexOf, sentenceAt } from "./geometry";
import type { openPdf, PDFDocumentProxy } from "./pdf";
import { PdfPage } from "./PdfPage";
import { PreparationProgress, readyToOpen } from "../library/Progress";
import { SketchPanel } from "./SketchPanel";
import { TextPanel } from "./TextPanel";
import type { Book, NarrationManifest, Reading, Sentence, ServiceInfo } from "../lib/types";
import { useNarration } from "./useNarration";
import { pictureOutstanding, useSketches } from "./useSketches";
import { PictureSummary, usePictureChoice } from "./pictures";

const SPEEDS = [0.75, 1, 1.25, 1.5, 2];
const positionKey = (bookId: string) => `imagine-reader:position:${bookId}`;

/** Per-browser preferences; storage may be unavailable, which only loses the memory. */
function loadFlag(key: string, fallback: boolean): boolean {
  try {
    const value = window.localStorage.getItem(key);
    return value === null ? fallback : value === "1";
  } catch {
    return fallback;
  }
}

function loadSize(key: string): number | null {
  try {
    return Number(window.localStorage.getItem(key)) || null;
  } catch {
    return null;
  }
}

function saveSize(key: string, value: number | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, String(value));
  } catch {
    // a convenience only
  }
}

function saveFlag(key: string, value: boolean): void {
  try {
    window.localStorage.setItem(key, value ? "1" : "0");
  } catch {
    // a convenience only
  }
}

/** Where this browser left off in the book, if it has read it before. */
function savedPosition(bookId: string): number | null {
  try {
    const value = window.localStorage.getItem(positionKey(bookId));
    return value === null ? null : Number(value) || 0;
  } catch {
    return null; // storage can be unavailable (private windows, blocked site data)
  }
}

/** The first sentence of the section where the story begins, past the title page and notes. */
function storyStart(reading: Reading): number {
  const index = reading.sections.findIndex((section) => section.id === reading.start_section);
  const first = index >= 0 ? reading.sections[index].first_paragraph : 0;
  const paragraph = reading.paragraphs[first]?.id;
  return reading.sentences.find((sentence) => sentence.paragraph_id === paragraph)?.order ?? 0;
}

/** The reader: the book's own page with the sentence highlighted, its voice, and the pictures beside it. */
export function Reader({ bookId, onClose }: { bookId: string; onClose: () => void }) {
  const [book, setBook] = useState<Book | null>(null);
  const [reading, setReading] = useState<Reading | null>(null);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState(() => savedPosition(bookId) ?? 0);
  const [showPictures, setShowPictures] = useState(() => loadFlag("imagine-reader:pictures", true));
  const [waitForPictures, setWaitForPictures] = useState(() => loadFlag("imagine-reader:wait-for-pictures", true));
  const [finished, setFinished] = useState(false); // the last sentence of the book was read aloud
  const [sideWidth, setSideWidth] = useState(() => loadSize("imagine-reader:side-width")); // pixels
  const [pictureHeight, setPictureHeight] = useState(() => loadSize("imagine-reader:picture-height"));
  const grid = useRef<HTMLDivElement>(null);
  const sideColumn = useRef<HTMLDivElement>(null);
  const [navOpen, setNavOpen] = useState(() => loadFlag("imagine-reader:book-nav-open", false)); // folded
  const [narrationStopped, setNarrationStopped] = useState(false); // the reader pressed "Stop preparing"
  const [info, setInfo] = useState<ServiceInfo | null>(null);
  const pictures = usePictureChoice(info);

  useEffect(() => {
    api.info().then(setInfo, () => {}); // the picture choices this site offers
  }, []);

  // Wait until the book is ready, then load its text and PDF together.
  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    let task: ReturnType<typeof openPdf> | null = null;
    async function load() {
      try {
        const current = await api.getBook(bookId);
        if (stopped) return;
        setBook(current);
        if (current.status === "failed") return;
        if (!readyToOpen(current)) {
          timer = window.setTimeout(load, 1500); // the first chapter's voice and pictures are still being made
          return;
        }
        const pdfjs = await import("./pdf"); // PDF.js loads only once a book is opened
        if (stopped) return;
        task = pdfjs.openPdf(api.sourceUrl(bookId));
        const [text, document] = await Promise.all([api.reading(bookId), task.promise]);
        if (stopped) return;
        if (savedPosition(bookId) === null) setActive(storyStart(text)); // a first visit opens at the story
        setReading(text);
        setPdf(document);
      } catch (e) {
        if (stopped) return;
        const gone = e instanceof ApiError && e.status === 404;
        setError(gone ? "This book is no longer here. It may have been deleted." : (e as Error).message);
      }
    }
    load();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      task?.destroy();
    };
  }, [bookId]);

  const total = reading?.sentences.length ?? 0;
  const orders = useMemo(() => paragraphOrders(reading?.paragraphs ?? []), [reading]);
  const byParagraph = useMemo(() => {
    const groups = new Map<string, Sentence[]>();
    for (const sentence of reading?.sentences ?? []) {
      groups.set(sentence.paragraph_id, [...(groups.get(sentence.paragraph_id) ?? []), sentence]);
    }
    return groups;
  }, [reading]);

  const current = reading?.sentences[Math.min(active, total - 1)];
  const sectionIndex = reading && current ? sectionIndexOf(reading.sections, orders.get(current.paragraph_id) ?? 0) : 0;
  const section = reading?.sections[sectionIndex];
  const nextSection = reading?.sections[sectionIndex + 1];

  const firstSentenceOf = useCallback(
    (index: number) => {
      const first = reading?.sections[index]?.first_paragraph ?? Infinity;
      return reading?.sentences.find((s) => (orders.get(s.paragraph_id) ?? -1) >= first);
    },
    [reading, orders],
  );
  const firstOrders = useMemo(
    () => (reading?.sections ?? []).map((_, index) => firstSentenceOf(index)?.order ?? total),
    [reading, firstSentenceOf, total],
  );
  const storyStartIndex = Math.max(0, reading?.sections.findIndex((s) => s.id === reading.start_section) ?? 0);
  const pageCount = reading?.pages.length ?? 1;

  const sketches = useSketches({
    bookId,
    sectionId: reading ? section?.id : undefined,
    nextSectionId: nextSection?.id,
    activeOrder: current?.order ?? 0,
    choice: pictures.choice,
    enabled: showPictures && !narrationStopped,
  });
  const holdForPictures = showPictures && waitForPictures;

  const shownPictures = sketches.manifest;

  const narration = useNarration({
    bookId,
    sectionId: section?.id,
    nextSectionId: nextSection?.id,
    activeOrder: current?.order ?? 0,
    onPosition: setActive,
    onSectionEnd: () => {
      const next = firstSentenceOf(sectionIndex + 1);
      if (next) setActive(next.order);
      else setFinished(true);
      return next?.order ?? null;
    },
    blocked: holdForPictures ? (order) => pictureOutstanding(shownPictures, order) : undefined,
    blockedKey: holdForPictures
      ? `${shownPictures?.status}:${shownPictures?.scenes.map((scene) => scene.status).join(",")}`
      : "off",
  });

  const togglePlay = useCallback(() => {
    if (!narration.playing) setNarrationStopped(false);
    narration.toggle();
  }, [narration.playing, narration.toggle]);

  const cancelPreparation = useCallback(() => {
    setNarrationStopped(true);
    narration.cancel();
  }, [narration.cancel]);

  /** The reader moved; narration follows if it is playing. */
  const go = useCallback(
    (order: number) => {
      const target = Math.max(0, Math.min(order, total - 1));
      setActive(target);
      narration.seekTo(target);
    },
    [total, narration.seekTo],
  );

  /** Turn to a page: reading moves to the first sentence that starts on it, or on the next page with text. */
  const goToPage = useCallback(
    (page: number) => {
      if (!reading) return;
      const wanted = Math.max(1, Math.min(Math.round(page), pageCount));
      const sentence = reading.sentences.find((s) => (s.boxes[0]?.[0] ?? 0) >= wanted) ?? reading.sentences.at(-1);
      if (sentence) go(sentence.order);
    },
    [reading, pageCount, go],
  );
  const shownPage = current?.boxes[0]?.[0] ?? section?.page ?? 1;

  useEffect(() => {
    if (!reading) return; // until the book is loaded, the position is not the reader's yet
    try {
      window.localStorage.setItem(positionKey(bookId), String(active));
    } catch {
      // position memory is a convenience only
    }
  }, [bookId, active, reading]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement;
      if (target.closest("input, select, textarea, button")) return;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") go(active + 1);
      else if (event.key === "ArrowLeft" || event.key === "ArrowUp") go(active - 1);
      else if (event.key === "PageDown") goToPage(shownPage + 1);
      else if (event.key === "PageUp") goToPage(shownPage - 1);
      else if (event.key === " ") togglePlay();
      else return;
      event.preventDefault();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, go, goToPage, shownPage, togglePlay]);

  if (error) return <Message text={error} onClose={onClose} />;
  if (book?.status === "failed") return <Message text={book.error_message ?? "This PDF could not be read."} onClose={onClose} />;
  if (book && !readyToOpen(book)) {
    return (
      <Message text="Getting the first chapter ready: its pictures and its voice." onClose={onClose}>
        <PreparationProgress book={book} />
      </Message>
    );
  }
  if (!book || !reading || !pdf) return <Message text="Loading…" onClose={onClose} />;

  const pageNumber = shownPage;
  const identity = bookIdentity(reading, book.filename);

  return (
    <div className="reader">
      <header className="reader-bar">
        <div className="bar-book">
          <a className="brand" href="#" onClick={(event) => (event.preventDefault(), onClose())}>
            <Logo size="small" />
          </a>
          <button className="quiet" onClick={onClose}>
            <ArrowLeft size={17} aria-hidden="true" /> Library
          </button>
          <h1 title={book.filename}>{identity.title}</h1>
        </div>
        {total > 0 && (
          <div className="playback" role="group" aria-label="Narration">
            <button
              className="primary play"
              onClick={togglePlay}
              aria-label={narration.playing ? "Pause narration" : "Play narration"}
            >
              {narration.playing ? (
                <>
                  <Pause size={17} fill="currentColor" aria-hidden="true" /> Pause
                </>
              ) : (
                <>
                  <Play size={17} fill="currentColor" aria-hidden="true" /> Play
                </>
              )}
            </button>
            <label>
              <span className="visually-hidden">Speed</span>
              <select value={narration.rate} onChange={(event) => narration.setRate(Number(event.target.value))}>
                {SPEEDS.map((speed) => (
                  <option key={speed} value={speed}>
                    {speed}×
                  </option>
                ))}
              </select>
            </label>
            <div className="sentence-nav">
              <button className="icon" aria-label="Previous sentence" onClick={() => go(active - 1)} disabled={active === 0}>
                <ChevronLeft size={18} />
              </button>
              <span aria-live="polite">
                {active + 1} / {total}
              </span>
              <button
                className="icon"
                aria-label="Next sentence"
                onClick={() => go(active + 1)}
                disabled={active >= total - 1}
              >
                <ChevronRight size={18} />
              </button>
            </div>
          </div>
        )}
        <div className="bar-view">
          <button
            className="quiet"
            aria-pressed={showPictures}
            onClick={() => {
              setShowPictures(!showPictures);
              saveFlag("imagine-reader:pictures", !showPictures);
            }}
          >
            {showPictures ? <ImageOff size={17} aria-hidden="true" /> : <Image size={17} aria-hidden="true" />}
            {showPictures ? "Hide pictures" : "Show pictures"}
          </button>
        </div>
      </header>

      {narration.engaged && (
        <NarrationStatus
          manifest={narration.manifest}
          waiting={narration.status === "waiting"}
          cancelled={narration.cancelled}
          error={narration.error}
          onCancel={cancelPreparation}
        />
      )}

      {reading.warnings.length > 0 && (
        <details className="warnings">
          <summary>{reading.warnings.length} note(s) about this PDF</summary>
          <ul>
            {reading.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </details>
      )}

      <div
        ref={grid}
        className={`reader-grid${sideWidth ? " sized" : ""}`}
        style={
          {
            "--side-width": sideWidth ? `${sideWidth}px` : undefined,
            "--sketch-height": pictureHeight ? `${pictureHeight}px` : undefined,
          } as React.CSSProperties
        }
      >
        <div className={`book-column${navOpen ? "" : " nav-closed"}`}>
          {reading.sections.length > 0 && (
            <BookNav
              pdf={pdf}
              coverUrl={book?.cover_url ?? null}
              title={identity.title}
              author={identity.author}
              sections={reading.sections}
              firstOrders={firstOrders}
              sectionIndex={sectionIndex}
              storyStart={storyStartIndex}
              activeOrder={current?.order ?? 0}
              total={total}
              page={pageNumber}
              pageCount={pageCount}
              open={navOpen}
              onToggle={() => {
                setNavOpen(!navOpen);
                saveFlag("imagine-reader:book-nav-open", !navOpen);
              }}
              onGo={go}
              onPage={goToPage}
            />
          )}
          <PdfPage
            pdf={pdf}
            pageNumber={pageNumber}
            highlights={current ? current.boxes.filter(([page]) => page === pageNumber) : []}
            onPick={(x, y) => {
              const picked = sentenceAt(reading.sentences, pageNumber, x, y);
              if (picked) go(picked.order);
            }}
          />
        </div>
        <Splitter
          between="columns"
          label="Width of the picture and text"
          value={sideWidth}
          measure={() => sideColumn.current?.getBoundingClientRect().width ?? 480}
          min={320}
          max={Math.max(320, (grid.current?.getBoundingClientRect().width ?? 1200) - 420)}
          direction={-1}
          onChange={(width) => {
            setSideWidth(width);
            saveSize("imagine-reader:side-width", width);
          }}
          onReset={() => {
            setSideWidth(null);
            saveSize("imagine-reader:side-width", null);
          }}
        />
        <div className="side" ref={sideColumn}>
          {showPictures && current && (
            <SketchPanel
              manifest={shownPictures}
              sample={book.shared}
              activeOrder={current.order}
              error={sketches.error}
              waitForPictures={waitForPictures}
              onWaitChange={(wait) => {
                setWaitForPictures(wait);
                saveFlag("imagine-reader:wait-for-pictures", wait);
              }}
              options={<PictureSummary pictures={pictures} />}
              bookTitle={identity.title}
              resizer={
                <Splitter
                  between="rows"
                  label="Size of the picture"
                  value={pictureHeight}
                  measure={() => document.querySelector(".sketch-frame")?.getBoundingClientRect().height ?? 320}
                  min={140}
                  max={Math.round(window.innerHeight * 0.8)}
                  onChange={(height) => {
                    setPictureHeight(height);
                    saveSize("imagine-reader:picture-height", height);
                  }}
                  onReset={() => {
                    setPictureHeight(null);
                    saveSize("imagine-reader:picture-height", null);
                  }}
                />
              }
            />
          )}
          {current && section ? (
            <TextPanel
              paragraphs={reading.paragraphs.slice(section.first_paragraph, section.end_paragraph)}
              sentencesByParagraph={byParagraph}
              activeOrder={current.order}
              onSelect={go}
            />
          ) : (
            <p className="notice">No readable text was found. Scanned pages need OCR, which is the next feature.</p>
          )}
        </div>
      </div>
      {finished && <EndOfBook onClose={() => setFinished(false)} />}
      <ReaderFooter />
    </div>
  );
}

function NarrationStatus(props: {
  manifest: NarrationManifest | null;
  waiting: boolean;
  cancelled: boolean;
  error: string | null;
  onCancel: () => void;
}) {
  const { manifest, waiting, cancelled, error, onCancel } = props;
  const segments = manifest?.segments ?? [];
  const ready = segments.filter((s) => s.status === "ready").length;
  const overBudget = segments.some((s) => s.error_code === "budget_exceeded");
  const placeholder = manifest?.provider.startsWith("mock");

  let message: string | null = null;
  const stopped = manifest?.error_code;
  if (error) message = `Narration problem: ${error}. Retrying…`;
  else if (stopped === "daily_limit")
    message = "Today's spending limit is reached. The voice carries on after midnight UTC, or raise IR_DAILY_SPEND_LIMIT_USD.";
  else if (stopped === "provider_account")
    message = "Your AI key was refused: check it, and its credit, in your settings (.env).";
  else if (overBudget)
    message = `This book's narration budget is used up (${manifest!.characters_used.toLocaleString()} of ${manifest!.character_budget.toLocaleString()} characters).`;
  else if (cancelled) message = "Narration preparation stopped. Press Play to resume it.";
  else if (!manifest || manifest.preparing)
    message = `Preparing narration… ${ready} of ${segments.length || "?"} sentences ready.`;
  else if (waiting) message = "Waiting for the next sentence…";

  return (
    <div className="narration-status" role="status">
      <span className="disclosure">
        {placeholder
          ? "Placeholder narration: clicks at a speaking pace, not speech, until you add your OpenRouter key."
          : "Narration is an AI-generated voice reading the original text."}
      </span>
      {message && <span>{message}</span>}
      {manifest?.preparing && !cancelled && (
        <button className="link" onClick={onCancel}>
          Stop preparing
        </button>
      )}
    </div>
  );
}

function Message({ text, onClose, children }: { text: string; onClose: () => void; children?: React.ReactNode }) {
  return (
    <main className="message">
      <p role="status">{text}</p>
      {children}
      <button onClick={onClose}>← Library</button>
    </main>
  );
}
