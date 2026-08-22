import struct
import zlib

import pytest

from app.extraction.errors import CorruptDocumentError, SecurityLimitError
from app.extraction.hwp import (
    HWPTAG_MEMO_LIST,
    HWPTAG_PARA_TEXT,
    decode_paragraph_text,
    decode_record_paragraphs,
    decompress_body_text,
    parse_file_header_flags,
    parse_record_stream,
)


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
    payload = "가상\u0001 저자\ue000".encode("utf-16le")
    assert decode_paragraph_text(payload) == "가상 저자"


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
