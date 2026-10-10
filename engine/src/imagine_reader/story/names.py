"""Finding names in text, in English and in Bangla.

Bangla has no capital letters, its vowel signs are not word characters to
Python's \\b, and it writes case endings onto names ("দেবদাসকে", "পার্বতীর").
A name matches at the start of a word and may be followed by one of those
endings; in other scripts it must end where the word ends.
"""

import re

LETTERS = r"\wঀ-৿‌‍"  # word characters, with every Bengali letter and sign
_BANGLA = re.compile(r"[ঀ-৿]")
# Endings Bangla writes onto a name: of, to, plural, also, only, and the article.
BANGLA_ENDINGS = r"(?:ের|েরা|েদের|র|রা|দের|কে|কেই|ও|ই|য়|টি|টা)?"


def name_pattern(name: str) -> str:
    """A regular expression that finds this name as a word (with a Bangla ending, for a Bangla name)."""
    ending = BANGLA_ENDINGS if _BANGLA.search(name) else ""
    return rf"(?<![{LETTERS}]){re.escape(name)}{ending}(?![{LETTERS}])"


def has_cases(text: str) -> bool:
    """Whether the text is in a script with capital letters (Latin, Greek, Cyrillic), not Bangla."""
    return any(ch.isalpha() and ch.lower() != ch.upper() for ch in text)
