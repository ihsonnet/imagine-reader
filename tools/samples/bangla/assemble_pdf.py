"""Assemble the sample: the real title page if it has one, then each laid-out page as a picture with its words as
invisible text.

usage: assemble_pdf.py <build dir> <dir with the Noto Serif Bengali TTFs>
The build dir holds book.json from prepare_layout.py, page-NN.png and words.json from layout-pages.mjs, and the
title page's scan when the spec names one. The PDF is written there under the spec's file name.
"""

import json
import re
import sys

from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

build, font_dir = sys.argv[1], sys.argv[2]
PX = 0.75  # points per CSS pixel
book = json.load(open(f"{build}/book.json", encoding="utf-8"))
layout = json.load(open(f"{build}/words.json", encoding="utf-8"))
W, H = layout["width"] * PX, layout["height"] * PX
BENGALI = "ঀ-৿‌‍।॥"
SCRIPT = re.compile(rf"[{BENGALI}]+|[^{BENGALI}]+")

fonts = {}
for weight in ("400", "700"):
    for subset in ("bengali", "latin"):
        fonts[(subset, weight)] = name = f"NotoSerifBengali-{subset}-{weight}"
        pdfmetrics.registerFont(TTFont(name, f"{font_dir}/noto-serif-bengali-{subset}-{weight}-normal.ttf"))


def runs(text: str, weight: str) -> list[tuple[str, str]]:
    """The text in pieces, each with the font (Bengali or Latin part) that has its characters."""
    return [
        (fonts[("bengali" if re.match(rf"[{BENGALI}]", run) else "latin", weight)], run) for run in SCRIPT.findall(text)
    ]


pdf = canvas.Canvas(f"{build}/{book['pdf']}", pagesize=(W, H))
pdf.setTitle(book["title"])
pdf.setAuthor(book["author"])
pdf.setSubject(book["subject"])
if book["title_page"]:
    cover = ImageReader(f"{build}/{book['title_page']}")
    cw, ch = cover.getSize()
    scale = min(W / cw, H / ch)
    pdf.drawImage(cover, (W - cw * scale) / 2, (H - ch * scale) / 2, cw * scale, ch * scale)
    pdf.showPage()

for index, words in enumerate(layout["pages"], start=1):
    pdf.drawImage(f"{build}/page-{index:02d}.png", 0, 0, W, H)
    for word in words:
        weight = "700" if word["size"] >= 20 else "400"
        size = word["size"] * PX
        pieces = runs(word["text"], weight)
        natural = sum(pdfmetrics.stringWidth(text, font, size) for font, text in pieces) or 1
        text = pdf.beginText()
        text.setTextRenderMode(3)  # invisible: the picture underneath shows the words
        text.setHorizScale(100 * word["w"] * PX / natural)  # exactly as wide as the word in the picture
        text.setTextOrigin(word["x"] * PX, H - (word["y"] + word["h"] * 0.74) * PX)
        for font, piece in pieces:
            text.setFont(font, size)
            text.textOut(piece)
        text.setFont(fonts[("latin", weight)], size)
        text.textOut(" ")  # a real space between words, however tight the line
        pdf.drawText(text)
    pdf.showPage()
pdf.save()
