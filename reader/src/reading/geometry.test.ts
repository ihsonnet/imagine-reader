import { describe, expect, it } from "vitest";
import { boxStyle, sectionIndexOf, sentenceAt } from "./geometry";
import type { Section, Sentence } from "../lib/types";

const section = (first: number, end: number): Section => ({
  id: `sec-${first}`,
  title: `From ${first}`,
  first_paragraph: first,
  end_paragraph: end,
  page: 1,
});

describe("boxStyle", () => {
  it("positions a box in page percentages", () => {
    expect(boxStyle([1, 0.1, 0.25, 0.6, 0.3])).toEqual({
      left: "10%",
      top: "25%",
      width: "50%",
      height: `${(0.3 - 0.25) * 100}%`,
    });
  });
});

describe("sectionIndexOf", () => {
  const sections = [section(0, 3), section(3, 10), section(10, 11)];
  it.each([
    [0, 0],
    [2, 0],
    [3, 1],
    [9, 1],
    [10, 2],
  ])("paragraph %i is in section %i", (paragraph, expected) => {
    expect(sectionIndexOf(sections, paragraph)).toBe(expected);
  });
});

describe("sentenceAt", () => {
  const sentences: Sentence[] = [
    { id: "s-1", order: 0, paragraph_id: "p", text: "One.", boxes: [[1, 0.1, 0.1, 0.5, 0.12]] },
    { id: "s-2", order: 1, paragraph_id: "p", text: "Two.", boxes: [[2, 0.1, 0.1, 0.5, 0.12]] },
  ];
  it("finds the sentence under a point on the right page", () => {
    expect(sentenceAt(sentences, 2, 0.3, 0.11)?.id).toBe("s-2");
    expect(sentenceAt(sentences, 1, 0.9, 0.11)).toBeUndefined();
  });
});
