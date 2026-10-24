import type { Box, Paragraph, Section, Sentence } from "../lib/types";

/** Position a box over a page element as percentages, so it holds at any zoom. */
export function boxStyle([, x0, top, x1, bottom]: Box) {
  return {
    left: `${x0 * 100}%`,
    top: `${top * 100}%`,
    width: `${(x1 - x0) * 100}%`,
    height: `${(bottom - top) * 100}%`,
  };
}

/** Index of the section containing a paragraph (sections are ordered and contiguous). */
export function sectionIndexOf(sections: Section[], paragraphOrder: number): number {
  let low = 0;
  let high = sections.length - 1;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (sections[middle].first_paragraph <= paragraphOrder) low = middle;
    else high = middle - 1;
  }
  return low;
}

/** The sentence whose highlight contains a point on a page, if any. */
export function sentenceAt(sentences: Sentence[], page: number, x: number, y: number): Sentence | undefined {
  return sentences.find((sentence) =>
    sentence.boxes.some(([p, x0, top, x1, bottom]) => p === page && x >= x0 && x <= x1 && y >= top && y <= bottom),
  );
}

/** Map paragraph id to its order, for grouping sentences by section. */
export function paragraphOrders(paragraphs: Paragraph[]): Map<string, number> {
  return new Map(paragraphs.map((paragraph) => [paragraph.id, paragraph.order]));
}
