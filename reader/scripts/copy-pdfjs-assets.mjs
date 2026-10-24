// Copy the files PDF.js loads at run time (standard fonts, CMaps, image decoders,
// colour profiles) into public/, so the reader serves them from its own origin.
import { cpSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = join(root, "node_modules", "pdfjs-dist");
const target = join(root, "public", "pdfjs");
rmSync(target, { recursive: true, force: true });
for (const folder of ["standard_fonts", "cmaps", "wasm", "iccs"]) {
  cpSync(join(source, folder), join(target, folder), { recursive: true });
}
