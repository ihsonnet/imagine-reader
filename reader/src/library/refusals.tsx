import { BookOpen, HeartHandshake } from "lucide-react";
import { Modal } from "../site/Modal";
import { GitHubButton, page, useProject } from "../site/project";
import { bookTitle } from "./books";
import type { Book, ServiceInfo } from "../lib/types";

/** How long a book may be. */
export function pagesNote(info: ServiceInfo | null): string {
  return `${(info?.max_pages ?? 1000).toLocaleString()} pages`;
}

/** A scanned book: why it cannot be read yet, and what to do instead. */
export function ScannedDialog({
  book,
  samples,
  onOpen,
  onClose,
}: {
  book: Book;
  samples: ServiceInfo["samples"];
  onOpen: (id: string) => void;
  onClose: () => void;
}) {
  const { repository } = useProject();
  return (
    <Modal title="Scanned books cannot be read yet" onClose={onClose}>
      <p>
        “{bookTitle(book.filename)}” is a scan: its pages are pictures of text, with no text in them to
        read aloud. For now Imagine-Reader reads text-based PDFs only, such as those saved from a document or an
        e-book, where you can select the words.
      </p>
      {samples.length > 0 && (
        <>
          <p>Meanwhile, read one of the sample books:</p>
          <div className="refusal-samples">
            {samples.map((sample) => (
              <button key={sample.id} type="button" lang={sample.language} onClick={() => onOpen(sample.id)}>
                <BookOpen size={16} aria-hidden="true" /> {sample.title}
              </button>
            ))}
          </div>
        </>
      )}
      <p className="quiet-note">
        Reading scanned books is the next feature. Imagine-Reader is open source: help build it, or star it so more
        readers find it.
      </p>
      <div className="dialog-actions refusal-links">
        <button type="button" className="quiet" onClick={onClose}>
          Close
        </button>
        <a className="button" href={page(repository, "blob/main/CONTRIBUTING.md")} target="_blank" rel="noopener">
          <HeartHandshake size={16} aria-hidden="true" /> Help build it
        </a>
        <GitHubButton label="Star on GitHub" />
      </div>
    </Modal>
  );
}
