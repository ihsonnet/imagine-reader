#!/usr/bin/env python3
"""Print a short Bangla story the way Google Docs does, to test reading its words back.

Chrome and Google Docs print with Skia, which labels each shaped glyph only
with the letter the font's character map names: the PDF's own text has vowel
signs before their consonants and conjuncts missing. The story is set in
Noto Sans Bengali Regular, an instance of the variable font the engine ships.

Usage (from the repository root, with Chrome or Chromium installed):

    .venv/bin/python fixtures/generate_shaped_pdf.py /path/to/chrome
"""

from __future__ import annotations

import base64
import html
import io
import subprocess
import sys
import tempfile
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parent
STORY = ROOT / "stories" / "rater-train.txt"
OUT = ROOT / "pdfs" / "bangla-shaped.pdf"
FONT = ROOT.parent / "engine" / "src" / "imagine_reader" / "ingestion" / "fonts" / "NotoSansBengali[wdth,wght].ttf"


def regular() -> bytes:
    font = instancer.instantiateVariableFont(TTFont(FONT), {"wght": 400, "wdth": 100})
    buffer = io.BytesIO()
    font.save(buffer)
    return buffer.getvalue()


def page() -> str:
    title, *paragraphs = [p.strip() for p in STORY.read_text(encoding="utf-8").split("\n\n") if p.strip()]
    font = base64.b64encode(regular()).decode()
    body = "".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)
    return f"""<!doctype html><meta charset="utf-8"><style>
@font-face {{ font-family: Bangla; src: url(data:font/ttf;base64,{font}); }}
@page {{ size: A5; margin: 18mm; }}
body {{ font: 13px/1.7 Bangla; }} p {{ margin: 0 0 26px; }}
h1 {{ font-size: 20px; font-weight: normal; text-align: center; }}
</style><h1>{html.escape(title)}</h1>{body}"""


def main(chrome: str) -> None:
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "story.html"
        source.write_text(page(), encoding="utf-8")
        subprocess.run(
            [chrome, "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
             f"--print-to-pdf={OUT}", source.as_uri()],
            check=True, capture_output=True,
        )  # fmt: skip
    print(f"wrote {OUT.relative_to(ROOT.parent)}")


if __name__ == "__main__":
    main(sys.argv[1])
