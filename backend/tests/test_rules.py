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
from app.rules.engine import DeterministicRuleEngine, _memo_text
from app.rules.models import CheckResult, ResultStatus, RuleSource, Severity
from app.rules.normalization import normalize_text
from app.rules.registry import COMMON, COMMON_VERSION, RULES, VERIFIED_AT


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
            _reference(0, "가상저자", 2020),
            _reference(1, "또다른저자", 2021),
            _reference(2, "Zeta, Synthetic", 2020, kind="english"),
            _reference(3, "A.", 2021, kind="english"),
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
    assert {"CR-08", "CR-09"} <= rule_ids
    assert "CR-11" not in rule_ids
    assert all("하이퍼링크" not in result.finding for result in results)
    subtitle = next(result for result in results if result.rule_id == "CR-08")
    assert subtitle.severity == Severity.WARNING
    assert subtitle.rule_source.document_name.startswith("Publication Manual")


def test_cr08_distinguishes_article_and_book_capitalization() -> None:
    manuscript = _manuscript(
        [],
        [
            _reference(
                0,
                "Alpha, Synthetic",
                2020,
                kind="english",
                title="Sentence style article title",
                raw=(
                    "Alpha, Synthetic. (2020). Sentence style article title. "
                    "Journal of Examples, 2(1), 1-10. https://doi.org/10.1234/synthetic"
                ),
            ),
            _reference(
                1,
                "Beta, Synthetic",
                2021,
                kind="english",
                title="Book title with lowercase content",
                raw=(
                    "Beta, Synthetic. (2021). Book title with lowercase content. "
                    "Seoul: Synthetic Press."
                ),
            ),
            _reference(
                2,
                "Gamma, Synthetic",
                2022,
                kind="english",
                title="Another sentence style article",
                raw=(
                    "Gamma, Synthetic. (2022). Another sentence style article. "
                    "journal of Examples, 3(1), 11-20. "
                    "https://doi.org/10.1234/another"
                ),
            ),
        ],
    )
    results = DeterministicRuleEngine().evaluate(manuscript)
    capitalization = [
        result
        for result in results
        if result.rule_id == "CR-08"
        and result.rule_source.document_name == COMMON
    ]
    assert len(capitalization) == 2
    assert {result.location.reference_index for result in capitalization} == {1, 2}
    assert all(result.severity == Severity.ERROR for result in capitalization)


def test_cr11_only_checks_western_reference_author_connector() -> None:
    manuscript = _manuscript(
        [],
        [
            _reference(
                0,
                "Alpha and Beta",
                2020,
                kind="english",
                raw="Alpha and Beta. (2020). Synthetic Title. Seoul: Synthetic Press.",
            ),
            _reference(
                1,
                "Gamma",
                2021,
                kind="english",
                raw="Gamma. (2021). Research and Practice. Journal of Tests, 1(1), 1-2.",
            ),
            _reference(
                2,
                "가상과저자",
                2022,
                raw="가상과저자. (2022). 합성 제목.",
            ),
        ],
    )
    results = DeterministicRuleEngine().evaluate(manuscript)
    connectors = [result for result in results if result.rule_id == "CR-11"]
    assert len(connectors) == 1
    assert connectors[0].location.reference_index == 0
    assert connectors[0].severity == Severity.ERROR


@pytest.mark.parametrize(
    ("raw_text", "corrected"),
    [
        ("(권누리. 권현석, 2020)", "(권누리, 권현석, 2020)"),
        ("(권누리·권현석, 2020)", "(권누리, 권현석, 2020)"),
        ("(권누리; 권현석, 2020)", "(권누리, 권현석, 2020)"),
    ],
)
def test_cr13_detects_korean_coauthor_separator(raw_text: str, corrected: str) -> None:
    citation = _citation([("권누리", 2020), ("권현석", 2020)])
    citation.raw_text = raw_text
    results = DeterministicRuleEngine().evaluate(_manuscript([citation], []))
    result = next(item for item in results if item.rule_id == "CR-13")
    assert result.severity == Severity.ERROR
    assert corrected in result.memo_text
    assert "Ⅰ-6)" in result.memo_text  # noqa: RUF001 - official clause notation


@pytest.mark.parametrize(
    "raw_text",
    [
        "(김영석, 이용재, 2018)",
        "(Golder & Huberman, 2006)",
        "(오동근 외, 2010)",
        "(홍길동, 2020; 김철수, 2021)",
        "(嶺南 烈女傳, 1905)",
    ],
)
def test_cr13_ignores_valid_or_non_korean_separators(raw_text: str) -> None:
    citation = _citation([("합성저자", 2020)])
    citation.raw_text = raw_text
    results = DeterministicRuleEngine().evaluate(_manuscript([citation], []))
    assert all(result.rule_id != "CR-13" for result in results)


