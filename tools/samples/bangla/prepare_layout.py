"""Write book.json (the book's contents) and layout.html (the page design) for layout-pages.mjs.

usage: prepare_layout.py <book spec>.json <text dir> <build dir> <dir with the Noto Serif Bengali woff2 files>
The spec names the book, its chapters' .txt files in the text dir, and any corrections checked against the printed
scan.
"""

import json
import re
import sys

spec_path, source, build, fonts = sys.argv[1:5]
spec = json.load(open(spec_path, encoding="utf-8"))


def corrected(text):
    """The text with each correction made; each must be found exactly once, so a changed source is noticed."""
    for fix in spec.get("fixes", []):
        if text.count(fix["from"]) != 1:
            sys.exit(f"correction on page {fix['page']} found {text.count(fix['from'])} times: {fix['from']!r}")
        text = text.replace(fix["from"], fix["to"])
    return text


def paragraphs(name):
    paras = corrected(open(f"{source}/{name}", encoding="utf-8").read()).split("\n\n")[1:]  # the first is the heading
    return [re.sub(r"([।?!])(?=[^\s\"”’—)])", r"\1 ", p) for p in paras]  # a page break joined two paragraphs


book = {
    "title": spec["title"],
    "author": spec["author"],
    "note": spec["note"],
    "subject": spec["subject"],
    "pdf": spec["pdf"],
    "title_page": spec.get("title_page"),
    "chapters": [{"title": c["title"], "paragraphs": paragraphs(c["text"])} for c in spec["chapters"]],
}
json.dump(book, open(f"{build}/book.json", "w", encoding="utf-8"), ensure_ascii=False)
face = 'font-family: "Noto Serif Bengali"; font-weight: {w}; src: url("file://{fonts}/noto-serif-bengali-{s}-{w}-normal.woff2")'
LATIN_RANGE = "; unicode-range: U+0000-00FF, U+2000-206F"
faces = "\n".join(
    "@font-face { " + face.format(w=w, s=s, fonts=fonts) + (LATIN_RANGE if s == "latin" else "") + "; }"
    for w in ("400", "700")
    for s in ("bengali", "latin")
)
open(f"{build}/layout.html", "w", encoding="utf-8").write(
    f"""<!doctype html><html lang="bn"><head><meta charset="utf-8"><style>
{faces}
body {{ margin: 0; background: #888; font-family: "Noto Serif Bengali", serif; color: #111; }}
.page {{ position: relative; width: 487px; height: 765px; margin: 0 0 20px; background: #fff; overflow: hidden; }}
.content {{ position: absolute; inset: 64px 57px 72px; }}
.w {{ white-space: nowrap; }}
p {{ margin: 0 0 0.25em; font-size: 14px; line-height: 1.75; text-align: justify; text-indent: 1.4em; }}
p.cont {{ text-indent: 0; }}
h2 {{ margin: 44px 0 30px; font-size: 22.5px; font-weight: 700; text-align: center; }}
.title-page {{ display: flex; flex-direction: column; justify-content: center; align-items: center;
  height: 100%; text-align: center; }}
.title-page h1 {{ margin: 0; font-size: 40px; font-weight: 700; }}
.title-page .author {{ margin: 22px 0 0; font-size: 20px; font-weight: 700; text-indent: 0; }}
.title-page .note {{ margin: 100px 10px 0; font-size: 11.5px; line-height: 1.6; text-align: center;
  text-indent: 0; color: #333; }}
</style></head><body></body></html>"""
)
