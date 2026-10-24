import { useEffect, useRef, useState } from "react";
import { boxStyle } from "./geometry";
import type { PDFDocumentProxy } from "./pdf";
import type { Box } from "../lib/types";

interface Props {
  pdf: PDFDocumentProxy;
  pageNumber: number;
  highlights: Box[];
  onPick: (x: number, y: number) => void;
}

interface Size {
  width: number;
  height: number;
}

/** One PDF page drawn whole inside its frame (no scrolling), with sentence highlights laid over it. */
export function PdfPage({ pdf, pageNumber, highlights, onPick }: Props) {
  const frame = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [room, setRoom] = useState<Size>({ width: 0, height: 0 });
  const [size, setSize] = useState<Size | null>(null);

  useEffect(() => {
    const element = frame.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) =>
      setRoom({ width: Math.floor(entry.contentRect.width), height: Math.floor(entry.contentRect.height) }),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!room.width || !room.height || !canvas.current) return;
    let cancelled = false;
    let task: { cancel(): void; promise: Promise<void> } | null = null;
    pdf.getPage(pageNumber).then((page) => {
      if (cancelled || !canvas.current) return;
      const base = page.getViewport({ scale: 1 }); // includes the page's own rotation
      const scale = Math.min(room.width / base.width, room.height / base.height); // the whole page fits
      setSize({ width: Math.floor(base.width * scale), height: Math.floor(base.height * scale) });
      const viewport = page.getViewport({ scale: scale * window.devicePixelRatio });
      canvas.current.width = viewport.width;
      canvas.current.height = viewport.height;
      task = page.render({ canvas: canvas.current, viewport });
      task.promise.catch(() => {}); // a newer render cancelled this one
    });
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [pdf, pageNumber, room.width, room.height]);

  function pick(event: React.MouseEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    onPick((event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height);
  }

  return (
    <div className="page-frame" ref={frame}>
      <div className="page" style={size ?? { width: 0, height: 0 }} onClick={pick}>
        <canvas ref={canvas} role="img" aria-label={`Page ${pageNumber} of ${pdf.numPages}`} />
        {highlights.map((box, index) => (
          <div key={index} className="highlight" style={boxStyle(box)} />
        ))}
      </div>
    </div>
  );
}
