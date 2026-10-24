import { BookOpen, LayoutGrid, List } from "lucide-react";
import { type ReactNode, useState } from "react";
import { PreparationProgress, readyToOpen } from "./Progress";
import type { Book } from "../lib/types";

export function bookTitle(filename: string): string {
  return filename.replace(/\.pdf$/i, "");
}

/** Books shown as covers in a grid, or as rows in a list. */
export type BookView = "grid" | "list";
const VIEW = "imagine-reader:book-view"; // remembered in this browser, for both libraries

function storedView(): BookView {
  try {
    return localStorage.getItem(VIEW) === "list" ? "list" : "grid";
  } catch {
    return "grid";
  }
}

export function useBookView(): [BookView, (view: BookView) => void] {
  const [view, setView] = useState(storedView);
  const choose = (next: BookView) => {
    setView(next);
    try {
      localStorage.setItem(VIEW, next);
    } catch {
      // not remembered: private browsing or storage turned off
    }
  };
  return [view, choose];
}

/** The two buttons in a library's top-right corner that switch between the grid and the list. */
export function ViewToggle({ view, onChange }: { view: BookView; onChange: (view: BookView) => void }) {
  return (
    <div className="view-toggle" role="group" aria-label="Show books as">
      <button type="button" aria-pressed={view === "grid"} title="Covers" onClick={() => onChange("grid")}>
        <LayoutGrid size={17} aria-hidden="true" />
        <span className="visually-hidden">Covers</span>
      </button>
      <button type="button" aria-pressed={view === "list"} title="List" onClick={() => onChange("list")}>
        <List size={17} aria-hidden="true" />
        <span className="visually-hidden">List</span>
      </button>
    </div>
  );
}

function BookState({ book, short = false }: { book: Book; short?: boolean }) {
  if (book.status === "failed") return <span className="status failed">Could not be read</span>;
  if (!readyToOpen(book)) return <PreparationProgress book={book} />;
  const size = `${book.page_count} pages · ${book.word_count?.toLocaleString()} words`;
  return <span className="status ready">{short ? size : `Ready to read · ${size}`}</span>;
}

/**
 * A shelf of books: covers in a grid, or rows in a list, each with its title,
 * how far it is from opening, and the page's own actions for it.
 */
export function BookCollection({
  books,
  view,
  onOpen,
  actions,
  marked,
}: {
  books: Book[];
  view: BookView;
  onOpen: (id: string) => void;
  actions?: (book: Book) => ReactNode;
  marked?: (book: Book) => boolean; // briefly outlined: a book just added, or the samples pointed at
}) {
  if (view === "list")
    return (
      <ul className="books">
        {books.map((book) => (
          <li key={book.id} id={`book-${book.id}`} className={marked?.(book) ? "just-added" : undefined}>
            {book.cover_url ? (
              <img className="book-cover" src={book.cover_url} alt="" loading="lazy" width={60} height={84} />
            ) : (
              <span className="book-cover blank" aria-hidden="true" />
            )}
            <div className="book-info">
              <strong className="book-title">
                {bookTitle(book.filename)}
                {book.shared && <span className="shared-tag">Sample</span>}
              </strong>
              <BookState book={book} />
              {book.error_message && <span className="book-error">{book.error_message}</span>}
            </div>
            <div className="actions">
              <button className="primary" disabled={!readyToOpen(book)} onClick={() => onOpen(book.id)}>
                <BookOpen size={17} aria-hidden="true" /> Open
              </button>
              {actions?.(book)}
            </div>
          </li>
        ))}
      </ul>
    );

  return (
    <ul className="book-grid">
      {books.map((book) => {
        const title = bookTitle(book.filename);
        const ready = readyToOpen(book);
        const extra = actions?.(book);
        return (
          <li key={book.id} id={`book-${book.id}`} className={`book-card${marked?.(book) ? " just-added" : ""}`}>
            <button
              type="button"
              className="cover-button"
              disabled={!ready}
              onClick={() => onOpen(book.id)}
              aria-label={ready ? `Open ${title}` : `${title}: not ready yet`}
            >
              {book.cover_url ? (
                <img src={book.cover_url} alt="" loading="lazy" width={180} height={252} />
              ) : (
                <span className="cover-blank" aria-hidden="true">
                  {title}
                </span>
              )}
              {ready && (
                <span className="cover-open" aria-hidden="true">
                  <BookOpen size={16} /> Open
                </span>
              )}
              {book.shared && <span className="shared-tag">Sample</span>}
            </button>
            <div className="card-info">
              <strong className="book-title" title={title}>
                {title}
              </strong>
              <BookState book={book} short />
              {book.error_message && <span className="book-error">{book.error_message}</span>}
            </div>
            {extra && <div className="actions">{extra}</div>}
          </li>
        );
      })}
    </ul>
  );
}
