import io
import zipfile
from pathlib import Path

import pytest
from conftest import synthetic_fixture

from app.extraction.errors import CorruptDocumentError, SecurityLimitError, UnsupportedDocumentError
from app.security.uploads import (
    HWP_MAGIC,
    new_upload_directory,
    validate_upload,
    validate_upload_count,
    validate_upload_path,
    write_uuid_temp_file,
)


def test_validates_hwpx_magic_mime_and_container() -> None:
    result = validate_upload(
        synthetic_fixture("normal.hwpx"), "manuscript.hwpx", "application/hwp+zip"
    )
    assert result.format == "hwpx"


def test_validates_hwpx_from_disk_without_loading_request_body(tmp_path: Path) -> None:
    path = tmp_path / "server-generated-id.hwpx"
    path.write_bytes(synthetic_fixture("normal.hwpx"))
    result = validate_upload_path(path, "manuscript.hwpx", "application/hwp+zip")
    assert result.format == "hwpx"
    assert result.size == path.stat().st_size


@pytest.mark.parametrize(
    ("name", "error"),
    [
        ("corrupt.hwpx", CorruptDocumentError),
        ("zip-bomb.hwpx", SecurityLimitError),
        ("path-traversal.hwpx", SecurityLimitError),
    ],
)
def test_rejects_malicious_hwpx(name: str, error: type[Exception]) -> None:
    with pytest.raises(error):
        validate_upload(synthetic_fixture(name), name, "application/hwp+zip")


def test_rejects_extension_magic_and_count_mismatches() -> None:
    with pytest.raises(UnsupportedDocumentError):
        validate_upload_count(2)
    with pytest.raises(CorruptDocumentError):
        validate_upload(b"not-ole", "document.hwp", "application/x-hwp")
    with pytest.raises(CorruptDocumentError):
        validate_upload(HWP_MAGIC + b"payload", "document.hwpx", "application/hwp+zip")


def test_uses_server_generated_uuid_paths(tmp_path: Path) -> None:
    directory = new_upload_directory(tmp_path)
    path = write_uuid_temp_file(b"data", directory, ".hwp")
    assert path.parent == directory
    assert path.name != "author-file.hwp"
    assert len(path.stem) == 32


def test_rejects_duplicate_normalized_zip_entries() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr("Contents/content.hpf", "<package/>")
        archive.writestr("Contents/section0.xml", "<sec/>")
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("Contents\\section0.xml", "<sec/>")
    with pytest.raises(SecurityLimitError, match="중복"):
        validate_upload(buffer.getvalue(), "duplicate.hwpx", "application/hwp+zip")
