from __future__ import annotations

import re
import unicodedata
from typing import Literal

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
    r"^(?:참\s*고\s*문\s*헌|參考文獻|인용\s*문헌|참고\s*자료|"
    r"references|bibliography|english\s+references|영문\s*참\s*고\s*문\s*헌)$",
    re.IGNORECASE,
)
ENGLISH_HEADING = re.compile(r"(english|references|bibliography|영문)", re.IGNORECASE)
HEADING_PREFIX = re.compile(
    r"^\s*(?:[0-9IVXLC\u2160-\u2169]+[\s.)·:-]+)?",
    re.IGNORECASE,
)
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
REFERENCE_YEAR = re.compile(
    r"(?:[\(\[]((?:19|20)\d{2})[a-z]?[\)\]]|"
    r"(?<!\d)((?:19|20)\d{2})[a-z]?\.)"
)
REFERENCE_ENTRY = re.compile(
    r"^.{1,120}?(?:\s+[\(\[](?:19|20)\d{2}[a-z]?[\)\]]\.|"
    r"\.\s+(?:19|20)\d{2}[a-z]?\.)\s+\S+",
    re.DOTALL,
)
DOI = re.compile(r"(?:https?://doi\.org/|doi:\s*)?(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", re.I)
URL = re.compile(r"https?://[^\s<>\]]+", re.I)


def parse_manuscript(document: ExtractedDocument) -> ParsedManuscript:
    reference_index, reference_method = _find_reference_section(document)
    reference_section_found = reference_index is not None
    split_index = reference_index if reference_index is not None else len(document.paragraphs)
    body = document.paragraphs[:split_index]
    reference_paragraphs = document.paragraphs[split_index:]
    citations = extract_citations(body)
    references = extract_references(reference_paragraphs)
    character_count = sum(len(paragraph.text) for paragraph in document.paragraphs)
    body_text_sufficient = len(document.paragraphs) >= 10 or character_count >= 500
    warnings: list[str] = []
    if not reference_section_found:
        warnings.append(
            f"참고문헌 목록을 자동으로 찾지 못했습니다. 본문 인용 {len(citations)}건만 추출했습니다"
        )
    if not body_text_sufficient:
        warnings.append(
            "논문 본문을 충분히 찾지 못했습니다. 표·도형 중심 문서 또는 "
            "표지·양식 파일인지 확인해 주세요"
        )
    return ParsedManuscript(
        document=document,
        citations=citations,
        references=references,
        reference_section_found=reference_section_found,
        reference_section_method=reference_method,
        body_text_sufficient=body_text_sufficient,
        warnings=warnings,
    )


def _find_reference_section(
    document: ExtractedDocument,
) -> tuple[int | None, Literal["heading", "inferred", "missing"]]:
    for index, paragraph in enumerate(document.paragraphs):
        if _is_reference_heading(paragraph.text) and _references_follow(
            document.paragraphs, index + 1, minimum=1
        ):
            return index, "heading"
    inferred = _infer_reference_start(document.paragraphs)
    return (inferred, "inferred") if inferred is not None else (None, "missing")


def _is_reference_heading(text: str) -> bool:
    candidate = HEADING_PREFIX.sub("", text).strip().rstrip(":\uff1a").strip()
    return len(candidate) <= 30 and REFERENCE_HEADING.fullmatch(candidate) is not None


def _is_reference_entry(text: str) -> bool:
    candidate = text.strip()
    if REFERENCE_ENTRY.match(candidate) is None:
        return False
    return re.search(r"(?:하였다|한다|이다|라고|이었다|였다)[.!?]?$", candidate) is None


def _references_follow(
    paragraphs: list[Paragraph],
    start: int,
    *,
    minimum: int,
    window: int = 12,
) -> bool:
    return (
        sum(
            _is_reference_entry(paragraph.text)
            for paragraph in paragraphs[start : start + window]
        )
        >= minimum
    )


def _infer_reference_start(paragraphs: list[Paragraph]) -> int | None:
    if len(paragraphs) < 4:
        return None
    search_start = len(paragraphs) // 2
    candidates = [
        index
        for index in range(search_start, len(paragraphs))
        if _is_reference_entry(paragraphs[index].text)
    ]
    for position, index in enumerate(candidates):
        nearby = candidates[position : position + 3]
        if len(nearby) >= 2 and nearby[1] - index <= 4:
            return index
    return None


def extract_citations(paragraphs: list[Paragraph]) -> list[Citation]:
    citations: list[Citation] = []
    occurrences: dict[str, int] = {}
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
            occurrences[raw_text] = occurrences.get(raw_text, 0) + 1
            context_start = max(0, offset - 24)
            context_end = min(len(paragraph.text), offset + len(raw_text) + 24)
            context = paragraph.text[context_start:context_end]
            location = paragraph.location.model_copy(
                update={
                    "run_index": offset,
                    "display_hint": (
                        f"본문 인용 {raw_text} · {occurrences[raw_text]}번째 출현 · {context}"
                    )[:80],
                }
            )
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
        years = [int(year) for year in re.findall(r"(?:19|20)\d{2}", segment)]
        if not years:
            continue
        year_start = re.search(r"(?:19|20)\d{2}", segment)
        assert year_start is not None
        author_area = segment[: year_start.start()].strip(" ,")
        author = _primary_citation_author(author_area)
        if author is None:
            match = MENTION.search(segment)
            if match is None:
                continue
            author = match.group("author").strip()
        mentions.extend(CitationMention(author=author, year=year) for year in years)
    return mentions


def _primary_citation_author(author_area: str) -> str | None:
    korean = re.match(r"([가-힣]{2,8})(?:\s*(?:,|외\b|;|[.·\u2024ㆍ/]))", author_area)
    if korean:
        return korean.group(1)
    western = re.match(r"([A-Z][A-Za-z'\u2019\u2013-]+)", author_area)
    return western.group(1) if western else None


def extract_references(paragraphs: list[Paragraph]) -> list[ReferenceItem]:
    references: list[ReferenceItem] = []
    list_kind = "korean"
    for paragraph in paragraphs:
        if _is_reference_heading(paragraph.text):
            if ENGLISH_HEADING.search(paragraph.text):
                list_kind = "english"
            continue
        if not REFERENCE_YEAR.search(paragraph.text):
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
    year_match = REFERENCE_YEAR.search(text)
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
        year=int(year_match.group(1) or year_match.group(2)),
        title=title,
        doi=doi_match.group(1).rstrip(".,)") if doi_match else None,
        url=url_match.group(0).rstrip(".,)") if url_match else None,
        location=location,
    )


def _normalize_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())
