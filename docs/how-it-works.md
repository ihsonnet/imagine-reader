# How Imagine-Reader works

```
PDF ─► engine: text layer, reading order, sentences, highlight boxes
         │
         ├─► front matter skipped ─► opens at Chapter 1
         ├─► voice: a paragraph at a time, cut into sentences
         │          read by gemini-3.8-flash-tts (Bangla: gemini-3.8-flash-lite-tts), voice Charon
         │          timed by gemini-3.8-flash, each cut checked by gpt-4o-transcribe
         └─► story bible: cast, objects, places, setting, chapter notes (gpt-5.4-mini, once per book)
                 └─► storyboard per chapter: where each picture starts, what it shows, how it is framed
                         └─► reference portraits ─► pictures (FLUX.2 [klein] 4B)

server: FastAPI + a background worker; jobs, cache, the daily limit and the spending ledger in SQLite
reader: React + PDF.js; the page, the highlight, the voice and the pictures, kept in step
```

Every model is reached through OpenRouter, with your one key, except the
pictures when a Cloudflare account draws them. The voice and pictures are
prepared ahead of the reader, a chapter at a time, so they are ready when the
reader gets there.

## Reading the PDF

- **Real layouts.** The engine reads the PDF's text layer with pdfplumber
  and works out the reading order across single and two columns, on rotated
  and cropped pages. It skips running headers, footers and page numbers,
  rejoins words hyphenated across lines, and reattaches drop caps. Each
  sentence keeps its boxes on the page, which the highlight is drawn from.
- **Lost letters recovered.** Some PDFs, such as e-books printed from a
  browser, store small capitals without their letters. The engine recovers
  them from the font, so a chapter title reads "In Santa Croce with No
  Baedeker", not "I S C N B".
- **Bangla PDFs from Google Docs.** Bangla PDFs made in Google Docs or printed
  from Chrome label their letters out of order and leave conjuncts out. The
  engine reads their words back from the glyphs on the page: it shapes
  syllables with HarfBuzz and the bundled Noto Sans Bengali, and splits each
  word into the syllables that make exactly its glyphs.
- **Bangla sentences and names.** Bangla sentences end at the dari (।), and
  names are found with their case endings (রহমতের, রহমতকে).
- **Front matter skipped.** The title page, copyright notice and contents are
  recognised, so the book opens at Chapter 1.
- **Scanned PDFs are refused** with a clear message, before anything is spent.

## The voice

- **A paragraph at a time.** Gemini reads a run of sentences in one go, laid
  out one sentence per line, with a direction to read slowly and warmly, so
  they flow into each other as a storyteller's would.
- **Cut into sentences.** A second Gemini model hears the recording and marks
  where each sentence starts, so the highlight moves sentence by sentence.
  Each cut is moved to the nearest real pause (at least 0.15 seconds of
  silence), never into a word.
- **Every cut checked.** OpenAI's recogniser hears each sentence's clip. If a
  clip ends more than a few letters away from where its sentence ends, the
  run is read again in smaller pieces. A sentence the voice refuses to read
  (garbled source text, for instance) is left silent rather than read wrong.
- **Recorded ahead.** Three runs are recorded at once, ahead of the reader.

## The pictures

- **The whole book first.** Before any picture is planned, the language model
  reads the whole book once and writes its story bible: the cast, objects and
  places, the setting, and notes on each chapter. It fixes how each one looks,
  including gender, age and species, and how their names are written in the
  book, so a Bangla book's characters are found in its Bangla text.
- **People met before they are named.** Stories often introduce someone as
  "the old man" or "a tall stranger" long before giving a name. The bible
  records how the story first refers to each person, so they are drawn as the
  same person before and after they are named, and never before they appear.
- **A storyboard for each chapter.** The model plans where each picture starts
  and what it shows, knowing what has happened so far, and never shows the
  same view twice in a row. With Auto, it is given the chapter's narrated
  length and asked for a picture about every 13 seconds, more in action and
  fewer in calm passages; a picture held much longer than 30 seconds is split.
  Long chapters are planned four minutes at a time.
- **Framing and portraits.** Each main character is drawn once as a reference
  portrait. Each picture is framed as a wide, medium or close shot, and the
  portraits go only with close shots, so wider pictures are composed freely
  instead of copying the portrait's pose.
- **Drawn by FLUX.2 [klein] 4B**, through OpenRouter or on Cloudflare, in the
  chosen style: 3:2 for scenes, square for portraits.

## Never worse, only later

The voice, planning and pictures always come from the same models. When a
limit is reached (the daily spending limit, a provider's rate limit, or an
account out of credit), the work waits and carries on later. Nothing cheaper
or rougher stands in, so a book never ends up with a mix of voices or picture
models. Each request's estimated cost is recorded in a spending ledger, which
the daily limit is checked against.

Recordings and pictures are cached under the model, voice and plan that made
them, so nothing is paid for twice.

## The sample pack

The samples' voice and pictures are not in the repository. They come in a
sample pack, a release asset that the worker downloads and installs on first
start. It holds each sample's reading, story bible, storyboards, pictures and
recordings. It is installed only if its SHA-256 is the published pack's, and
each sample only if it was made from the same PDF as the one in the
repository, so the recordings match their sentences. Turn it off with
`IR_SAMPLES=false`, or use a pack of your own: set `IR_SAMPLE_PACK_URL` to its
URL or file path, and `IR_SAMPLE_PACK_SHA256` to its SHA-256 (or leave that
empty to skip the check).

To make a pack, prepare the samples with your key (`IR_PREPARE_SAMPLES=true`),
then run this with the reader's data folder:

```bash
python tools/make_sample_pack.py DATA_DIR samples-1.tar.gz
```

It prints the pack's size and SHA-256.

## Using the engine on its own

```python
from imagine_reader.ingestion import ingest_pdf

document = ingest_pdf("fixtures/pdfs/two-column.pdf")
print(document.sentences[1].narration_text)
print(document.sentences[1].boxes)  # fractions of the visible page, origin top-left
```
