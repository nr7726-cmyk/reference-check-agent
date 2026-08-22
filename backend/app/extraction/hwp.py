from __future__ import annotations

import io
import re
import struct
import unicodedata
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import olefile  # type: ignore[import-untyped]

from app.extraction.errors import (
    CorruptDocumentError,
    PageCountUnknownError,
    SecurityLimitError,
    UnsupportedDocumentError,
)
from app.extraction.models import ExtractedDocument, Location, Paragraph
from app.security.uploads import HWP_MAGIC

HWPTAG_PARA_TEXT = 67
HWPTAG_MEMO_LIST = 93
MAX_DECOMPRESSED_SECTION = 32 * 1024 * 1024
MAX_DECOMPRESSION_RATIO = 100.0
SECTION_NAME = re.compile(r"^BodyText/Section(\d+)$")


@dataclass(frozen=True)
class HwpRecord:
    tag: int
    level: int
    payload: bytes
    offset: int


def parse_record_stream(data: bytes) -> list[HwpRecord]:
    records: list[HwpRecord] = []
    offset = 0
    while offset < len(data):
        header_offset = offset
        if len(data) - offset < 4:
            raise CorruptDocumentError("잘린 HWP 레코드 헤더입니다")
        header = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        tag = header & 0x3FF
        level = (header >> 10) & 0x3FF
        size = (header >> 20) & 0xFFF
        if size == 0xFFF:
            if len(data) - offset < 4:
                raise CorruptDocumentError("잘린 HWP 확장 크기입니다")
            size = struct.unpack_from("<I", data, offset)[0]
            offset += 4
        end = offset + size
        if end < offset or end > len(data):
            raise CorruptDocumentError("HWP 레코드 크기가 스트림 범위를 벗어났습니다")
        records.append(
            HwpRecord(tag=tag, level=level, payload=data[offset:end], offset=header_offset)
        )
        offset = end
    return records


def decode_paragraph_text(payload: bytes) -> str:
    if len(payload) % 2:
        raise CorruptDocumentError("HWP 문단 텍스트의 UTF-16LE 길이가 올바르지 않습니다")
    try:
        text = payload.decode("utf-16le")
    except UnicodeDecodeError as exc:
        raise CorruptDocumentError("HWP 문단 텍스트를 디코딩할 수 없습니다") from exc
    cleaned = "".join(
        character
        for character in text
        if not (
            unicodedata.category(character).startswith("C") or "\ue000" <= character <= "\uf8ff"
        )
    )
    return " ".join(cleaned.split())


def decode_record_paragraphs(data: bytes, section_index: int) -> tuple[list[Paragraph], list[str]]:
    paragraphs: list[Paragraph] = []
    warnings: list[str] = []
    unknown_tags: set[int] = set()
    memo_count = 0
    for record in parse_record_stream(data):
        if record.tag == HWPTAG_PARA_TEXT:
            text = decode_paragraph_text(record.payload)
            if text:
                paragraph_index = len(paragraphs)
                paragraphs.append(
                    Paragraph(
                        text=text,
                        location=Location(
                            format="hwp",
                            section_label=f"Section{section_index}",
                            section_index=section_index,
                            paragraph_index=paragraph_index,
                            run_index=0,
                            display_hint=text[:80],
                        ),
                    )
                )
        elif record.tag == HWPTAG_MEMO_LIST:
            memo_count += 1
        else:
            unknown_tags.add(record.tag)
    if memo_count:
        warnings.append(f"Section{section_index}: 메모 레코드 {memo_count}개를 확인했습니다")
    if unknown_tags:
        tags = ", ".join(str(tag) for tag in sorted(unknown_tags))
        warnings.append(f"Section{section_index}: 알 수 없는 태그를 건너뛰었습니다: {tags}")
    return paragraphs, warnings


