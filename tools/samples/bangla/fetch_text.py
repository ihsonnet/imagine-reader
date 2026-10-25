"""The paragraphs of Wikisource chapter pages, without page numbers or editorial marks.

usage: fetch_text.py <chapter>.html.json ...
Each file is the JSON of api.php?action=parse&prop=text for one chapter page;
the paragraphs are written beside it as <chapter>.txt, one blank line apart.
"""

import json
import re
import sys
from html.parser import HTMLParser

SKIPPED = ("pagenum", "ws-pagenum", "mw-editsection", "reference", "ws-noexport", "wst-header", "headertemplate")


class Paragraphs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paragraphs: list[str] = []
        self.current: list[str] = []
        self.skipping = 0  # depth inside an element whose text is not the book's
        self.in_paragraph = False

    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get("class") or ""
        if self.skipping or any(name in classes for name in SKIPPED) or tag in ("style", "script", "sup"):
            self.skipping += 1
            return
        if tag == "p":
            self.in_paragraph, self.current = True, []
        elif tag == "br" and self.in_paragraph:
            self.current.append(" ")

    def handle_endtag(self, tag):
        if self.skipping:
            self.skipping -= 1
            return
        if tag == "p" and self.in_paragraph:
            text = re.sub(r"\s+", " ", "".join(self.current)).strip()
            if text:
                self.paragraphs.append(text)
            self.in_paragraph = False

    def handle_data(self, data):
        if self.in_paragraph and not self.skipping:
            self.current.append(data)


for path in sys.argv[1:]:
    parser = Paragraphs()
    parser.feed(json.load(open(path, encoding="utf-8"))["parse"]["text"]["*"])
    out = path.replace(".html.json", ".txt")
    open(out, "w", encoding="utf-8").write("\n\n".join(parser.paragraphs))
    print(out, len(parser.paragraphs), "paragraphs")
