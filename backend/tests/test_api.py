from __future__ import annotations

import io
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

from conftest import synthetic_fixture
from fastapi.testclient import TestClient
from httpx import Response

from app.config import Settings
from app.extraction.models import ExtractedDocument
from app.main import create_app
from app.security.uploads import MAX_FILE_SIZE

OPF = "http://www.hancom.co.kr/hwpml/2011/opf"


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "upload_root": tmp_path,
        "sweep_interval_seconds": 0.01,
        "heartbeat_seconds": 0.01,
        "allowed_origins": ("https://editor.example",),
    }
    values.update(overrides)
    return Settings(**values)


def _auth(created: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {created['access_token']}"}


def _upload(client: TestClient, data: bytes, name: str = "synthetic.hwpx") -> Response:
    return client.post(
        "/api/v1/checks",
        files=[("files", (name, data, "application/hwp+zip"))],
    )


def _wait_terminal(
    client: TestClient, created: dict[str, Any], timeout: float = 3
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/checks/{created['id']}", headers=_auth(created))
        if response.status_code == 410:
            return {"status": "expired"}
        response.raise_for_status()
        payload = response.json()
        if payload["status"] in {"completed", "failed", "cancelled"}:
            return cast(dict[str, Any], payload)
        time.sleep(0.01)
    raise AssertionError("pipeline did not reach a terminal state")


def _sized_hwpx(target_size: int, page_count: int = 30) -> bytes:
    def build(first: int, second: int) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("mimetype", "application/hwp+zip")
            archive.writestr(
                "Contents/content.hpf",
                (
                    f'<opf:package xmlns:opf="{OPF}">'
                    f'<opf:meta name="page-count" content="{page_count}"/>'
                    "</opf:package>"
                ),
            )
            archive.writestr(
                "Contents/section0.xml",
                '<hp:sec xmlns:hp="urn:synthetic:hwp"><hp:p><hp:run>'
                "<hp:t>참고문헌</hp:t></hp:run></hp:p></hp:sec>",
            )
            archive.writestr("BinData/filler-a.bin", b"\0" * first)
            archive.writestr("BinData/filler-b.bin", b"\0" * second)
        return output.getvalue()

    overhead = len(build(0, 0))
    payload_size = target_size - overhead
    first = payload_size // 2
    result = build(first, payload_size - first)
    assert len(result) == target_size
    return result


def test_full_api_auth_sse_reconnect_edit_export_and_cleanup(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        created_response = _upload(client, synthetic_fixture("normal.hwpx"))
        assert created_response.status_code == 202
        created = created_response.json()
        assert "access_token" in created
        assert created["access_token"] not in str(app.state.session_store._states)

        assert client.get(f"/api/v1/checks/{created['id']}").status_code == 401
        assert (
            client.get(
                f"/api/v1/checks/{created['id']}",
                headers={"Authorization": "Bearer wrong"},
            ).status_code
            == 401
        )

        summary = _wait_terminal(client, created)
        assert summary["status"] == "completed"
        state = next(iter(app.state.session_store._states.values()))
        assert state.temp_path is None
        assert not any(tmp_path.rglob("*.hwpx"))

        event_response = client.get(
            created["events_url"],
            headers={**_auth(created), "Last-Event-ID": "1"},
        )
        assert event_response.status_code == 200
        ids = [
            int(line.removeprefix("id: "))
            for line in event_response.text.splitlines()
            if line.startswith("id: ")
        ]
        assert ids and all(event_id > 1 for event_id in ids)
        assert len(ids) == len(set(ids))
        assert event_response.headers["cache-control"] == "no-store"
        assert event_response.headers["x-accel-buffering"] == "no"

        results = client.get(
            f"/api/v1/checks/{created['id']}/results", headers=_auth(created)
        ).json()
        assert results
        result_id = results[0]["id"]
        edited = client.patch(
            f"/api/v1/checks/{created['id']}/results/{result_id}",
            headers=_auth(created),
            json={"memo_text": "합성 수정 요청", "decision": "approved"},
        )
        assert edited.status_code == 200
        export = client.get(f"/api/v1/checks/{created['id']}/export", headers=_auth(created))
        assert export.status_code == 200
        assert "합성 수정 요청" in export.text
        assert "PRIVATE" not in export.text


def test_upload_count_size_page_boundaries_and_rate_limit(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, ip_hourly_limit=2))
    with TestClient(app) as client:
        too_many = client.post(
            "/api/v1/checks",
            files=[
                ("files", ("one.hwpx", synthetic_fixture("normal.hwpx"), "application/hwp+zip")),
                ("files", ("two.hwpx", synthetic_fixture("normal.hwpx"), "application/hwp+zip")),
            ],
        )
        assert too_many.status_code == 422

        oversized = _upload(client, b"x" * (MAX_FILE_SIZE + 1))
        assert oversized.status_code == 413

    boundary_app = create_app(_settings(tmp_path / "boundary", ip_hourly_limit=1))
    with TestClient(boundary_app) as client:
        exact = _upload(client, _sized_hwpx(MAX_FILE_SIZE, page_count=30))
        assert exact.status_code == 202
        assert _wait_terminal(client, exact.json())["status"] == "completed"
        limited = _upload(client, synthetic_fixture("normal.hwpx"))
        assert limited.status_code == 429


def test_rejects_large_request_before_multipart_parsing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/checks",
            content=b"not-a-multipart-body",
            headers={
                "Content-Type": "multipart/form-data; boundary=synthetic",
                "Content-Length": str(MAX_FILE_SIZE + settings.multipart_overhead_bytes + 1),
            },
        )
        assert response.status_code == 413
        assert response.headers["x-content-type-options"] == "nosniff"
        assert not any(tmp_path.iterdir())


def test_page_limit_failure_cancel_and_expiry(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    clock = MutableClock()
    app = create_app(_settings(tmp_path), clock)
    with TestClient(app) as client:
        over_page = _upload(client, _sized_hwpx(2_000_000, page_count=31))
        assert over_page.status_code == 202
        failed = _wait_terminal(client, over_page.json())
        assert failed["status"] == "failed"
        assert failed["error"]["code"] == "CorruptDocumentError"

        import app.workflows.pipeline as pipeline_module

        original = pipeline_module.extract_hwpx  # type: ignore[attr-defined]

        def slow_extract(path: Path) -> ExtractedDocument:
            time.sleep(0.2)
            return original(path)

        monkeypatch.setattr(pipeline_module, "extract_hwpx", slow_extract)
        created = _upload(client, synthetic_fixture("normal.hwpx")).json()
        cancelled = client.post(f"/api/v1/checks/{created['id']}/cancel", headers=_auth(created))
        assert cancelled.json()["status"] == "cancelled"
        assert _wait_terminal(client, created)["status"] == "cancelled"

        expiring = _upload(client, synthetic_fixture("normal.hwpx")).json()
        clock.advance(7_201)
        expired = client.get(f"/api/v1/checks/{expiring['id']}", headers=_auth(expiring))
        assert expired.status_code == 410


def test_security_headers_cors_and_health(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get("/health/ready", headers={"Origin": "https://editor.example"})
        assert response.status_code == 200
        assert response.json() == {"status": "ready", "rules": 12}
        assert response.headers["content-security-policy"].startswith("default-src")
        assert response.headers["strict-transport-security"].startswith("max-age=")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["access-control-allow-origin"] == "https://editor.example"

        denied = client.options(
            "/api/v1/checks",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" not in denied.headers
        assert client.get("/health/live").json() == {"status": "ok"}
