import pytest

from app.extraction.citations import parse_manuscript
from app.extraction.models import ExtractedDocument, Location, Paragraph
from app.rules.engine import DeterministicRuleEngine


def _paragraph(index: int, text: str) -> Paragraph:
    return Paragraph(
        text=text,
        location=Location(
            format="hwpx",
            section_label="Section0",
            section_index=0,
            paragraph_index=index,
            run_index=0,
        ),
    )


def test_extracts_korean_english_citations_and_reference_fields() -> None:
    document = ExtractedDocument(
        format="hwpx",
        page_count=2,
        paragraphs=[
            _paragraph(0, "가상저자(2020)와 (Synthetic, 2021)를 비교했다."),
            _paragraph(1, "참고문헌"),
            _paragraph(2, "가상저자. (2020). 합성 제목. doi:10.1234/SYNTH.1"),
            _paragraph(3, "영문 참고문헌"),
            _paragraph(4, "Synthetic, Author. (2021). English title. https://example.invalid"),
        ],
    )
    manuscript = parse_manuscript(document)
    assert len(manuscript.citations) == 2
    assert [item.list_kind for item in manuscript.references] == ["korean", "english"]
    assert manuscript.references[0].doi == "10.1234/SYNTH.1"
    assert manuscript.references[1].url == "https://example.invalid"
    assert manuscript.references[1].location.id == "loc:hwpx:ref1:s0:p4"


def test_assigns_repeated_years_to_the_preceding_author() -> None:
    document = ExtractedDocument(
        format="hwpx",
        page_count=1,
        paragraphs=[
            _paragraph(0, "(Synthetic, 2021, 2020)"),
            _paragraph(1, "References"),
            _paragraph(2, "Synthetic, Author. (2020). English title."),
        ],
    )
    manuscript = parse_manuscript(document)
    assert [mention.year for mention in manuscript.citations[0].mentions] == [2021, 2020]
    assert manuscript.references[0].list_kind == "english"


@pytest.mark.parametrize(
    "heading",
    ["참 고 문 헌:", "參考文獻", "인용문헌", "\u2164. 참고문헌", "Bibliography"],
)
def test_accepts_reference_heading_variants(heading: str) -> None:
    document = ExtractedDocument(
        format="hwp",
        paragraphs=[
            _paragraph(0, "충분한 합성 본문 " * 40),
            _paragraph(1, heading),
            _paragraph(2, "Synthetic, Author. (2020). Synthetic title."),
        ],
    )

    manuscript = parse_manuscript(document)

    assert manuscript.reference_section_found is True
    assert len(manuscript.references) == 1


def test_infers_unheaded_reference_run_and_rejects_title_false_positive() -> None:
    paragraphs = [
        _paragraph(0, "References of References라는 합성 논문 제목"),
        *[_paragraph(index, "충분한 합성 본문 문장입니다. " * 8) for index in range(1, 6)],
        _paragraph(6, "Synthetic, Alpha. (2020). First synthetic title."),
        _paragraph(7, "Synthetic, Beta. (2021). Second synthetic title."),
    ]
    manuscript = parse_manuscript(
        ExtractedDocument(format="hwp", paragraphs=paragraphs)
    )

    assert manuscript.reference_section_found is True
    assert len(manuscript.references) == 2
    assert all("References of References" not in item.raw_text for item in manuscript.references)


def test_missing_reference_section_suppresses_bulk_missing_results() -> None:
    paragraphs = [
        _paragraph(index, f"충분한 합성 본문 (Synthetic, 202{index % 3}) " * 8)
        for index in range(12)
    ]
    manuscript = parse_manuscript(
        ExtractedDocument(format="hwp", paragraphs=paragraphs)
    )

    results = DeterministicRuleEngine().evaluate(manuscript)

    assert manuscript.reference_section_found is False
    assert len(results) == 1
    assert results[0].rule_id == "CR-03"
    assert "왕복 대조 생략" in results[0].memo_text


def test_tiny_extraction_returns_review_instead_of_normal() -> None:
    manuscript = parse_manuscript(
        ExtractedDocument(
            format="hwp",
            paragraphs=[_paragraph(index, "합성 양식") for index in range(6)],
        )
    )

    results = DeterministicRuleEngine().evaluate(manuscript)

    assert manuscript.body_text_sufficient is False
    assert len(results) == 1
    assert "본문을 충분히" in results[0].finding


def test_narrative_sentence_is_not_inferred_as_reference() -> None:
    paragraphs = [
        *[
            _paragraph(index, "충분한 합성 본문 문장입니다. " * 8)
            for index in range(6)
        ],
        _paragraph(6, "김영석(2018)은 합성 개념이라고 정의하였다."),
        _paragraph(7, "다른 합성 본문 문장이다."),
    ]
    manuscript = parse_manuscript(
        ExtractedDocument(format="hwp", paragraphs=paragraphs)
    )

    assert manuscript.reference_section_found is False
    assert manuscript.references == []


def test_parses_bare_year_reference_but_not_narrative_citation() -> None:
    document = ExtractedDocument(
        format="hwp",
        paragraphs=[
            _paragraph(0, "충분한 합성 본문 " * 40),
            _paragraph(1, "참고문헌"),
            _paragraph(2, "가상저자. 2020. 합성 제목. 서울: 합성출판사."),
        ],
    )
    manuscript = parse_manuscript(document)

    assert manuscript.reference_section_method == "heading"
    assert manuscript.references[0].authors[0].raw == "가상저자"
    assert manuscript.references[0].year == 2020


def test_multi_author_citation_matches_by_first_author_and_year() -> None:
    document = ExtractedDocument(
        format="hwp",
        paragraphs=[
            _paragraph(0, "충분한 합성 본문 (김이경, 안지윤, 황혜정, 김경현, 2017) " * 20),
            _paragraph(1, "참고문헌"),
            _paragraph(2, "김이경, 안지윤, 황혜정, 김경현 (2017). 합성 제목."),
        ],
    )
    manuscript = parse_manuscript(document)
    results = DeterministicRuleEngine().evaluate(manuscript)

    assert manuscript.citations[0].mentions[0].author == "김이경"
    assert all(result.rule_id not in {"CR-01", "CR-03"} for result in results)


def test_high_missing_ratio_is_one_review_result() -> None:
    citations = "; ".join(
        f"가상{chr(ord('가') + index)}, 2020" for index in range(8)
    )
    document = ExtractedDocument(
        format="hwp",
        paragraphs=[
            _paragraph(0, f"충분한 합성 본문 ({citations}) " * 10),
            _paragraph(1, "참고문헌"),
            _paragraph(2, "다른저자 (2021). 합성 제목."),
        ],
    )
    results = DeterministicRuleEngine().evaluate(parse_manuscript(document))
    missing = [result for result in results if result.rule_id in {"CR-01", "CR-03"}]

    assert len(missing) == 1
    assert missing[0].rule_id == "CR-03"
    assert "개별 누락 요청 생성 생략" in missing[0].memo_text
