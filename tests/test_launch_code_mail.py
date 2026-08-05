from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import requests

from github_launch_code_mail import (
    PollResult,
    extract_github_launch_code,
    find_github_launch_code,
    get_access_token,
    poll_github_launch_code,
)


NOW = datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc)


def _message(
    *,
    code: str = "52778203",
    subject: str = "Your GitHub launch code",
    sender_name: str = "GitHub",
    sender_address: str = "noreply@github.com",
    received: datetime = NOW,
    content: str | None = None,
) -> dict[str, object]:
    return {
        "subject": subject,
        "from": {
            "emailAddress": {"name": sender_name, "address": sender_address}
        },
        "receivedDateTime": received.isoformat().replace("+00:00", "Z"),
        "bodyPreview": "GitHub launch code",
        "body": {
            "content": content
            or f"Continue signing up for GitHub by entering the code below: {code}"
        },
    }


def test_extracts_code_from_body_and_confirmation_link() -> None:
    assert extract_github_launch_code(_message()) == "52778203"
    assert (
        extract_github_launch_code(
            _message(
                content=(
                    "https://github.com/account_verifications/confirm/"
                    "17098ca5-6467-4a4c-9dbd-109c4dcd46a3/87654321"
                )
            )
        )
        == "87654321"
    )


def test_rejects_non_eight_digit_noise() -> None:
    assert extract_github_launch_code(_message(content="numbers 1234567 and 123456789")) is None


def test_finds_newest_matching_message_and_counts_sender_matches() -> None:
    messages = [
        _message(code="11111111", received=NOW - timedelta(minutes=5)),
        _message(code="22222222", subject="Unrelated notice"),
        _message(code="33333333", sender_name="Someone", sender_address="other@example.com"),
        _message(code="52778203", received=NOW + timedelta(seconds=1)),
    ]

    result = find_github_launch_code(messages, not_before=NOW)

    assert result.code == "52778203"
    assert result.scanned == 4
    assert result.sender_matches == 3


class _Response:
    def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self) -> dict[str, object]:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class _TokenSession:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = iter(responses)
        self.posts: list[tuple[str, dict[str, str], float]] = []

    def post(self, url: str, *, data: dict[str, str], timeout: float) -> _Response:
        self.posts.append((url, data, timeout))
        return next(self.responses)


def test_token_exchange_falls_back_and_returns_rotated_refresh_token() -> None:
    session = _TokenSession(
        [
            _Response({"error": "invalid_request"}, 400),
            _Response({"access_token": "access", "refresh_token": "rotated"}),
        ]
    )

    access, refresh = get_access_token(session, "client", "original")

    assert (access, refresh) == ("access", "rotated")
    assert len(session.posts) == 2


def test_poll_waits_five_seconds_before_first_read_and_returns_code() -> None:
    sleeps: list[float] = []
    token_calls: list[tuple[str, str]] = []
    read_calls: list[str] = []

    result = poll_github_launch_code(
        client_id="client",
        refresh_token="refresh",
        not_before=NOW,
        timeout=30,
        sleep=sleeps.append,
        monotonic=iter([0.0, 0.0, 1.0]).__next__,
        token_getter=lambda _session, client, refresh: (
            token_calls.append((client, refresh)) or ("access", "rotated")
        ),
        message_reader=lambda _session, access: (
            read_calls.append(access) or [_message(received=NOW + timedelta(seconds=1))]
        ),
        session_factory=object,
    )

    assert isinstance(result, PollResult)
    assert result.code == "52778203"
    assert result.refresh_token == "rotated"
    assert sleeps == [5.0]
    assert token_calls == [("client", "refresh")]
    assert read_calls == ["access"]
    assert "rotated" not in repr(result)


def test_poll_retries_every_five_seconds_until_code_arrives() -> None:
    sleeps: list[float] = []
    reads = iter([[], [_message(received=NOW + timedelta(seconds=1))]])
    clock = iter([0.0, 0.0, 1.0, 1.0, 6.0, 6.0])

    result = poll_github_launch_code(
        client_id="client",
        refresh_token="refresh",
        not_before=NOW,
        timeout=30,
        sleep=sleeps.append,
        monotonic=clock.__next__,
        token_getter=lambda *_: ("access", "refresh"),
        message_reader=lambda *_: next(reads),
        session_factory=object,
    )

    assert result.code == "52778203"
    assert sleeps == [5.0, 5.0]


def test_poll_resends_after_exactly_three_empty_reads() -> None:
    sleeps: list[float] = []
    resends: list[str] = []
    reads = iter(
        [
            [],
            [],
            [],
            [_message(received=NOW + timedelta(seconds=1))],
        ]
    )

    result = poll_github_launch_code(
        client_id="client",
        refresh_token="refresh",
        not_before=NOW,
        timeout=60,
        reads_per_cycle=3,
        resend_callback=lambda: resends.append("重发"),
        sleep=sleeps.append,
        monotonic=lambda: 0.0,
        token_getter=lambda *_: ("access", "refresh"),
        message_reader=lambda *_: next(reads),
        session_factory=object,
    )

    assert result.code == "52778203"
    assert resends == ["重发"]
    assert sleeps == [5.0, 5.0, 5.0, 5.0]


def test_poll_times_out_with_chinese_error() -> None:
    times = iter([0.0, 0.0, 0.5, 1.0, 1.0])

    with pytest.raises(TimeoutError, match="等待 GitHub 8 位邮箱验证码超时"):
        poll_github_launch_code(
            client_id="client",
            refresh_token="refresh",
            not_before=NOW,
            timeout=1,
            sleep=lambda _seconds: None,
            monotonic=times.__next__,
            token_getter=lambda *_: ("access", "refresh"),
            message_reader=lambda *_: [],
            session_factory=object,
        )
