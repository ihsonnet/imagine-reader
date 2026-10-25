import { SlidersHorizontal } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import colorExample from "../assets/examples/color.webp";
import colorHdExample from "../assets/examples/color-hd.webp";
import ghibliExample from "../assets/examples/ghibli.webp";
import pencilExample from "../assets/examples/pencil.webp";
import type { ServiceInfo } from "../lib/types";

// What readers may choose for the pictures; the server lists what it allows in /api/info.
const STYLES: Record<string, { name: string; note: string; example: string }> = {
  pencil: { name: "Pencil sketch", note: "Soft graphite, like a sketchbook", example: pencilExample },
  color: { name: "Colour", note: "Watercolour, like a storybook", example: colorExample },
  "color-hd": { name: "Colour, more detail", note: "Finer and richer; a little slower", example: colorHdExample },
  ghibli: { name: "Studio Ghibli style", note: "Hand-painted, like their films", example: ghibliExample },
  animated: { name: "Hand-painted animation", note: "Hand-painted, like an animated film", example: ghibliExample },
};

/** A picture style's name, as the reader shows it. */
export function styleName(style: string): string {
  return STYLES[style]?.name || style;
}

// How often a new picture appears, with how many pictures that makes in a minute of a story told slowly.
// 0 is Auto: the AI reads the story and changes the picture when it moves.
const AUTO = 0;
const INTERVALS: Record<number, { name: string; short: string; perMinute: number; note: string }> = {
  [AUTO]: {
    name: "Auto",
    short: "paced by the story",
    perMinute: 0,
    note: "The AI changes the picture when the story moves, about 4 or 5 times a minute: quicker in action, slower in calm passages",
  },
  1: { name: "Every sentence", short: "every sentence", perMinute: 9, note: "the most pictures" },
  25: { name: "Every ~10 s", short: "about every 10 s", perMinute: 6, note: "about 6 a minute" },
  60: { name: "Every ~25 s", short: "about every 25 s", perMinute: 2, note: "about 2 a minute" },
  120: { name: "Every ~50 s", short: "about every 50 s", perMinute: 1, note: "about 1 a minute" },
};

const STYLE_KEY = "imagine-reader:picture-style";
const WORDS_KEY = "imagine-reader:picture-words";

function stored(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null; // storage can be unavailable; the choice then lasts until the page closes
  }
}

function store(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // a convenience only
  }
}

export interface PictureChoice {
  style: string;
  words: number;
}

/** This browser's picture style and how often pictures change, remembered between visits. */
export function usePictureChoice(info: ServiceInfo | null) {
  const [style, setStyle] = useState(() => stored(STYLE_KEY));
  const [words, setWords] = useState(() => {
    const saved = stored(WORDS_KEY);
    return saved === null || saved === "" || Number.isNaN(Number(saved)) ? null : Number(saved);
  });
  const styles = info?.picture_styles ?? Object.keys(STYLES);
  const intervals = info?.picture_words ?? Object.keys(INTERVALS).map(Number);
  const allowed = (pace: number) => intervals.includes(pace);
  const choice: PictureChoice = {
    style: style && styles.includes(style) ? style : (info?.default_style ?? "pencil"),
    words: words !== null && allowed(words) ? words : (info?.default_words ?? AUTO),
  };
  const save = (next: PictureChoice) => {
    setStyle(next.style);
    setWords(next.words);
    store(STYLE_KEY, next.style);
    store(WORDS_KEY, String(next.words));
  };
  return { choice, styles, intervals, save };
}

export type Pictures = ReturnType<typeof usePictureChoice>;

/** How often a new picture appears, in a few words. */
export function frequencyName(words: number): string {
  return INTERVALS[words]?.short ?? `every ${words} words`;
}

function describe({ style, words }: PictureChoice): string {
  return `${styleName(style)}, ${frequencyName(words)}`;
}

/** A dense or sparse row of ticks: how often pictures change in a minute of reading. */
function Ticks({ count }: { count: number }) {
  return (
    <span className="ticks" aria-hidden="true">
      {Array.from({ length: count }, (_, index) => (
        <span key={index} />
      ))}
    </span>
  );
}

// Where an Auto-paced minute might change the picture (5 times): close together in action, far apart in calm.
const STORY_TICKS = [0, 11, 20, 52, 84];

