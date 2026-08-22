import io
import zipfile
from typing import Optional, Tuple

import pytest
from conftest import synthetic_fixture

from app.extraction.errors import CorruptDocumentError, PageCountUnknownError
from app.extraction.hwpx import extract_hwpx


def test_extracts_safe_hwpx_in_document_order() -> None:
    document = extract_hwpx(synthetic_fixture("normal.hwpx"))
    assert document.page_count == 2
    assert [paragraph.location.paragraph_index for paragraph in document.paragraphs] == [
        0,
        1,
        2,
        3,
        4,
    ]
    assert document.paragraphs[0].location.id == "loc:hwpx:s0:p0:r0"


def test_rejects_xxe() -> None:
    with pytest.raises(CorruptDocumentError):
        extract_hwpx(synthetic_fixture("xxe.hwpx"))


def _rewrite_archive(
    data: bytes,
    *,
    remove: Optional[str] = None,
    replace: Optional[Tuple[bytes, bytes]] = None,
) -> bytes:
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as source,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for info in source.infolist():
            if info.filename == remove:
                continue
            content = source.read(info.filename)
            if replace:
                content = content.replace(*replace)
            target.writestr(info.filename, content)
    return output.getvalue()


def test_rejects_unknown_or_excessive_page_count() -> None:
    normal = synthetic_fixture("normal.hwpx")
    without_page_count = _rewrite_archive(normal, remove="Contents/content.hpf")
    with pytest.raises(PageCountUnknownError, match="페이지 수 확인 불가"):
        extract_hwpx(without_page_count)

    over_limit = _rewrite_archive(normal, replace=(b'content="2"', b'content="31"'))
    with pytest.raises(CorruptDocumentError, match="최대 30쪽"):
        extract_hwpx(over_limit)
