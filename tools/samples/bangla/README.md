# The Bangla samples

Two Bangla samples are built with these tools, each from a spec in this folder:

- `devdas.json` makes `server/src/imagine_reader_server/samples/devdas.pdf`.
  It holds the first two chapters of *দেবদাস* by Sarat Chandra Chattopadhyay
  (first published 1917).
- `kabuliwala.json` makes `samples/kabuliwala.pdf`. It holds the whole of
  *কাবুলিওয়ালা* by Rabindranath Tagore (first published in *Sadhana*,
  1892).

Both texts are in the public domain.

## Why they are built this way

Bangla in PDFs is hard to read back. Printing Bangla from a browser, or from
Typst, gives a text layer where joined letters are missing and vowel signs
such as ি and ে come before their consonant, so the reader would highlight and
narrate garbage. These samples are built like a scanned book with a text layer:

1. Chrome lays the text out in fixed pages and saves each page as a picture,
   with the position of every word.
2. The PDF shows those pictures and carries every word, in correct Unicode,
   as invisible text at the same place. The words therefore read back exactly
   as written, and highlights land on the words in the picture.

## The sources

- **দেবদাস.** The first page is the real title page of the 1355 (1949)
  Sarkar & Co. edition, from the public-domain scan on Bangla Wikisource. The
  cover art of that edition is not used.
- **কাবুলিওয়ালা.** The text is from Bangla Wikisource's
  *গল্পগুচ্ছ (প্রথম খণ্ড)*. That is the Visva-Bharati edition, reprinted in
  1357 (1950–51), and its pages are marked validated. Reading it against the
  scan still found 21 transcription slips. They are corrected in the spec's
  `fixes`, each with its printed page number:
  - letters: ষোলো for যোলো, বিস্মৃত for বিস্তৃত, ঝাড় for ঝড়;
  - doubled chandrabindus;
  - stray commas and quote marks, and missing dashes;
  - a paragraph run into the one before.

  A correction that no longer matches exactly once stops the build. The story
  opens on a typeset title page, because the scan's title page names the
  whole collection.

## Rebuilding one

You need:

- the project's Python environment, with `fontTools`;
- Node.js with `playwright-core`;
- a Chromium build.

```bash
# 1. The chapters' text, from Bangla Wikisource (api.php?action=parse&prop=text JSON for each chapter page,
#    saved as <name>.html.json, where <name> is the chapter's text file in the spec)
python fetch_text.py <text dir>/*.html.json                             # writes <name>.txt beside each
# 2. The page design and the book's contents, corrections made
python prepare_layout.py <spec>.json <text dir> <build dir> reader/node_modules/@fontsource/noto-serif-bengali/files
# 3. Pages as pictures, and every word's box
node layout-pages.mjs <chromium> <build dir>
# 4. The fonts to embed, then the PDF (named in the spec) in the build dir. দেবদাস also needs title-page.jpg
#    there: page 1 of the Wikisource scan, rendered at 220 dpi.
python fonts_to_ttf.py reader/node_modules/@fontsource/noto-serif-bengali/files <ttf dir>
python assemble_pdf.py <build dir> <ttf dir>
```
