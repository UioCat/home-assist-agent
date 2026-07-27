import re
import unicodedata


_WHITESPACE = re.compile(r"\s+")


def normalize_term(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    previous = None
    while normalized != previous:
        previous = normalized
        normalized = normalized.strip()
        while normalized and unicodedata.category(normalized[0]).startswith("P"):
            normalized = normalized[1:]
        while normalized and unicodedata.category(normalized[-1]).startswith("P"):
            normalized = normalized[:-1]
    return _WHITESPACE.sub(" ", normalized.strip())
