"""The Noto Serif Bengali TTFs assemble_pdf.py embeds, from the fontsource web fonts.

usage: fonts_to_ttf.py <dir with the Noto Serif Bengali woff files> <out dir>
The Latin part is renamed, so a PDF reader does not confuse its character map with the Bengali part's.
The .woff files are read, as .woff2 would need brotli; their outlines are the same.
"""

import sys

from fontTools.ttLib import TTFont

source, out = sys.argv[1:3]
for weight, style in (("400", "Regular"), ("700", "Bold")):
    for subset in ("bengali", "latin"):
        font = TTFont(f"{source}/noto-serif-bengali-{subset}-{weight}-normal.woff")
        font.flavor = None
        if subset == "latin":
            names = font["name"]
            names.setName("Noto Serif Bengali Latin", 1, 3, 1, 0x409)
            names.setName(f"Noto Serif Bengali Latin {style}", 4, 3, 1, 0x409)
            names.setName(f"NotoSerifBengaliLatin-{style}", 6, 3, 1, 0x409)
        font.save(f"{out}/noto-serif-bengali-{subset}-{weight}-normal.ttf")
