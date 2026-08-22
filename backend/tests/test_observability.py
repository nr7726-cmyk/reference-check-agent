import logging

from app.observability.logging import log_event


def test_structured_log_drops_manuscript_and_memo(caplog) -> None:  # type: ignore[no-untyped-def]
    logger = logging.getLogger("redaction-test")
    with caplog.at_level(logging.INFO, logger="redaction-test"):
        log_event(
            logger,
            "pipeline",
            correlation_id="cid",
            stage="checking",
            result_count=2,
            manuscript_text="PRIVATE_SYNTHETIC_MANUSCRIPT",
            memo_text="PRIVATE_SYNTHETIC_MEMO",
            author="PRIVATE_SYNTHETIC_AUTHOR",
        )
    output = caplog.text
    assert '"correlation_id": "cid"' in output
    assert "PRIVATE_SYNTHETIC" not in output
