# Running the reading study

The study build runs the within-subjects study of picture frequency and
style. Each participant reads four passages (A to D), each with a different
frequency and style, assigned by a counterbalanced schedule. Every
picture is prepared before the study, so all participants see the same
pictures at the same sentences. Everything participants do is logged.

Run the study build on the lab machine, not on a public site: its console
and its log have no sign-in.

## 1. Set it up

Put the four passages, as text-based PDFs, in one folder as `A.pdf`,
`B.pdf`, `C.pdf` and `D.pdf`. The practice passage is the bundled
*The Lantern Keeper*.

Add these settings to `.env`:

| Setting | Example | Why |
| --- | --- | --- |
| `IR_STUDY_DIR` | `/data/study/passages` | Turns the site into the study build. With Docker, use a folder inside the `/data` volume. |
| `OPENAI_API_KEY` | | Real voice and pictures, from OpenAI's models as the paper describes. |
| `IR_SPEECH_PROVIDER` | `openai` | Keeps OpenAI's voice even when `.env` also holds an OpenRouter or Gemini key. |
| `IR_DAILY_SPEND_LIMIT_USD` | `20` | Preparing four passages of about 700 words costs about $12. |
| `IR_OPENAI_SPEECH_MODEL`, `IR_IMAGE_MODEL`, `IR_STORY_MODEL` | dated snapshots | So the paper can name the exact models. Set these before preparing: changing a model draws everything again. |
| `IR_BUILD_VERSION` | the commit or tag | Recorded with every logged event. |

The study build offers four styles: Pencil sketch, Watercolour, Detailed
colour, and Hand-painted animation, whose prompt names no studio.

## 2. Prepare the passages

```bash
python -m imagine_reader_server.study prepare
python -m imagine_reader_server.study status
```

With Docker, prefix each command with `docker compose exec api`.
`prepare` adds the passages and queues every frequency and style of each
for the worker. Run it again until `status` shows 16 of 16 settings ready
for every passage; repeating it only queues what is unfinished. `--run`
does the work in that process instead of the worker. If you replace a
passage's PDF, it is read again and must be prepared again.

## 3. Screen the pictures

```bash
python -m imagine_reader_server.study export screening/
```

This copies every picture into `screening/<passage>/<words>w-<style>/`
and writes `pictures.csv` (one row per picture: its sentences, prompt, and
status), `study.json` (the build, the models, every scene), and each
passage's `visual-bible.json`. Keep these for the open materials.

Screen for failures only (blank, unreadable or unsafe pictures), not for
quality. Draw a failure again, once:

```bash
python -m imagine_reader_server.study redraw A pencil 25 sec-001-scn-004
python -m imagine_reader_server.study redraw A animated 1 --failed
```

A picture is named by its request, so a scene with the same request in
another setting changes too. Clear the lab browser's cache after redrawing.

## 4. Run the sessions

Open `/#/study` on the lab machine. Type the participant number, then
start Practice and Sessions 1 to 4 in order. Each button shows that
session's passage and pictures, and is enabled only once they are fully
prepared. `python -m imagine_reader_server.study schedule --participants 24`
prints the whole schedule.

A session opens at the start of its passage with pictures shown. The
participant cannot change the pictures or the speed; Hide pictures, pause,
and the sentence buttons stay available and are logged. When the passage
ends, the reader says so; End session (top left) returns to the console
early. A session started again is logged as attempt 2.

## 5. The log

Each participant's events go to `data/study/events/P001.jsonl` (and so on),
one JSON object per line. Every line carries:

| Field | Meaning |
| --- | --- |
| `participant`, `session`, `attempt`, `kind` | Who, which session (1 to 4), which try; `study`, `practice` or `benchmark` |
| `passage`, `style`, `words` | The condition: a new picture after `words` words (1, 25, 60 or 120) |
| `build`, `models` | `IR_BUILD_VERSION` and the narration, picture and visual-bible models |
| `event`, `data` | What happened, with its details (below) |
| `narration_ms` | Time narration had played when it happened; it stops while paused or waiting |
| `client_at`, `received_at` | The browser's and the server's clocks |

| Event | `data` |
| --- | --- |
| `session_start` / `session_end` | sentences in the passage; at the end: reason, sentences heard, pictures shown, duration |
| `sentence_start` | the sentence and its scene, as narration reaches it |
| `picture_shown` | picture, scene, the sentence on screen, `source` (`cache`: drawn before the session; `live`), style asked and `rendered_style` drawn, `drawn_at` |
| `picture_ready` | a picture drawn while the session ran: scene, `drawn_at`, `draw_seconds` |
| `narration_wait` | sentence, `reason` (`picture` or `voice`), start, duration |
| `pause`, `resume`, `replay`, `skip` | the sentence; replay and skip also give from and to |
| `pictures_hidden`, `pictures_shown` | the sentence |
| `setting_change` | benchmark only: from and to |

`rendered_style` is the drawing style. Watercolour and Detailed colour
both draw as `color`; a hand-painted scene the image service declined
shows style `animated` with `rendered_style` `color`.

## 6. Benchmark the live engine

The study's pictures are drawn ahead; the benchmark times the engine
drawing while the voice plays. Upload a multi-chapter public-domain book in
the lab browser, then clear its pictures before each run:

```bash
python -m imagine_reader_server.study clear-pictures BOOK_ID
```

In the console, start a Benchmark run with that book, a style, a frequency,
and a run name. Read across a chapter end, or change the pictures part way
through. The run's events go to `benchmark-<run name>.jsonl`. A picture's
lead time is its `drawn_at` minus the `client_at` of the `sentence_start`
for its scene's first sentence; `narration_wait` gives the stalls.
