import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  PanelLeftClose,
  PanelLeftOpen,
  TableOfContents,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { PDFDocumentProxy } from "./pdf";
import type { Reading, Section } from "../lib/types";

const SMALL_WORDS = new Set(["a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to"]);
const NUMBERED = new Set(["chapter", "book", "part", "volume", "stave", "canto", "letter", "section"]);
const COVER_WIDTH = 56; // CSS pixels

const capitalize = (word: string) => word.replace(/(^|-)(\p{L})/gu, (_, dash, letter) => dash + letter.toUpperCase());

/**
 * "CHAPTER 7 A MAD TEA-PARTY" -> "Chapter 7 A Mad Tea-Party". Small words stay lowercase except at the
 * start and after a number or a colon, where a new title begins. Titles with lowercase letters are kept.
 * A Roman numeral stays in capitals after "Chapter" and the like, alone, or beside a "·" between headings.
 */
export function displayTitle(title: string): string {
  if (/\p{Ll}/u.test(title)) return title;
  const words = title.toLowerCase().split(" ");
  return words
    .map((word, index) => {
      const previous = words[index - 1] ?? "";
      const numbered = NUMBERED.has(previous) || words.length === 1 || previous === "·" || words[index + 1] === "·";
      if (/^[ivxlc]+[.:]?$/.test(word) && numbered) return word.toUpperCase(); // "Chapter IV.", "VI", "XII · The End"
      const startsTitle = index === 0 || previous === "·" || /^([0-9]+|[ivxlc]+)[.:]?$|:$/.test(previous);
      return SMALL_WORDS.has(word) && !startsTitle ? word : capitalize(word);
    })
    .join(" ");
}

/** The book's title and author, from its title page when it has one, or else from the file name. */
export function bookIdentity(reading: Reading, filename: string): { title: string; author: string | null } {
  const fromFile = { title: filename.replace(/\.pdf$/i, "").replace(/_+/g, " ").trim(), author: null };
  const first = reading.sections[0];
  const titlePage = reading.sections.findIndex((section) => section.id === reading.start_section) > 0;
  if (!first || !titlePage) return fromFile;
  const headings = reading.paragraphs
    .slice(first.first_paragraph, first.end_paragraph)
    .filter((paragraph) => paragraph.heading)
    .map((paragraph) => paragraph.text.trim());
  if (!headings.length) return fromFile;
  const byline = headings[1]?.replace(/^by\s+/i, "");
  const author = byline && byline.split(/\s+/).length <= 5 && !/[0-9:]/.test(byline) ? displayTitle(byline) : null;
  return { title: displayTitle(headings[0]), author };
}

/** The book's cover, as the server has it, or else the first page of the PDF drawn small. */
function Cover({ pdf, title, url }: { pdf: PDFDocumentProxy; title: string; url: string | null }) {
  if (url) return <img className="cover" src={url} alt={`Cover of ${title}`} width={56} />;
  return <PageOneCover pdf={pdf} title={title} />;
}

function PageOneCover({ pdf, title }: { pdf: PDFDocumentProxy; title: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    let cancelled = false;
    let task: { cancel(): void; promise: Promise<void> } | null = null;
    pdf.getPage(1).then((page) => {
      if (cancelled || !canvas.current) return;
      const base = page.getViewport({ scale: 1 });
      const viewport = page.getViewport({ scale: (COVER_WIDTH / base.width) * window.devicePixelRatio });
      canvas.current.width = viewport.width;
      canvas.current.height = viewport.height;
      task = page.render({ canvas: canvas.current, viewport });
      task.promise.catch(() => {}); // cancelled by a newer render
    });
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [pdf]);
  return <canvas ref={canvas} className="cover" role="img" aria-label={`Cover of ${title}`} />;
}

interface Props {
  pdf: PDFDocumentProxy;
  coverUrl: string | null; // a sample's own cover, or page 1 drawn by the server
  title: string;
  author: string | null;
  sections: Section[];
  firstOrders: number[]; // the first sentence of each section
  sectionIndex: number;
  storyStart: number; // index of the section where the story begins; earlier ones are front matter
  activeOrder: number;
  total: number; // sentences in the book
  page: number;
  pageCount: number;
  open: boolean; // the full panel, or a slim strip of page and chapter buttons
  onToggle: () => void;
  onGo: (order: number) => void;
  onPage: (page: number) => void;
}

/**
 * Moving around the book: its chapters, and its pages. Beside the page on wide
 * screens, either open or folded into a slim strip of buttons; a single row
 * above the page on narrow ones.
 */