def test_cr13_ignores_names_and_periods_outside_parenthetical_citations() -> None:
    manuscript = _manuscript(
        [],
        [
            _reference(
                0,
                "Hoffer, J. A.",
                2020,
                kind="english",
                raw="Hoffer, J. A. (2020). 한국도서관\u2024정보학회지.",
            )
        ],
    )
    assert all(
        result.rule_id != "CR-13"
        for result in DeterministicRuleEngine().evaluate(manuscript)
    )


@pytest.mark.parametrize(
    ("raw_text", "clause"),
    [
        ("윤희윤. (2020). 문명과 매체, 그리고 도서관. 대구: 합성출판사.", "Ⅱ-2)-(1)"),
        ("윤희윤. 2020. 문명과 매체, 그리고 도서관. 대구: 합성출판사.", "Ⅱ-2)-(1)"),
        (
            "정영미, 배정희. (2015). 합성 제목. 합성학회지, 1(1), 1-10.",
            "Ⅱ-3)-(1)",
        ),
    ],
)
def test_cr14_removes_period_after_korean_author(raw_text: str, clause: str) -> None:
    reference = _reference(0, "윤희윤", 2020, raw=raw_text)
    result = next(
        item
        for item in DeterministicRuleEngine().evaluate(_manuscript([], [reference]))
        if item.rule_id == "CR-14"
    )
    assert result.severity == Severity.ERROR
    assert clause in result.memo_text
    assert "저자명 뒤 온점 삭제 필요" in result.memo_text


@pytest.mark.parametrize(
    "raw_text",
    [
        "윤희윤 (2020). 합성 제목.",
        "Caplan, P. (2003). Synthetic title.",
        "Hoffer, J. A., George, J., & Valacich, J. S. (1996). Synthetic title.",
        "남태우, 류반디 (2012a). 합성 제목.",
    ],
)
def test_cr14_ignores_valid_korean_and_western_author_forms(raw_text: str) -> None:
    kind = "english" if raw_text[0].isascii() else "korean"
    reference = _reference(0, "합성저자", 2020, kind=kind, raw=raw_text)
    assert all(
        result.rule_id != "CR-14"
        for result in DeterministicRuleEngine().evaluate(_manuscript([], [reference]))
    )


def test_reference_group_hangul_and_year_order_use_original_positions() -> None:
    references = [
        _reference(0, "Alpha", 2020, kind="english"),
        _reference(1, "나저자", 2021),
        _reference(2, "가저자", 2020),
        _reference(3, "가저자", 2019),
    ]
    results = DeterministicRuleEngine().evaluate(_manuscript([], references))
    by_rule = {result.rule_id: result for result in results}
    assert by_rule["CR-15"].location.reference_index == 1
    assert by_rule["CR-16"].location.reference_index == 2
    assert by_rule["CR-17"].location.reference_index == 3
    assert "참고문헌 4번째 항목" in by_rule["CR-17"].memo_text


def test_same_author_same_year_requires_ordered_suffixes() -> None:
    references = [
        _reference(0, "가저자", 2020, raw="가저자 (2020). 첫 번째 합성 제목."),
        _reference(1, "가저자", 2020, raw="가저자 (2020). 두 번째 합성 제목."),
    ]
    results = DeterministicRuleEngine().evaluate(_manuscript([], references))
    assert any(result.rule_id == "CR-18" for result in results)

    references[0].raw_text = "가저자 (2020a). 첫 번째 합성 제목."
    references[1].raw_text = "가저자 (2020b). 두 번째 합성 제목."
    results = DeterministicRuleEngine().evaluate(_manuscript([], references))
    assert all(result.rule_id != "CR-18" for result in results)


def test_many_reference_order_errors_are_summarized() -> None:
    references = [
        _reference(0, "다저자", 2022),
        _reference(1, "나저자", 2021),
        _reference(2, "가저자", 2020),
    ]
    results = DeterministicRuleEngine(reference_order_summary_threshold=1).evaluate(
        _manuscript([], references)
    )
    order_results = [
        result for result in results if result.rule_id in {"CR-15", "CR-16", "CR-17"}
    ]
    assert len(order_results) == 1
    assert order_results[0].rule_id == "CR-15"
    assert "목록 전체" in order_results[0].memo_text


def test_repeated_review_items_are_summarized_by_rule() -> None:
    references = [
        _reference(
            index,
            f"Author{index}",
            2020 + index,
            kind="english",
            title=f"Synthetic article {index}",
            raw=(
                f"Author{index}. ({2020 + index}). Synthetic article {index}. "
                f"Journal of Examples, 1(1), {index + 1}-{index + 2}."
            ),
            doi=None,
        )
        for index in range(6)
    ]
    results = DeterministicRuleEngine(review_repeat_summary_threshold=5).evaluate(
        _manuscript([], references)
    )
    doi_reviews = [result for result in results if result.rule_id == "CR-10"]
    assert len(doi_reviews) == 1
    assert doi_reviews[0].memo_text.startswith("참고문헌 목록 전체\n")
    assert "6건" in doi_reviews[0].memo_text


