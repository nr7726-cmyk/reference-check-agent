import re
import unicodedata

PUNCTUATION = re.compile(
    r"[\s\.,;:!?()\[\]{}'\"\u201c\u201d\u2018\u2019"
    r"\u00b7\u318d\-\u2013\u2014_/]+"
)


def normalize_text(value: str) -> str:
    width_normalized = unicodedata.normalize("NFKC", value)
    nfc = unicodedata.normalize("NFC", width_normalized)
    return PUNCTUATION.sub("", nfc.casefold())


def normalize_name(value: str) -> str:
    return normalize_text(value.replace("et al.", "").replace("등", ""))


def first_author_key(value: str) -> str:
    return normalize_name(re.split(r",|;|&|\band\b|와|과", value, maxsplit=1)[0])
