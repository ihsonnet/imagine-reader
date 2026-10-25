# Contributing to Imagine-Reader

Thank you for helping. Imagine-Reader reads a PDF's own text aloud, highlights
each sentence on the page, and draws the story as it goes. There are many
ways to help, and most of them need no code.

## Ways to help

- **Star the repository.** It helps other readers and contributors find it.
- **Report a book that reads badly.** Wrong reading order, a header read
  aloud, a chapter title split in two, a sentence cut in half:
  [open an issue](https://github.com/ihsonnet/imagine-reader/issues/new?template=book-reads-badly.yml)
  with the book's title, the page, and what you heard or saw. Public-domain
  books are easiest to look into; please do not attach books you may not share.
- **Report a bug** in the reader, the voice or the pictures, with the
  [bug report form](https://github.com/ihsonnet/imagine-reader/issues/new?template=bug-report.yml).
- **Suggest or improve picture styles and narration.** Tell us what works and
  what does not, with an example passage, as an
  [idea](https://github.com/ihsonnet/imagine-reader/issues/new?template=idea.yml).
- **Improve the code.** Scanned-page OCR, more languages, a desktop app,
  accessibility, and translations of the interface are all open.
- **Improve the documentation**, including this guide.

Look for issues labelled
[`good first issue`](https://github.com/ihsonnet/imagine-reader/labels/good%20first%20issue)
to start small. For anything larger than a small fix, open an issue first so
we can agree on the approach.

## Making a change

1. Fork the repository on GitHub and clone your fork.
2. Create a branch from `main` named for the change, such as
   `fix-two-column-order` or `bangla-names`.
3. Set it up and make your change, as below, with tests for what it adds or
   fixes.
4. Run the checks in [Before you open a pull request](#before-you-open-a-pull-request).
5. Push the branch and open a pull request against `main`. Fill in the
   template: what changed, why, and how you checked it. CI runs the same
   checks.

A maintainer will review it. If changes are asked for, push them to the same
branch; the pull request updates itself.

## Getting set up

You need Python 3.11 or newer and Node.js 22.

```bash
python3 -m venv .venv
.venv/bin/pip install -e "engine[dev]" -e "server[dev]"
(cd reader && npm install)
```

Run the API, the worker, and the reader in three terminals, from the
repository root:

```bash
.venv/bin/uvicorn --factory imagine_reader_server.api:create_app --port 8000
.venv/bin/python -m imagine_reader_server.worker
(cd reader && npm run dev)        # http://localhost:5173
```

The reader's development server passes `/api` requests on to the API on port
8000, and reloads as you edit.

Without any key, narration and pictures use placeholders, so you can work on
everything without paying for AI. To hear and see the real thing, copy
`.env.example` to `.env` and add an `OPENROUTER_API_KEY`; it is git-ignored.
See the README for running with Docker and for the server's settings. To try
your change the way users run it, build the image from your branch:

```bash
docker compose up -d --build
```

## Where the code is

- `engine/`: reads a PDF into sentences (`ingestion/`), plans the story's
  pictures (`story/`), and calls the AI platforms (`providers/`).
- `server/`: the API, the background worker, the sample pack, and the daily
  spending limit.
- `reader/src/`: the web app. `main.tsx` and `App.tsx` start it and choose the
  page; the rest is grouped by what it is for:

  | Folder | What is in it |
  |---|---|
  | `lib/` | The API client and the shared types |
  | `site/` | Header, logo, footer, and the dialog frame, on every page |
  | `library/` | The home page: your books, the samples, adding a book, and where a PDF's text goes |
  | `reading/` | The reader: the PDF page, narration, pictures, and their choices |
  | `study/` | The research study's console and its logging |

  Tests sit next to the files they test, as `name.test.ts`.

## Before you open a pull request

```bash
.venv/bin/python -m pytest engine server
(cd reader && npm test && npm run build)
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

- Keep each pull request to one change, with tests for what it adds or fixes.
- Write commit messages that say what changed and why.
- Never commit keys or `.env` files, and never commit books you may not share.
  The test fixtures in `fixtures/` are generated from a public-domain story.
- Match the surrounding code: plain names, short comments that explain why.

## Code of conduct and security

Everyone taking part is expected to follow the
[Code of Conduct](CODE_OF_CONDUCT.md): be kind and assume good intent.

Report security problems privately, as [SECURITY.md](SECURITY.md) describes,
not in a public issue.

## Licence

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE), the same licence as the project.
