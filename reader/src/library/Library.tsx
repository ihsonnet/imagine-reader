import { BookOpen, FileUp, KeyRound, Scissors, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { justAdded, useBookAdder } from "./adder";
import { api } from "../lib/api";
import { BookCollection, bookTitle, useBookView, ViewToggle } from "./books";
import { HeroDemo } from "./HeroDemo";
import { PdfRoute } from "./PdfRoute";
import { readyToOpen } from "./Progress";
import { pagesNote, ScannedDialog } from "./refusals";
import { page, SiteFooter, useProject } from "../site/project";
import { SiteHeader } from "../site/SiteHeader";
import type { Book, ServiceInfo } from "../lib/types";

/**
 * The home page and library: what Imagine-Reader is, your books, the samples, and where a PDF's text goes. A book
 * you add opens by itself once its first chapter is ready.
 */
export function Library({ onOpen }: { onOpen: (id: string) => void }) {
  const [info, setInfo] = useState<ServiceInfo | null>(null);
  const [books, setBooks] = useState<Book[] | null>(null);
  const [view, setView] = useBookView();
  const [marked, setMarked] = useState<string | null>(justAdded.id); // a book just added: in view, outlined
  const [refused, setRefused] = useState<Book | null>(null); // a book just added that was a scan
  const [error, setError] = useState<string | null>(null);
  const shelf = useRef<HTMLElement>(null);
  const [pointingAtSamples, setPointingAtSamples] = useState(false); // the samples are marked for a moment

  const refresh = useCallback(
    () =>
      api.listBooks().then(
        (list) => {
          setBooks(list);
          const waiting = list.find((book) => book.id === justAdded.id);
          if (waiting && readyToOpen(waiting)) {
            justAdded.id = null; // opens once, by itself, when its first chapter is ready
            onOpen(waiting.id);
          }
          if (waiting?.status === "failed") {
            justAdded.id = null;
            if (waiting.error_code === "scanned_pdf") setRefused(waiting);
          }
        },
        (e: Error) => setError(e.message),
      ),
    [onOpen],
  );
  const adder = useBookAdder({
    info,
    onAdded: (book) => {
      setMarked(book.id);
      refresh();
    },
  });

  useEffect(() => {
    api.info().then(setInfo, () => {});
    refresh();
  }, [refresh]);

  const busy = !!books?.some((book) => book.status !== "failed" && !readyToOpen(book));
  useEffect(() => {
    if (!busy) return;
    const timer = window.setInterval(refresh, 1200);
    return () => window.clearInterval(timer);
  }, [busy, refresh]);

  // A new book: in view, so its preparation can be followed, and outlined for a moment.
  const markedIsListed = marked !== null && !!books?.some((book) => book.id === marked);
  useEffect(() => {
    if (!markedIsListed) return;
    const quiet = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document.getElementById(`book-${marked}`)?.scrollIntoView({ behavior: quiet ? "auto" : "smooth", block: "center" });
    const timer = window.setTimeout(() => setMarked(null), 4000);
    return () => window.clearTimeout(timer);
  }, [markedIsListed, marked]);

  function showSamples() {
    const quiet = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    shelf.current?.scrollIntoView({ behavior: quiet ? "auto" : "smooth", block: "start" });
    setPointingAtSamples(true);
    window.setTimeout(() => setPointingAtSamples(false), 3000);
  }

  async function trim(book: Book) {
    setError(null);
    try {
      await api.trim(book.id);
      justAdded.id = book.id; // opens by itself once its first chapter is ready
      setMarked(book.id);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function remove(book: Book) {
    if (!window.confirm(`Delete “${bookTitle(book.filename)}” and everything made from it?`)) return;
    if (justAdded.id === book.id) justAdded.id = null;
    await api.deleteBook(book.id).catch((e: Error) => setError(e.message));
    await refresh();
  }

  function actions(book: Book) {
    const tooLong = book.error_code === "too_many_pages" || book.error_code === "too_many_words";
    return (
      <>
        {tooLong && (
          <button className="primary" onClick={() => trim(book)}>
            <Scissors size={16} aria-hidden="true" />
            {book.error_code === "too_many_pages"
              ? `Read the first ${(info?.max_pages ?? 1000).toLocaleString()} pages`
              : "Read the first part"}
          </button>
        )}
        <button className="quiet" onClick={() => remove(book)} aria-label={`Delete ${book.filename}`}>
          <Trash2 size={16} aria-hidden="true" /> Delete
        </button>
      </>
    );
  }

  const yours = (books ?? []).filter((book) => book.mine);
  const samples = (books ?? []).filter((book) => book.shared);
  const problem = adder.error ?? error;
  return (
    <>
      <SiteHeader />

      <main className="site">
        <section className="hero" aria-labelledby="hero-title">
          <div className="hero-copy">
            <h1 id="hero-title">
              Listen &amp; Imagine a book read from <em>its own pages.</em>
            </h1>
            <p className="lede">
              Imagine-Reader reads the original text of a PDF aloud, highlights each sentence on the page as it is
              read, and sketches each scene beside it.
            </p>
            <div className="hero-actions">
              <button className="primary" disabled={!samples.length} onClick={showSamples}>
                <BookOpen size={18} aria-hidden="true" /> Read a Sample Book
              </button>
              {adder.allowed && (
                <button className="button" disabled={adder.adding} onClick={adder.choose}>
                  <FileUp size={18} aria-hidden="true" /> {adder.adding ? "Opening…" : "Add Your Book"}
                </button>
              )}
            </div>
            {adder.allowed && (
              <p className="limits">
                Text-based PDFs up to {info?.max_upload_mb ?? 100} MB and {pagesNote(info)}. Drop one anywhere on this
                page.
              </p>
            )}
            {problem && (
              <p className="error-banner" role="alert">
                {problem}
              </p>
            )}
            <KeyNotice info={info} />
          </div>
          <HeroDemo />
        </section>

        {adder.allowed && (
          <section className="shelf" aria-labelledby="yours-title">
            <div className="shelf-head">
              <div>
                <h2 id="yours-title">Your books</h2>
                <p className="shelf-note">On this computer, until you delete them.</p>
              </div>
              <div className="shelf-tools">
                <button className="primary" disabled={adder.adding} onClick={adder.choose}>
                  <FileUp size={17} aria-hidden="true" /> {adder.adding ? "Adding…" : "Add a book"}
                </button>
                <ViewToggle view={view} onChange={setView} />
              </div>
            </div>
            {books && yours.length === 0 ? (
              <div className="empty-shelf">
                <p>No books yet. Add a PDF, or drop one anywhere on this page.</p>
                <p className="quiet-note">Scanned books cannot be read yet.</p>
              </div>
            ) : (
              <BookCollection
                books={yours}
                view={view}
                onOpen={onOpen}
                actions={actions}
                marked={(book) => book.id === marked}
              />
            )}
          </section>
        )}

        <section className="shelf" aria-labelledby="samples-title" ref={shelf}>
          <div className="shelf-head">
            <div>
              <h2 id="samples-title">Sample books</h2>
              <p className="shelf-note">Ready to read, with their voice and pictures: no key needed.</p>
            </div>
            {!adder.allowed && (
              <div className="shelf-tools">
                <ViewToggle view={view} onChange={setView} />
              </div>
            )}
          </div>
          <BookCollection
            books={samples}
            view={view}
            onOpen={onOpen}
            marked={(book) => pointingAtSamples && book.shared}
          />
        </section>

        <PdfRoute info={info} />
      </main>

      <SiteFooter />

      {adder.element}

      {refused && (
        <ScannedDialog
          book={refused}
          samples={info?.samples ?? []}
          onOpen={(id) => {
            setRefused(null);
            onOpen(id);
          }}
          onClose={() => setRefused(null)}
        />
      )}
    </>
  );
}

/**
 * What your own books need, when something is missing: a key (OpenRouter's is all it takes), or credit, or a new
 * day. A public demo, which offers only its samples, says where to run your own.
 */
function KeyNotice({ info }: { info: ServiceInfo | null }) {
  const { repository } = useProject();
  if (!info) return null;
  const guide = page(repository, "#run-it-on-your-computer");
  if (!info.uploads)
    return (
      <p className="preview-note">
        <strong>A demo of the samples.</strong> To read your own books, run Imagine-Reader on your computer with your
        own OpenRouter key: <a href={guide}>it takes one command</a>.
      </p>
    );
  if (info.services.voice === "placeholders" || info.services.voice === "none")
    return (
      <p className="preview-note key-note">
        <KeyRound size={16} aria-hidden="true" /> <strong>Add your OpenRouter key</strong> to give your own books a
        voice and pictures: put <code>OPENROUTER_API_KEY=…</code> in the <code>.env</code> file and restart (
        <a href={guide}>how</a>). Until then, they get placeholders. The samples need no key.
      </p>
    );
  if (info.ai_status === "unavailable")
    return (
      <p className="error-banner" role="alert">
        Your AI key was refused just now: check it in <code>.env</code>, and that its account has credit.
      </p>
    );
  if (info.ai_status === "daily_limit")
    return (
      <p className="preview-note">
        Today's spending limit (${info.daily_limit_usd.toFixed(2)}) is reached: voice and pictures carry on after
        midnight UTC, or raise <code>IR_DAILY_SPEND_LIMIT_USD</code> in <code>.env</code>.
      </p>
    );
  return (
    <p className="preview-note">
      <strong>Early preview.</strong> The voice and the sketches are made by AI (
      {describe(info)}). Scanned pages cannot be read yet.
    </p>
  );
}

function describe(info: ServiceInfo): string {
  const via = (route: string) => (route === "openrouter" ? " through OpenRouter" : "");
  const voice = `Google's Gemini storyteller${via(info.services.voice)}`;
  const pictures =
    info.services.pictures === "cloudflare"
      ? "FLUX.2 pictures on Cloudflare"
      : info.services.pictures === "openrouter"
        ? "FLUX.2 pictures through OpenRouter"
        : "OpenAI's pictures";
  return info.services.voice === "openai" ? `OpenAI's voice, ${pictures}` : `${voice}, ${pictures}`;
}
