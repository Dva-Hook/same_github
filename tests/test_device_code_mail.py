from __future__ import annotations

from datetime import datetime, timedelta, timezone

from github_device_code_mail import (
    PollResult,
    extract_device_verification_code,
    find_github_device_code,
    poll_github_device_code,
)


NOW = datetime(2026, 8, 6, 10, 0, tzinfo=timezone.utc)


def _message(
    *,
    code: str = "123456",
    subject: str = "Please verify your device",
    sender_name: str = "GitHub",
    sender_address: str = "noreply@github.com",
    received: datetime = NOW,
) -> dict[str, object]:
    return {
        "subject": subject,
        "from": {
            "emailAddress": {
                "name": sender_name,
                "address": sender_address,
            }
        },
        "receivedDateTime": received.isoformat().replace("+00:00", "Z"),
        "bodyPreview": f"Verification code: {code}",
        "body": {"content": f"Your verification code: <b>{code}</b>"},
    }


def test_extracts_six_digit_device_code() -> None:
    assert extract_device_verification_code(_message()) == "123456"
    assert extract_device_verification_code(_message(code="12345")) is None


def test_finds_only_new_matching_github_device_mail() -> None:
    result = find_github_device_code(
        [
            _message(code="111111", received=NOW - timedelta(minutes=5)),
            _message(code="222222", subject="Your GitHub launch code"),
            _message(
                code="333333",
                sender_name="Other",
                sender_address="other@example.com",
            ),
            _message(code="654321", received=NOW + timedelta(seconds=1)),
        ],
        not_before=NOW,
    )

    assert result.code == "654321"
    assert result.scanned == 4
    assert result.sender_matches == 3


def test_device_mail_poll_waits_then_retries() -> None:
    sleeps: list[float] = []
    reads = iter([[], [_message(received=NOW + timedelta(seconds=1))]])
    clock = iter([0.0, 0.0, 1.0, 1.0, 6.0, 6.0])

    result = poll_github_device_code(
        client_id="client",
        refresh_token="refresh",
        not_before=NOW,
        timeout=30,
        sleep=sleeps.append,
        monotonic=clock.__next__,
        token_getter=lambda *_: ("access", "rotated"),
        message_reader=lambda *_: next(reads),
        session_factory=object,
    )

    assert isinstance(result, PollResult)
    assert result.code == "123456"
    assert result.refresh_token == "rotated"
    assert sleeps == [5.0, 5.0]
    assert "rotated" not in repr(result)
