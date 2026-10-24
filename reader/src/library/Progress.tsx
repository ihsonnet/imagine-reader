import type { Book } from "../lib/types";

/**
 * A book opens only when its first chapter is fully prepared: read, every
 * picture drawn, and every sentence recorded. If preparation stopped early
 * (for example at the daily limit), it opens with what is there.
 */
export function readyToOpen(book: Book): boolean {
  return book.status === "ready" && !book.preparation?.active;
}

interface Stage {
  fraction: number; // 0 to 1
  label: string;
}

/** How far a book is from opening. Reading the PDF is the first tenth; pictures weigh more than the voice. */
export function preparationStage(book: Book): Stage {
  if (book.status === "queued") return { fraction: 0.02, label: "Waiting to be read…" };
  if (book.status === "processing") return { fraction: 0.06, label: "Reading the PDF…" };
  const preparation = book.preparation;
  if (readyToOpen(book) || !preparation) return { fraction: 1, label: "Ready to read" };
  if (!preparation.pictures_planned) return { fraction: 0.1, label: "Planning the scenes of the first chapter…" };
  const { pictures_ready, pictures_planned, clips_ready, clips_planned } = preparation;
  const pictures = pictures_ready / pictures_planned;
  const voice = clips_planned ? clips_ready / clips_planned : 1;
  return {
    fraction: 0.1 + 0.9 * (0.7 * pictures + 0.3 * voice),
    label: `Drawing pictures ${pictures_ready} of ${pictures_planned} · Recording the voice ${clips_ready} of ${clips_planned}`,
  };
}

/** A pencil-line progress bar with its stage written beneath. */
export function PreparationProgress({ book }: { book: Book }) {
  const { fraction, label } = preparationStage(book);
  const percent = Math.round(fraction * 100);
  return (
    <div className="preparation">
      <div
        className="progress"
        role="progressbar"
        aria-label={`Preparing ${book.filename}`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={label}
      >
        <span className="progress-fill" style={{ width: `${percent}%` }} />
      </div>
      <span className="progress-label">
        {label} <span className="progress-percent">{percent}%</span>
      </span>
    </div>
  );
}
