import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { bookTitle } from "./books";
import { type PictureChoice, PictureDialog, usePictureChoice } from "../reading/pictures";
import type { Book, ServiceInfo } from "../lib/types";

/** The book just added: it opens by itself once its first chapter is ready. */
export const justAdded = { id: null as string | null };

/**
 * Adding a book, from any page: choose a PDF (or drop one on the page), say how its pictures should look, and it is
 * read and its first chapter prepared with your keys. ``element`` holds the dialogs; ``choose`` starts it all.
 */
export function useBookAdder({ info, onAdded }: { info: ServiceInfo | null; onAdded: (book: Book) => void }) {
  const pictures = usePictureChoice(info);
  const input = useRef<HTMLInputElement>(null);
  const [chosen, setChosen] = useState<File | null>(null); // a PDF waiting for its picture choices
  const [adding, setAdding] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const allowed = info !== null; // once the picture choices are known

  function choose() {
    input.current?.click();
  }

  function addFile(file: File | undefined) {
    if (!file || !allowed) return;
    if (file.type && file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
      setError("That file is not a PDF. Choose a PDF file.");
      return;
    }
    setError(null);
    setChosen(file); // asks how the pictures should look, then uploads
  }

  function forgetChosen() {
    setChosen(null);
    if (input.current) input.current.value = "";
  }

  async function add(file: File, choice: PictureChoice) {
    pictures.save(choice);
    setChosen(null);
    setError(null);
    setAdding(true);
    try {
      const book = await api.upload(file, choice);
      justAdded.id = book.id;
      onAdded(book);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setAdding(false);
      if (input.current) input.current.value = "";
    }
  }

  // Drop a PDF anywhere on the page.
  useEffect(() => {
    if (!allowed) return;
    let depth = 0;
    const carriesFiles = (event: DragEvent) => event.dataTransfer?.types.includes("Files");
    const enter = (event: DragEvent) => {
      if (!carriesFiles(event)) return;
      depth += 1;
      setDragging(true);
    };
    const leave = () => {
      depth = Math.max(0, depth - 1);
      if (!depth) setDragging(false);
    };
    const over = (event: DragEvent) => carriesFiles(event) && event.preventDefault();
    const drop = (event: DragEvent) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      depth = 0;
      setDragging(false);
      addFile(event.dataTransfer?.files[0]);
    };
    window.addEventListener("dragenter", enter);
    window.addEventListener("dragleave", leave);
    window.addEventListener("dragover", over);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragenter", enter);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("dragover", over);
      window.removeEventListener("drop", drop);
    };
  });

  const element = (
    <>
      <input
        ref={input}
        type="file"
        accept="application/pdf,.pdf"
        hidden
        disabled={adding || !allowed}
        onChange={(event) => addFile(event.target.files?.[0])}
      />

      {chosen && (
        <PictureDialog
          title="How should the pictures look?"
          subtitle={bookTitle(chosen.name)}
          note="The first chapter is prepared this way before the book opens. You can change it while reading."
          confirm="Prepare the book"
          pictures={pictures}
          onConfirm={(choice) => add(chosen, choice)}
          onCancel={forgetChosen}
        />
      )}

      {dragging && (
        <div className="drop-overlay" aria-hidden="true">
          <span>Drop the PDF to open it</span>
        </div>
      )}
    </>
  );

  return { choose, adding, allowed, error, element };
}
