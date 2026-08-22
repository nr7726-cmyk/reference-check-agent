from __future__ import annotations

import re
import unicodedata

from app.extraction.models import (
    AuthorName,
    Citation,
    CitationMention,
    ExtractedDocument,
    Location,
    Paragraph,
    ParsedManuscript,
    ReferenceItem,
)

REFERENCE_HEADING = re.compile(
    r"^\s*(참고\s*문헌|참고자료|references|english\s+references|영문\s*참고\s*문헌)\s*$",
    re.IGNORECASE,
)
ENGLISH_HEADING = re.compile(r"(english|references|영문)", re.IGNORECASE)
PAREN_CITATION = re.compile(r"\(([^()]*?(?:19|20)\d{2}[^()]*)\)")
NARRATIVE_CITATION = re.compile(
    r"(?P<author>[가-힣]{2,8}|[A-Z][A-Za-z'\u2019\u2013-]+"
    r"(?:\s+(?:et\s+al\.|and\s+[A-Z][A-Za-z'\u2019\u2013-]+))?)"
    r"\s*\((?P<year>(?:19|20)\d{2})\)"
)
MENTION = re.compile(
    r"(?P<author>[가-힣]{2,8}|[A-Z][A-Za-z'\u2019\u2013-]+(?:\s+et\s+al\.)?)"
    r"\s*,?\s*(?P<year>(?:19|20)\d{2})"
)
YEAR = re.compile(r"[\(\[]((?:19|20)\d{2})[a-z]?[\)\]]")
DOI = re.compile(r"(?:https?://doi\.org/|doi:\s*)?(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", re.I)
URL = re.compile(r"https?://[^\s<>\]]+", re.I)


def parse_manuscript(document: ExtractedDocument) -> ParsedManuscript:
    heading_index = _find_reference_heading(document)
    body = document.paragraphs[:heading_index]
    reference_paragraphs = document.paragraphs[heading_index:]
    citations = extract_citations(body)
    references = extract_references(reference_paragraphs)
    return ParsedManuscript(document=document, citations=citations, references=references)


def _find_reference_heading(document: ExtractedDocument) -> int:
    for index, paragraph in enumerate(document.paragraphs):
        if REFERENCE_HEADING.fullmatch(paragraph.text):
            return index
    return len(document.paragraphs)


def extract_citations(paragraphs: list[Paragraph]) -> list[Citation]:
    citations: list[Citation] = []
    for paragraph in paragraphs:
        matches: list[tuple[int, str, list[CitationMention]]] = []
        for match in PAREN_CITATION.finditer(paragraph.text):
            mentions = _parse_mentions(match.group(1))
            if mentions:
                matches.append((match.start(), match.group(0), mentions))
        for match in NARRATIVE_CITATION.finditer(paragraph.text):
            matches.append(
                (
                    match.start(),
                    match.group(0),
                    [
                        CitationMention(
                            author=match.group("author").strip(), year=int(match.group("year"))
                        )
                    ],
                )
            )
        for offset, raw_text, mentions in sorted(matches, key=lambda item: item[0]):
            location = paragraph.location.model_copy(update={"run_index": offset})
            citations.append(
                Citation(
                    id=f"cit-{len(citations)}",
                    raw_text=raw_text,
                    mentions=mentions,
                    location=location,
                )
            )
    return citations


def _parse_mentions(text: str) -> list[CitationMention]:
    mentions: list[CitationMention] = []
    for segment in text.split(";"):
        match = MENTION.search(segment)
        if not match:
            continue
        author = match.group("author").strip()
        mentions.append(CitationMention(author=author, year=int(match.group("year"))))
        for year in re.findall(r"(?:19|20)\d{2}", segment[match.end() :]):
            mentions.append(CitationMention(author=author, year=int(year)))
    return mentions


def extract_references(paragraphs: list[Paragraph]) -> list[ReferenceItem]:
    references: list[ReferenceItem] = []
    list_kind = "korean"
    for paragraph in paragraphs:
        if REFERENCE_HEADING.fullmatch(paragraph.text):
            if ENGLISH_HEADING.search(paragraph.text):
                list_kind = "english"
            continue
        if not YEAR.search(paragraph.text):
            continue
        index = len(references)
        location = Location(
            **paragraph.location.model_dump(exclude={"reference_index", "display_hint"}),
            reference_index=index,
            display_hint=paragraph.text[:80],
        )
        references.append(_parse_reference(paragraph.text, location, index, list_kind))
    return references


def _parse_reference(text: str, location: Location, index: int, list_kind: str) -> ReferenceItem:
    year_match = YEAR.search(text)
    assert year_match is not None
    author_text = text[: year_match.start()].strip(" .")
    remainder = text[year_match.end() :].strip(" .")
    title = remainder.split(".", 1)[0].strip() or None
    author_parts = re.split(r"\s*(?:,|;|&|\band\b|와|과)\s*", author_text)
    authors = [
        AuthorName(raw=author, normalized=_normalize_name(author))
        for author in author_parts
        if author
    ]
    doi_match = DOI.search(text)
    url_match = URL.search(text)
    return ReferenceItem(
        id=f"ref-{index}",
        list_kind="english" if list_kind == "english" else "korean",
        reference_index=index,
        raw_text=text,
        authors=authors,
        year=int(year_match.group(1)),
        title=title,
        doi=doi_match.group(1).rstrip(".,)") if doi_match else None,
        url=url_match.group(0).rstrip(".,)") if url_match else None,
        location=location,
    )


def _normalize_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())
