import io
import struct
import zlib

import pytest

from app.extraction.errors import CorruptDocumentError, SecurityLimitError
from app.extraction.hwp import (
    HWPTAG_LIST_HEADER,
    HWPTAG_MEMO_LIST,
    HWPTAG_PARA_TEXT,
    decode_paragraph_text,
    decode_record_paragraphs,
    decompress_body_text,
    extract_hwp,
    parse_file_header_flags,
    parse_record_stream,
)
from app.security.uploads import HWP_MAGIC


def _record(tag: int, payload: bytes, *, level: int = 0, extended: bool = False) -> bytes:
    size = 0xFFF if extended else len(payload)
    header = tag | (level << 10) | (size << 20)
    extension = struct.pack("<I", len(payload)) if extended else b""
    return struct.pack("<I", header) + extension + payload


def test_parses_standard_and_extended_record_sizes() -> None:
    data = _record(67, b"ab") + _record(93, b"memo", extended=True)
    records = parse_record_stream(data)
    assert [(record.tag, record.payload) for record in records] == [(67, b"ab"), (93, b"memo")]


def test_reads_file_header_security_flags() -> None:
    header = bytearray(40)
    header[:18] = b"HWP Document File\x00"
    header[32:36] = bytes((2, 0, 0, 5))
    struct.pack_into("<I", header, 36, 0b111)
    assert parse_file_header_flags(bytes(header)) == (True, True, True)


def test_decodes_text_and_removes_controls_and_private_use() -> None:
    payload = "가상\u0000 저자\ue000".encode("utf-16le")
    assert decode_paragraph_text(payload) == "가상 저자"


def test_discards_full_eight_wchar_controls_and_preserves_hanja() -> None:
    prefix = "앞".encode("utf-16le")
    inline = struct.pack("<8H", 4, 0x6D65, 0x2525, 0xFF01, 0x00FF, 1, 2, 4)
    tab = struct.pack("<8H", 9, 0x6D65, 0x2525, 0xFF01, 0x00FF, 3, 4, 9)

    decoded = decode_paragraph_text(
        prefix
        + inline
        + "圖書館".encode("utf-16le")
        + tab
        + "뒤".encode("utf-16le")
    )

    assert decoded == "앞圖書館 뒤"
    assert not {"浥", "\uff01", "ÿ"} & set(decoded)


def test_extracts_paragraph_and_reports_memo_and_unknown_tags() -> None:
    data = (
        _record(HWPTAG_PARA_TEXT, "합성 문단".encode("utf-16le"))
        + _record(HWPTAG_MEMO_LIST, b"")
        + _record(511, b"ignored")
    )
    paragraphs, warnings = decode_record_paragraphs(data, 2)
    assert paragraphs[0].location.id == "loc:hwp:s2:p0:r0"
    assert any("메모 레코드 1개" in warning for warning in warnings)
    assert any("511" in warning for warning in warnings)


@pytest.mark.parametrize("data", [b"\x01", _record(67, b"x")[:-1]])
def test_rejects_truncated_records(data: bytes) -> None:
    with pytest.raises(CorruptDocumentError):
        parse_record_stream(data)


def test_decompresses_raw_deflate_and_rejects_extreme_ratio() -> None:
    original = b"safe synthetic record stream"
    compressor = zlib.compressobj(wbits=-15)
    compressed = compressor.compress(original) + compressor.flush()
    assert decompress_body_text(compressed) == original

    compressor = zlib.compressobj(wbits=-15)
    bomb = compressor.compress(b"A" * 200_000) + compressor.flush()
    with pytest.raises(SecurityLimitError):
        decompress_body_text(bomb)

    with pytest.raises(CorruptDocumentError):
        decompress_body_text(compressed[:-1])

    assert decompress_body_text(compressed + b"12345678") == original
    with pytest.raises(CorruptDocumentError):
        decompress_body_text(compressed + b"x" * 17)


def _file_header() -> bytes:
    header = bytearray(40)
    header[:18] = b"HWP Document File\x00"
    header[32:36] = bytes((0, 0, 0, 5))
    return bytes(header)


class _SyntheticOle:
    def __init__(self, sections: dict[str, bytes]) -> None:
        self.sections = sections

    def __enter__(self) -> "_SyntheticOle":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def listdir(
        self, *, streams: bool, storages: bool
    ) -> list[list[str]]:
        assert streams is True
        assert storages is False
        return [name.split("/") for name in self.sections]

    def exists(self, name: str) -> bool:
        return name == "FileHeader" or name in self.sections

    def openstream(self, name: str) -> io.BytesIO:
        if name == "FileHeader":
            return io.BytesIO(_file_header())
        return io.BytesIO(self.sections[name])


def test_hwp_without_page_count_extracts_normally(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    body = _record(HWPTAG_PARA_TEXT, "합성 본문과 참고문헌".encode("utf-16le"))
    ole = _SyntheticOle({"BodyText/Section0": body})
    monkeypatch.setattr("app.extraction.hwp.olefile.OleFileIO", lambda _: ole)

    document = extract_hwp(HWP_MAGIC + b"synthetic")

    assert document.page_count is None
    assert [paragraph.text for paragraph in document.paragraphs] == ["합성 본문과 참고문헌"]
    assert document.paragraphs[0].location.id == "loc:hwp:s0:p0:r0"
    assert document.warnings


def test_hwp_partial_section_failure_keeps_extractable_text(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    good = _record(HWPTAG_PARA_TEXT, "추출 가능한 합성 본문".encode("utf-16le"))
    ole = _SyntheticOle(
        {
            "BodyText/Section0": b"\x01",
            "BodyText/Section1": good,
        }
    )
    monkeypatch.setattr("app.extraction.hwp.olefile.OleFileIO", lambda _: ole)

    document = extract_hwp(HWP_MAGIC + b"synthetic")

    assert [paragraph.text for paragraph in document.paragraphs] == ["추출 가능한 합성 본문"]
    assert any("Section0" in warning for warning in document.warnings)


def test_paragraph_text_inside_table_records_is_included() -> None:
    data = _record(HWPTAG_LIST_HEADER, b"") + _record(
        HWPTAG_PARA_TEXT,
        "표 셀 안의 합성 참고문헌. (2024). 합성 제목.".encode("utf-16le"),
        level=2,
    )

    paragraphs, _ = decode_record_paragraphs(data, 0)

    assert paragraphs[0].text.startswith("표 셀 안의 합성 참고문헌")
