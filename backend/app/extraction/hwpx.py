from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Iterable
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as SafeET

from app.extraction.errors import CorruptDocumentError, PageCountUnknownError
from app.extraction.models import ExtractedDocument, Location, Paragraph
from app.security.uploads import validate_hwpx_container

SECTION_PATTERN = re.compile(r"^Contents/section(\d+)\.xml$")
PAGE_COUNT_NAMES = {"page-count", "pageCount", "page_count"}
OPF_NAMESPACE = "http://www.hancom.co.kr/hwpml/2011/opf"


def extract_hwpx(source: bytes | Path) -> ExtractedDocument:
    data = source.read_bytes() if isinstance(source, Path) else source
    validate_hwpx_container(data)

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        page_count = _read_page_count(archive)
        if page_count is None:
            raise PageCountUnknownError("페이지 수 확인 불가")
        if page_count > 30:
            raise CorruptDocumentError("원고는 최대 30쪽까지 지원합니다")

        section_entries = sorted(
            (
                (int(match.group(1)), info.filename)
                for info in archive.infolist()
                if (match := SECTION_PATTERN.fullmatch(info.filename))
            ),
            key=lambda item: item[0],
        )
        paragraphs: list[Paragraph] = []
        for section_index, entry_name in section_entries:
            root = _safe_parse(archive.read(entry_name), entry_name)
            paragraphs.extend(_extract_section(root, section_index))

    if not paragraphs:
        raise CorruptDocumentError("HWPX 본문 문단을 찾을 수 없습니다")
    return ExtractedDocument(format="hwpx", paragraphs=paragraphs, page_count=page_count)


def _read_page_count(archive: zipfile.ZipFile) -> int | None:
    entry_name = "Contents/content.hpf"
    if entry_name not in archive.namelist():
        return None
    root = _safe_parse(archive.read(entry_name), entry_name)
    for element in root.iter():
        if not element.tag.startswith(f"{{{OPF_NAMESPACE}}}"):
            continue
        local_name = _local_name(element.tag)
        attributes = {_local_name(key): value for key, value in element.attrib.items()}
        name = attributes.get("name") or attributes.get("property")
        value = attributes.get("content") or attributes.get("value")
        if local_name == "meta" and name in PAGE_COUNT_NAMES and value and value.isdigit():
            count = int(value)
            return count if count > 0 else None
        if local_name in PAGE_COUNT_NAMES and element.text:
            text = element.text.strip()
            if text.isdigit() and int(text) > 0:
                return int(text)
    return None


def _safe_parse(data: bytes, entry_name: str) -> Element:
    try:
        return SafeET.fromstring(data)
    except Exception as exc:
        raise CorruptDocumentError(f"안전하게 파싱할 수 없는 XML입니다: {entry_name}") from exc


def _extract_section(root: Element, section_index: int) -> Iterable[Paragraph]:
    paragraph_index = 0
    for element in root.iter():
        if _local_name(element.tag) != "p":
            continue
        run_texts: list[str] = []
        for child in element.iter():
            if _local_name(child.tag) == "t" and child.text:
                run_texts.append(child.text)
        text = _clean_text("".join(run_texts))
        if text:
            yield Paragraph(
                text=text,
                location=Location(
                    format="hwpx",
                    section_label=f"Section{section_index}",
                    section_index=section_index,
                    paragraph_index=paragraph_index,
                    run_index=0,
                    display_hint=text[:80],
                ),
            )
        paragraph_index += 1


def _local_name(name: str) -> str:
    return name.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _clean_text(text: str) -> str:
    return " ".join(text.split())
