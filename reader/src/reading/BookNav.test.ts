import { describe, expect, it } from "vitest";
import { bookIdentity, displayTitle } from "./BookNav";
import type { Reading } from "../lib/types";

describe("displayTitle", () => {
  it("turns titles printed in capitals into title case", () => {
    expect(displayTitle("CHAPTER 1 DOWN THE RABBIT HOLE")).toBe("Chapter 1 Down the Rabbit Hole");
    expect(displayTitle("CHAPTER 7 A MAD TEA-PARTY")).toBe("Chapter 7 A Mad Tea-Party");
    expect(displayTitle("CHAPTER IV. THE RABBIT SENDS IN A LITTLE BILL")).toBe("Chapter IV. The Rabbit Sends in a Little Bill");
    expect(displayTitle("A CIVIL WAR")).toBe("A Civil War");
  });

  it("keeps a chapter's Roman numeral in capitals", () => {
    expect(displayTitle("VI")).toBe("VI");
    expect(displayTitle("XII · THE RETURN")).toBe("XII · The Return");
    expect(displayTitle("PART II · VIII · MEDIEVAL")).toBe("Part II · VIII · Medieval");
  });

  it("keeps titles that already have lowercase letters", () => {
    expect(displayTitle("The Lantern Keeper")).toBe("The Lantern Keeper");
  });
});

const paragraph = (order: number, text: string, heading: boolean) => ({ id: `p${order}`, order, text, heading });

function reading(paragraphs: ReturnType<typeof paragraph>[], startSection: string): Reading {
  return {
    book_id: "b",
    schema_version: "0.1",
    pages: [],
    sections: [
      { id: "sec-001", title: "front", first_paragraph: 0, end_paragraph: 3, page: 1 },
      { id: "sec-002", title: "CHAPTER 1", first_paragraph: 3, end_paragraph: 4, page: 3 },
    ],
    paragraphs,
    sentences: [],
    warnings: [],
    start_section: startSection,
    narration_style: "story",
  };
}

describe("bookIdentity", () => {
  const titlePage = [
    paragraph(0, "ALICE'S ADVENTURES IN WONDERLAND", true),
    paragraph(1, "LEWIS CARROLL", true),
    paragraph(2, "This book has been downloaded from www.example.com.", false),
    paragraph(3, "CHAPTER 1", true),
  ];

  it("reads the title and author from the title page", () => {
    expect(bookIdentity(reading(titlePage, "sec-002"), "alice.pdf")).toEqual({
      title: "Alice's Adventures in Wonderland",
      author: "Lewis Carroll",
    });
  });

  it("falls back to the file name when the story starts on the first page", () => {
    expect(bookIdentity(reading(titlePage, "sec-001"), "Great_Expectations.pdf")).toEqual({
      title: "Great Expectations",
      author: null,
    });
  });
});