def decompress_body_text(data: bytes) -> bytes:
    try:
        decompressor = zlib.decompressobj(wbits=-15)
        output = decompressor.decompress(data, MAX_DECOMPRESSED_SECTION + 1)
        if len(output) > MAX_DECOMPRESSED_SECTION or decompressor.unconsumed_tail:
            raise SecurityLimitError("HWP BodyText 압축 해제 크기 제한을 초과했습니다")
        remaining = MAX_DECOMPRESSED_SECTION + 1 - len(output)
        output += decompressor.flush(remaining)
    except zlib.error as exc:
        raise CorruptDocumentError("HWP BodyText raw deflate 압축이 손상되었습니다") from exc
    if len(output) > MAX_DECOMPRESSED_SECTION or decompressor.unconsumed_tail:
        raise SecurityLimitError("HWP BodyText 압축 해제 크기 제한을 초과했습니다")
    if not decompressor.eof or decompressor.unused_data:
        raise CorruptDocumentError("HWP BodyText 압축 스트림이 완전하지 않습니다")
    if data and len(output) / len(data) > MAX_DECOMPRESSION_RATIO:
        raise SecurityLimitError("비정상 HWP BodyText 압축률이 감지되었습니다")
    return output


def extract_hwp(source: bytes | Path, *, page_count: int | None = None) -> ExtractedDocument:
    data = source.read_bytes() if isinstance(source, Path) else source
    if not data.startswith(HWP_MAGIC):
        raise CorruptDocumentError("HWP OLE 서명이 올바르지 않습니다")

    stream: BinaryIO = io.BytesIO(data)
    try:
        with olefile.OleFileIO(stream) as ole:
            header = _read_required_stream(ole, "FileHeader")
            compressed, encrypted, distributable = parse_file_header_flags(header)
            if encrypted:
                raise UnsupportedDocumentError("암호화된 HWP는 지원하지 않습니다")
            if distributable:
                raise UnsupportedDocumentError("배포용 HWP는 지원하지 않습니다")
            if page_count is None:
                raise PageCountUnknownError("페이지 수 확인 불가")
            if page_count > 30:
                raise UnsupportedDocumentError("원고는 최대 30쪽까지 지원합니다")

            sections: list[tuple[int, str]] = []
            for parts in ole.listdir(streams=True, storages=False):
                name = "/".join(parts)
                match = SECTION_NAME.fullmatch(name)
                if match:
                    sections.append((int(match.group(1)), name))
            if not sections:
                raise CorruptDocumentError("HWP BodyText 섹션이 없습니다")

            paragraphs: list[Paragraph] = []
            warnings: list[str] = []
            for section_index, name in sorted(sections):
                section_data = _read_required_stream(ole, name)
                if compressed:
                    section_data = decompress_body_text(section_data)
                decoded, section_warnings = decode_record_paragraphs(section_data, section_index)
                paragraphs.extend(decoded)
                warnings.extend(section_warnings)
    except OSError as exc:
        raise CorruptDocumentError("손상된 HWP OLE 컨테이너입니다") from exc

    return ExtractedDocument(
        format="hwp", paragraphs=paragraphs, page_count=page_count, warnings=warnings
    )


def _read_required_stream(ole: olefile.OleFileIO, name: str) -> bytes:
    if not ole.exists(name):
        raise CorruptDocumentError(f"HWP 필수 스트림이 없습니다: {name}")
    return bytes(ole.openstream(name).read())


def parse_file_header_flags(header: bytes) -> tuple[bool, bool, bool]:
    signature = b"HWP Document File"
    if len(header) < 40 or not header.startswith(signature) or header[len(signature)] != 0:
        raise CorruptDocumentError("HWP FileHeader가 올바르지 않습니다")
    version = tuple(reversed(header[32:36]))
    if not version or version[0] != 5:
        raise UnsupportedDocumentError("지원하지 않는 HWP 버전입니다")
    flags = struct.unpack_from("<I", header, 36)[0]
    return bool(flags & 0x01), bool(flags & 0x02), bool(flags & 0x04)