/** Ticks spaced the way a story might pace its pictures. */
function StoryTicks() {
  return (
    <span className="ticks story" aria-hidden="true">
      {STORY_TICKS.map((at) => (
        <span key={at} style={{ left: `${at}%` }} />
      ))}
    </span>
  );
}

/**
 * Picture style and interval, shown with example pictures and a timeline of how often they change.
 * Choices are a draft until confirmed, so trying options draws nothing.
 */
export function PictureDialog(props: {
  title: string;
  subtitle?: string;
  note: string;
  confirm: string;
  pictures: Pictures;
  onConfirm: (choice: PictureChoice) => void;
  onCancel: () => void;
}) {
  const { title, subtitle, note, confirm, pictures, onConfirm, onCancel } = props;
  const [draft, setDraft] = useState(pictures.choice);
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);

  return (
    <dialog
      ref={dialog}
      className="picture-dialog"
      aria-labelledby="picture-dialog-title"
      onCancel={(event) => {
        event.preventDefault(); // Escape goes through the same path as the Cancel button
        onCancel();
      }}
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          onConfirm(draft);
        }}
      >
        <header>
          <h2 id="picture-dialog-title">{title}</h2>
          {subtitle && <p className="dialog-book">{subtitle}</p>}
        </header>

        <fieldset className="choice-group">
          <legend>Style</legend>
          <div className="style-cards">
            {pictures.styles.map((style) => {
              const known = STYLES[style];
              return (
                <label key={style} className="style-card">
                  <input
                    type="radio"
                    name="style"
                    value={style}
                    checked={draft.style === style}
                    onChange={() => setDraft({ ...draft, style })}
                  />
                  {known && <img src={known.example} alt="" width={480} height={320} />}
                  <span className="card-text">
                    <span className="card-name">{styleName(style)}</span>
                    {known && <span className="card-note">{known.note}</span>}
                  </span>
                </label>
              );
            })}
          </div>
          <p className="example-caption">
            Examples from <cite>Alice’s Adventures in Wonderland</cite>, drawn by Imagine-Reader.
            {pictures.styles.includes("ghibli") &&
              " The Studio Ghibli style is inspired by their films; Imagine-Reader is not affiliated with Studio" +
                " Ghibli. Scenes the picture service declines in that style are drawn in colour instead."}
          </p>
        </fieldset>

        <fieldset className="choice-group">
          <legend>New picture</legend>
          <div className="interval-options">
            {pictures.intervals.map((words) => {
              const known = INTERVALS[words];
              const auto = words === AUTO;
              return (
                <label key={words} className={["interval-option", auto && "auto"].filter(Boolean).join(" ")}>
                  <input
                    type="radio"
                    name="words"
                    value={words}
                    checked={draft.words === words}
                    onChange={() => setDraft({ ...draft, words })}
                  />
                  <span className="card-name">
                    {known?.name ?? `Every ${words} words`}
                    {auto && <span className="badge">Recommended</span>}
                  </span>
                  {auto ? <StoryTicks /> : known && <Ticks count={known.perMinute} />}
                  {known && <span className="card-note">{known.note}</span>}
                </label>
              );
            })}
          </div>
        </fieldset>

        <p className="dialog-note">{note}</p>
        <div className="dialog-actions">
          <button type="button" className="quiet" onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" className="primary" autoFocus>
            {confirm}
          </button>
        </div>
      </form>
    </dialog>
  );
}

/** While reading: the current choice in one line, and a button that opens the choices. */
export function PictureSummary({ pictures }: { pictures: Pictures }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="picture-summary">
      <span>
        <span className="summary-label">Pictures:</span> {describe(pictures.choice)}
      </span>
      <button type="button" className="link" onClick={() => setOpen(true)}>
        <SlidersHorizontal size={15} aria-hidden="true" /> Change
      </button>
      {open && (
        <PictureDialog
          title="Change the pictures"
          note="New pictures are drawn from the sentence you are on. Pictures already drawn are kept, so switching back is instant."
          confirm="Use these pictures"
          pictures={pictures}
          onConfirm={(choice) => {
            pictures.save(choice);
            setOpen(false);
          }}
          onCancel={() => setOpen(false)}
        />
      )}
    </div>
  );
}
