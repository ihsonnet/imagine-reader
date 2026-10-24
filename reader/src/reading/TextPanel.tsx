import { useEffect, useRef } from "react";
import type { Paragraph, Sentence } from "../lib/types";

interface Props {
  paragraphs: Paragraph[];
  sentencesByParagraph: Map<string, Sentence[]>;
  activeOrder: number;
  onSelect: (order: number) => void;
}

/** The extracted reading text of one section; the active sentence follows the page highlight. */
export function TextPanel({ paragraphs, sentencesByParagraph, activeOrder, onSelect }: Props) {
  const active = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    active.current?.scrollIntoView({ block: "nearest", behavior: still ? "auto" : "smooth" });
  }, [activeOrder]);

  return (
    <article className="text-panel" aria-label="Extracted text">
      {paragraphs.map((paragraph) => {
        const sentences = sentencesByParagraph.get(paragraph.id) ?? [];
        const content = sentences.map((sentence) => (
          <span key={sentence.id}>
            <span
              ref={sentence.order === activeOrder ? active : undefined}
              className={sentence.order === activeOrder ? "sentence active" : "sentence"}
              aria-current={sentence.order === activeOrder ? "true" : undefined}
              onClick={() => onSelect(sentence.order)}
            >
              {sentence.text}
            </span>{" "}
          </span>
        ));
        return paragraph.heading ? <h2 key={paragraph.id}>{content}</h2> : <p key={paragraph.id}>{content}</p>;
      })}
    </article>
  );
}