export function BookNav(props: Props) {
  const { pdf, title, author, sections, firstOrders, sectionIndex, storyStart, activeOrder, total, page, pageCount } =
    props;
  const { coverUrl, open, onToggle, onGo, onPage } = props;
  const [typed, setTyped] = useState(String(page));
  useEffect(() => setTyped(String(page)), [page]);

  const start = firstOrders[sectionIndex] ?? 0;
  const end = firstOrders[sectionIndex + 1] ?? total;
  const progress = end > start ? (activeOrder - start + 1) / (end - start) : 0;
  const chapter = (index: number) => firstOrders[index] !== undefined && onGo(firstOrders[index]);

  const steps = (
    <div className="nav-steps">
      <div className="step-row" role="group" aria-label="Chapter">
        <button
          className="icon"
          aria-label="Previous chapter"
          disabled={sectionIndex === 0}
          onClick={() => chapter(sectionIndex - 1)}
        >
          <ChevronsLeft size={18} />
        </button>
        <span className="step-label">Chapter</span>
        <button
          className="icon"
          aria-label="Next chapter"
          disabled={sectionIndex >= sections.length - 1}
          onClick={() => chapter(sectionIndex + 1)}
        >
          <ChevronsRight size={18} />
        </button>
      </div>
      <form
        className="step-row"
        aria-label="Page"
        onSubmit={(event) => {
          event.preventDefault();
          const wanted = Number(typed);
          if (Number.isFinite(wanted)) onPage(wanted);
        }}
      >
        <button type="button" className="icon" aria-label="Previous page" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          <ChevronLeft size={18} />
        </button>
        <label className="step-label page-field">
          <span>Page</span>
          <input
            type="number"
            inputMode="numeric"
            min={1}
            max={pageCount}
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            onBlur={() => setTyped(String(page))}
            aria-label={`Page number, 1 to ${pageCount}`}
          />
          <span>of {pageCount}</span>
        </label>
        <button
          type="button"
          className="icon"
          aria-label="Next page"
          disabled={page >= pageCount}
          onClick={() => onPage(page + 1)}
        >
          <ChevronRight size={18} />
        </button>
      </form>
    </div>
  );

  return (
    <nav className={`book-nav ${open ? "open" : "closed"}`} aria-label="Chapters and pages">
      <div className="nav-strip">
        <button className="icon quiet" aria-label="Show chapters and pages" aria-expanded={false} onClick={onToggle}>
          <PanelLeftOpen size={18} />
        </button>
        <span className="strip-rule" />
        <button
          className="icon quiet"
          aria-label="Previous chapter"
          title="Previous chapter"
          disabled={sectionIndex === 0}
          onClick={() => chapter(sectionIndex - 1)}
        >
          <ChevronsLeft size={18} />
        </button>
        <button
          className="icon quiet"
          aria-label="Previous page"
          title="Previous page"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
        >
          <ChevronLeft size={18} />
        </button>
        <span className="strip-page" title={`Page ${page} of ${pageCount}`}>
          {page}
        </span>
        <button
          className="icon quiet"
          aria-label="Next page"
          title="Next page"
          disabled={page >= pageCount}
          onClick={() => onPage(page + 1)}
        >
          <ChevronRight size={18} />
        </button>
        <button
          className="icon quiet"
          aria-label="Next chapter"
          title="Next chapter"
          disabled={sectionIndex >= sections.length - 1}
          onClick={() => chapter(sectionIndex + 1)}
        >
          <ChevronsRight size={18} />
        </button>
      </div>
      <div className="book-identity">
        <Cover pdf={pdf} title={title} url={coverUrl} />
        <div>
          <p className="book-name">{title}</p>
          {author && <p className="book-author">{author}</p>}
        </div>
      </div>
      <div className="nav-heading-row">
        <h2 className="nav-heading">
          <TableOfContents size={16} aria-hidden="true" /> Contents
        </h2>
        <button className="icon quiet" aria-label="Hide chapters and pages" aria-expanded={true} onClick={onToggle}>
          <PanelLeftClose size={18} />
        </button>
      </div>
      <ol className="contents">
        {sections.map((section, index) => (
          <li key={section.id} className={index < storyStart ? "front-matter" : undefined}>
            <button
              aria-current={index === sectionIndex ? "true" : undefined}
              onClick={() => chapter(index)}
              title={section.title}
            >
              <span className="contents-title">{displayTitle(section.title)}</span>
              <span className="contents-page">{section.page}</span>
            </button>
            {index === sectionIndex && (
              <span className="contents-progress" aria-hidden="true">
                <span style={{ width: `${Math.round(Math.min(1, Math.max(0, progress)) * 100)}%` }} />
              </span>
            )}
          </li>
        ))}
      </ol>
      <label className="contents-select">
        <span className="visually-hidden">Chapter</span>
        <select value={sectionIndex} onChange={(event) => chapter(Number(event.target.value))}>
          {sections.map((section, index) => (
            <option key={section.id} value={index}>
              {displayTitle(section.title)} · p. {section.page}
            </option>
          ))}
        </select>
      </label>
      {steps}
    </nav>
  );
}
