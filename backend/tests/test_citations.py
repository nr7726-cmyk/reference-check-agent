from app.extraction.citations import parse_manuscript
from app.extraction.models import ExtractedDocument, Location, Paragraph


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
