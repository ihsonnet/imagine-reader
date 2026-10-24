import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

export type { PDFDocumentProxy } from "pdfjs-dist";

/** Start loading a PDF from this site (the session cookie goes along). Destroy the task to stop or release it. */
export function openPdf(url: string) {
  return pdfjs.getDocument({
    url,
    // Copied from pdfjs-dist by scripts/copy-pdfjs-assets.mjs.
    standardFontDataUrl: "/pdfjs/standard_fonts/", // fonts many PDFs use without embedding them
    cMapUrl: "/pdfjs/cmaps/", // character maps for CJK and other composite fonts
    wasmUrl: "/pdfjs/wasm/", // decoders for JPEG 2000 and JBIG2 images, common in scans
    iccUrl: "/pdfjs/iccs/",
  });
}
