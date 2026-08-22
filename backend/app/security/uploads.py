from __future__ import annotations

import io
import mimetypes
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, cast

import olefile  # type: ignore[import-untyped]

from app.extraction.errors import CorruptDocumentError, SecurityLimitError, UnsupportedDocumentError

HWP_MAGIC = bytes.fromhex("D0 CF 11 E0 A1 B1 1A E1")
ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
HWPX_MIMETYPE = b"application/hwp+zip"
MAX_FILE_SIZE = 30 * 1024 * 1024
MAX_ZIP_ENTRIES = 256
MAX_ZIP_ENTRY_SIZE = 16 * 1024 * 1024
MAX_ZIP_TOTAL_SIZE = 64 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100.0
ALLOWED_MIME_TYPES = {
    "hwp": {"application/x-hwp", "application/haansofthwp", "application/octet-stream"},
    "hwpx": {"application/hwp+zip", "application/zip", "application/octet-stream"},
}


@dataclass(frozen=True)
class ValidatedUpload:
    format: Literal["hwp", "hwpx"]
    size: int
    content_type: str


def validate_upload_count(file_count: int) -> None:
    if file_count != 1:
        raise UnsupportedDocumentError("HWP 또는 HWPX 파일 1개만 업로드할 수 있습니다")


def validate_upload(data: bytes, filename: str, content_type: str | None) -> ValidatedUpload:
    if not data:
        raise CorruptDocumentError("빈 파일은 업로드할 수 없습니다")
    if len(data) > MAX_FILE_SIZE:
        raise SecurityLimitError("파일 크기는 30MB 이하여야 합니다")

    extension = Path(filename).suffix.lower().lstrip(".")
    if extension not in {"hwp", "hwpx"}:
        raise UnsupportedDocumentError("지원 형식은 HWP와 HWPX입니다")

    normalized_type = (content_type or mimetypes.guess_type(filename)[0] or "").lower()
    if normalized_type not in ALLOWED_MIME_TYPES[extension]:
        raise UnsupportedDocumentError("파일 MIME 형식이 확장자와 일치하지 않습니다")

    if extension == "hwp":
        if not data.startswith(HWP_MAGIC):
            raise CorruptDocumentError("HWP OLE 서명이 올바르지 않습니다")
        _validate_hwp_container(data)
    else:
        if not data.startswith(ZIP_MAGICS):
            raise CorruptDocumentError("HWPX ZIP 서명이 올바르지 않습니다")
        validate_hwpx_container(data)

    return ValidatedUpload(
        format=cast(Literal["hwp", "hwpx"], extension),
        size=len(data),
        content_type=normalized_type,
    )


def validate_upload_path(path: Path, filename: str, content_type: str | None) -> ValidatedUpload:
    size = path.stat().st_size
    if size == 0:
        raise CorruptDocumentError("빈 파일은 업로드할 수 없습니다")
    if size > MAX_FILE_SIZE:
        raise SecurityLimitError("파일 크기는 30MB 이하여야 합니다")
    extension = Path(filename).suffix.lower().lstrip(".")
    if extension not in {"hwp", "hwpx"}:
        raise UnsupportedDocumentError("지원 형식은 HWP와 HWPX입니다")
    normalized_type = (content_type or mimetypes.guess_type(filename)[0] or "").lower()
    if normalized_type not in ALLOWED_MIME_TYPES[extension]:
        raise UnsupportedDocumentError("파일 MIME 형식이 확장자와 일치하지 않습니다")
    with path.open("rb") as stream:
        magic = stream.read(8)
    if extension == "hwp":
        if magic != HWP_MAGIC:
            raise CorruptDocumentError("HWP OLE 서명이 올바르지 않습니다")
        _validate_hwp_container(path)
    else:
        if not magic.startswith(ZIP_MAGICS):
            raise CorruptDocumentError("HWPX ZIP 서명이 올바르지 않습니다")
        validate_hwpx_container(path)
    return ValidatedUpload(
        format=cast(Literal["hwp", "hwpx"], extension),
        size=size,
        content_type=normalized_type,
    )


def validate_hwpx_container(data: bytes | Path) -> None:
    source = io.BytesIO(data) if isinstance(data, bytes) else data
    try:
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                raise SecurityLimitError("HWPX ZIP 항목 수 제한을 초과했습니다")

            total_size = 0
            normalized_names = [info.filename.replace("\\", "/") for info in infos]
            if len(normalized_names) != len(set(normalized_names)):
                raise SecurityLimitError("HWPX ZIP에 중복 항목이 있습니다")
            names = set(normalized_names)
            if "mimetype" not in names or not any(
                name.startswith("Contents/section") and name.endswith(".xml") for name in names
            ):
                raise CorruptDocumentError("HWPX 필수 항목이 없습니다")

            for info in infos:
                _validate_zip_entry(info)
                total_size += info.file_size
                if total_size > MAX_ZIP_TOTAL_SIZE:
                    raise SecurityLimitError("HWPX 압축 해제 크기 제한을 초과했습니다")

            if archive.read("mimetype").strip() != HWPX_MIMETYPE:
                raise CorruptDocumentError("HWPX mimetype 항목이 올바르지 않습니다")
    except zipfile.BadZipFile as exc:
        raise CorruptDocumentError("손상된 HWPX ZIP 컨테이너입니다") from exc


def _validate_zip_entry(info: zipfile.ZipInfo) -> None:
    path = PurePosixPath(info.filename.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise SecurityLimitError("HWPX ZIP 경로 traversal이 감지되었습니다")
    if info.file_size > MAX_ZIP_ENTRY_SIZE:
        raise SecurityLimitError("HWPX 단일 항목 크기 제한을 초과했습니다")
    if info.file_size and info.compress_size == 0:
        raise SecurityLimitError("비정상 HWPX 압축 정보입니다")
    if info.compress_size:
        ratio = info.file_size / info.compress_size
        if ratio > MAX_COMPRESSION_RATIO:
            raise SecurityLimitError("비정상 HWPX 압축률이 감지되었습니다")


def _validate_hwp_container(data: bytes | Path) -> None:
    source = io.BytesIO(data) if isinstance(data, bytes) else data
    try:
        with olefile.OleFileIO(source) as ole:
            if not ole.exists("FileHeader") or not any(
                parts[:1] == ["BodyText"] for parts in ole.listdir(streams=True, storages=False)
            ):
                raise CorruptDocumentError("HWP 필수 OLE 스트림이 없습니다")
    except (OSError, ValueError) as exc:
        raise CorruptDocumentError("손상된 HWP OLE 컨테이너입니다") from exc


def write_uuid_temp_file(data: bytes, directory: Path, suffix: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{uuid.uuid4().hex}.{suffix.lstrip('.')}"
    with path.open("xb") as stream:
        stream.write(data)
    return path


def new_upload_directory(root: Path | None = None) -> Path:
    base = root or Path(tempfile.gettempdir()) / "reference-check-agent"
    directory = base / uuid.uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    return directory