def test_inferred_reference_section_skips_order_checks() -> None:
    manuscript = _manuscript(
        [],
        [
            _reference(0, "나저자", 2021),
            _reference(1, "가저자", 2020),
        ],
    )
    manuscript.reference_section_method = "inferred"
    results = DeterministicRuleEngine().evaluate(manuscript)
    assert not {result.rule_id for result in results} & {
        "CR-15",
        "CR-16",
        "CR-17",
        "CR-18",
        "CR-19",
    }


def test_hangul_names_follow_ga_na_da_collation() -> None:
    names = ["가저자", "김저자", "나저자"]
    assert names == sorted(names)


def test_normalizes_unicode_whitespace_punctuation_and_width() -> None:
    assert normalize_text("\uff21 lpha,  테스트") == normalize_text("Alpha 테스트")


def test_verified_rule_evidence_and_unverified_safety() -> None:
    expected_clauses = {
        "CR-01": "Ⅱ-1)-(1)",
        "CR-02": "Ⅱ-1)-(1)",
        "CR-03": "Ⅱ-1)-(1)",
        "CR-04": "Ⅰ-9)",  # noqa: RUF001 - official clause notation
        "CR-05": "Ⅰ-7)",  # noqa: RUF001 - official clause notation
        "CR-06": "Ⅱ-9)",
        "CR-07": "Ⅱ-9) + Ⅱ-10)",
        "CR-08": "Ⅱ-1)-(5)",
        "CR-09": "Ⅱ-6)-(1)",
        "CR-10": "Ⅱ-3)-(1)",
        "CR-11": "Ⅱ-1)-(4)",
        "CR-13": "Ⅰ-6)",  # noqa: RUF001 - official clause notation
        "CR-14": "Ⅱ-2)-(1)",
        "CR-15": "Ⅱ-1)-(1)",
        "CR-16": "Ⅱ-1)-(2)",
        "CR-17": "Ⅱ-1)-(2)",
        "CR-18": "Ⅱ-3)-(2)",
        "CR-19": "Ⅱ-1)-(1)",
    }
    for rule_id, clause in expected_clauses.items():
        source = RULES[rule_id].source
        assert source.document_name == COMMON
        assert source.version_or_published_at == COMMON_VERSION
        assert source.clause_number == clause
        assert source.verified_at == VERIFIED_AT
        assert source.verified is (rule_id != "CR-06")
    assert RULES["CR-06"].severity == Severity.WARNING
    assert RULES["CR-06"].effective_severity() == Severity.NEEDS_REVIEW
    assert RULES["CR-01"].effective_severity() == Severity.ERROR

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
            rule_id="CR-06",
            rule_source=RULES["CR-06"].source,
            confidence=1,
            sort_key=(0, 0, 0, -1),
        )

    result = next(
        item
        for item in DeterministicRuleEngine().evaluate(
            _manuscript([], [_reference(0, "가상저자", 2020)])
        )
        if item.rule_id == "CR-06"
    )
    assert result.category.value == "확인 필요"
    assert result.severity == Severity.NEEDS_REVIEW
    assert result.status == ResultStatus.NEEDS_CONTEXT


def test_memo_contains_actual_clause_and_rule_id() -> None:
    manuscript = _manuscript(
        [_citation([("Missing", 2020)])],
        [_reference(0, "Alpha", 2021)],
    )
    result = next(
        item for item in DeterministicRuleEngine().evaluate(manuscript)
        if item.rule_id == "CR-01"
    )
    assert f"{COMMON} Ⅱ-1)-(1)" in result.memo_text
    assert "CR-01" not in result.memo_text


def test_all_rule_memos_use_anonymous_nominal_style() -> None:
    forbidden = (
        "주세요",
        "바랍니다",
        "하십시오",
        "것 같",
        "보입니다",
        "제가",
        "저희",
        "우리 학회",
        "아쉽게도",
        "유감스럽게",
        "잘못된",
    )
    for rule in RULES.values():
        memo = _memo_text(rule, location=_location(0))
        assert memo.splitlines()[0].startswith("본문 1번째 문단")
        assert memo.splitlines()[-1].startswith("(근거:")
        assert not any(expression in memo for expression in forbidden)
        assert not rule.memo_template.splitlines()[0].endswith(".")


def test_generated_memo_keeps_location_action_and_evidence_on_separate_lines() -> None:
    reference = _reference(
        0,
        "윤희윤",
        2020,
        raw="윤희윤. (2020). 합성 제목.",
    )
    result = next(
        item
        for item in DeterministicRuleEngine().evaluate(_manuscript([], [reference]))
        if item.rule_id == "CR-14"
    )
    lines = result.memo_text.splitlines()
    assert lines[0].startswith("참고문헌 1번째 항목")
    assert lines[1] == "저자명 뒤 온점 삭제 필요"
    assert lines[2] == "수정 예: 윤희윤 (2020)."
    assert lines[3].startswith("(근거:")
