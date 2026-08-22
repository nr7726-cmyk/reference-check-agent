from datetime import date
from typing import Optional

import pytest

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
from app.rules.engine import DeterministicRuleEngine
from app.rules.models import CheckResult, ResultStatus, RuleSource, Severity
from app.rules.normalization import normalize_text
from app.rules.registry import RULES


def _location(paragraph: int, reference: Optional[int] = None) -> Location:
    return Location(
        format="hwpx",
        section_label="Section0",
        section_index=0,
        paragraph_index=paragraph,
        run_index=0,
        reference_index=reference,
    )


def _reference(
    index: int,
    author: str,
    year: int,
    *,
    kind: str = "korean",
    title: str = "Synthetic title",
    raw: Optional[str] = None,
    doi: Optional[str] = "10.1234/synthetic",
) -> ReferenceItem:
    return ReferenceItem(
        id=f"ref-{index}",
        list_kind=kind,
        reference_index=index,
        raw_text=raw or f"{author}. ({year}). {title}.",
        authors=[AuthorName(raw=author, normalized=normalize_text(author))],
        year=year,
        title=title,
        doi=doi,
        location=_location(index + 2, index),
    )


def _manuscript(citations: list[Citation], references: list[ReferenceItem]) -> ParsedManuscript:
    paragraph = Paragraph(text="합성 본문", location=_location(0))
    return ParsedManuscript(
        document=ExtractedDocument(format="hwpx", paragraphs=[paragraph], page_count=1),
        citations=citations,
        references=references,
    )


def _citation(mentions: list[tuple[str, int]]) -> Citation:
    return Citation(
        id="cit-0",
        raw_text="; ".join(f"{author}, {year}" for author, year in mentions),
        mentions=[CitationMention(author=author, year=year) for author, year in mentions],
        location=_location(0),
    )


def test_different_authors_follow_reference_order() -> None:
    manuscript = _manuscript(
        [_citation([("Beta", 2020), ("Alpha", 2021)])],
        [_reference(0, "Alpha", 2021), _reference(1, "Beta", 2020)],
    )
    results = DeterministicRuleEngine().evaluate(manuscript)
    assert any(result.rule_id == "CR-04" for result in results)


def test_same_author_compound_citation_is_chronological() -> None:
    manuscript = _manuscript(
        [_citation([("Alpha", 2021), ("Alpha", 2020)])],
        [_reference(0, "Alpha", 2020), _reference(1, "Alpha", 2021)],
    )
    results = DeterministicRuleEngine().evaluate(manuscript)
    assert any(result.rule_id == "CR-05" for result in results)


def test_requires_english_conversion_full_names_and_alphabetical_order() -> None:
    missing = _manuscript([], [_reference(0, "가상저자", 2020)])
    assert any(result.rule_id == "CR-06" for result in DeterministicRuleEngine().evaluate(missing))

    english = _manuscript(
        [],
        [
            _reference(0, "Zeta, Synthetic", 2020, kind="english"),
            _reference(1, "A.", 2021, kind="english"),
        ],
    )
    results = DeterministicRuleEngine().evaluate(english)
    assert sum(result.rule_id == "CR-07" for result in results) == 2


def test_safety_regressions() -> None:
    manuscript = _manuscript(
        [_citation([("Alpha", 2020)])],
        [
            _reference(
                0,
                "Alpha and Beta",
                2020,
                title="Main title: lowercase subtitle",
                raw="Alpha and Beta. (2020). Main title: lowercase subtitle. 출처 : https://x.invalid",
            )
        ],
    )
    results = DeterministicRuleEngine().evaluate(manuscript)
    rule_ids = {result.rule_id for result in results}
    assert {"CR-08", "CR-09", "CR-11"} <= rule_ids
    assert all("하이퍼링크" not in result.finding for result in results)
    context = next(result for result in results if result.rule_id == "CR-11")
    assert context.status == ResultStatus.NEEDS_CONTEXT


def test_normalizes_unicode_whitespace_punctuation_and_width() -> None:
    assert normalize_text("\uff21 lpha,  테스트") == normalize_text("Alpha 테스트")


def test_unverified_rule_cannot_create_error() -> None:
    assert RULES["CR-01"].source.verified is False
    assert RULES["CR-01"].effective_severity() == Severity.NEEDS_REVIEW
    source = RuleSource(
        document_name="Synthetic rule",
        version_or_published_at="1",
        clause_number="1",
        section_title="Synthetic",
        verified=True,
        verified_at=date(2026, 1, 1),
    )
    assert source.verified is True

    with pytest.raises(ValueError, match="unverified rules"):
        CheckResult(
            id="invalid",
            category="누락",
            severity="오류",
            status="detected",
            location=_location(0),
            finding="synthetic",
            memo_text="synthetic",
            rule_id="CR-01",
            rule_source=RULES["CR-01"].source,
            confidence=1,
            sort_key=(0, 0, 0, -1),
        )

    result = DeterministicRuleEngine().evaluate(_manuscript([], [_reference(0, "Alpha", 2020)]))[0]
    assert result.category.value == "확인 필요"
    assert result.severity == Severity.NEEDS_REVIEW
    assert result.status == ResultStatus.NEEDS_CONTEXT
