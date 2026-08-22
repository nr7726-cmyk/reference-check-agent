"""Generate public, fully synthetic HWPX security fixtures."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MIMETYPE = "application/hwp+zip"
CONTENT = """<?xml version="1.0" encoding="UTF-8"?>
<opf:package xmlns:opf="http://www.hancom.co.kr/hwpml/2011/opf">
  <opf:meta name="page-count" content="2"/>
</opf:package>
"""
SECTION = """<?xml version="1.0" encoding="UTF-8"?>
<hp:sec xmlns:hp="urn:synthetic:hwp">
  <hp:p><hp:run><hp:t>가상저자(2020)는 합성 연구를 설명했다.</hp:t></hp:run></hp:p>
  <hp:p><hp:run><hp:t>참고문헌</hp:t></hp:run></hp:p>
  <hp:p><hp:run><hp:t>가상저자. (2020). 합성 연구 제목. https://example.invalid/item</hp:t></hp:run></hp:p>
  <hp:p><hp:run><hp:t>영문 참고문헌</hp:t></hp:run></hp:p>
  <hp:p><hp:run><hp:t>Author, Synthetic. (2020). Synthetic title. https://example.invalid/item</hp:t></hp:run></hp:p>
</hp:sec>
"""
XXE_SECTION = """<?xml version="1.0"?>
<!DOCTYPE sec [<!ENTITY secret SYSTEM "file:///not-readable">]>
<sec><p><run><t>&secret;</t></run></p></sec>
"""


def _archive(section: str = SECTION, *, extra: tuple[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", MIMETYPE, compress_type=zipfile.ZIP_STORED)
        archive.writestr("Contents/content.hpf", CONTENT)
        archive.writestr("Contents/section0.xml", section)
        if extra:
            archive.writestr(extra[0], extra[1])
    return buffer.getvalue()


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "normal.hwpx").write_bytes(_archive())
    (ROOT / "corrupt.hwpx").write_bytes(b"PK\x03\x04synthetic-corrupt")
    (ROOT / "xxe.hwpx").write_bytes(_archive(XXE_SECTION))
    (ROOT / "path-traversal.hwpx").write_bytes(_archive(extra=("../escape.xml", b"<x/>")))
    bomb_text = "<sec><p><run><t>" + ("A" * 2_000_000) + "</t></run></p></sec>"
    (ROOT / "zip-bomb.hwpx").write_bytes(_archive(bomb_text))


if __name__ == "__main__":
    main()
