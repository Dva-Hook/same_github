#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通过 Microsoft Graph 读取 GitHub 首次登录设备验证码。"""

from __future__ import annotations

import html
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping

import requests

from github_launch_code_mail import (
    AccessTokenExpired,
    MESSAGES_URL,
    get_access_token,
)


LOG = logging.getLogger("github_register_v6.device_mail")
DEVICE_SUBJECT_MARKER = "please verify your device"
DEVICE_CODE_RE = re.compile(
    r"\bverification\s+code\s*:\s*(\d{6})\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class DeviceCodeResult:
    code: str | None
    scanned: int
    sender_matches: int


@dataclass(frozen=True, slots=True, repr=False)
class PollResult:
    code: str
    refresh_token: str
    scanned: int
    sender_matches: int

    def __repr__(self) -> str:
        return (
            f"PollResult(code={self.code!r}, refresh_token=<已隐藏>, "
            f"scanned={self.scanned}, sender_matches={self.sender_matches})"
        )


def _normalized_message_text(message: Mapping[str, Any]) -> str:
    body = message.get("body")
    content = body.get("content") if isinstance(body, Mapping) else ""
    combined = " ".join(
        (
            str(message.get("subject") or ""),
            str(message.get("bodyPreview") or ""),
            str(content or ""),
        )
    )
    decoded = html.unescape(combined)
    without_tags = re.sub(r"<[^>]+>", " ", decoded)
    normalized = unicodedata.normalize("NFKC", without_tags)
    return re.sub(r"\s+", " ", normalized).strip()


def extract_device_verification_code(message: Mapping[str, Any]) -> str | None:
    match = DEVICE_CODE_RE.search(_normalized_message_text(message))
    return match.group(1) if match else None


def _parse_graph_datetime(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_github_sender(message: Mapping[str, Any]) -> bool:
    sender = message.get("from")
    address_data = sender.get("emailAddress") if isinstance(sender, Mapping) else None
    if not isinstance(address_data, Mapping):
        return False
    name = str(address_data.get("name") or "").strip().casefold()
    address = str(address_data.get("address") or "").strip().casefold()
    return name == "github" or address == "noreply@github.com"


def find_github_device_code(
    messages: Iterable[Mapping[str, Any]],
    *,
    not_before: datetime,
) -> DeviceCodeResult:
    threshold = not_before.astimezone(timezone.utc) - timedelta(seconds=10)
    scanned = 0
    sender_matches = 0
    best_code: str | None = None
    best_received: datetime | None = None

    for message in messages:
        scanned += 1
        if not _is_github_sender(message):
            continue
        sender_matches += 1
        subject = str(message.get("subject") or "").strip().casefold()
        if DEVICE_SUBJECT_MARKER not in subject:
            continue
        received = _parse_graph_datetime(message.get("receivedDateTime"))
        if received is None or received < threshold:
            continue
        code = extract_device_verification_code(message)
        if code and (best_received is None or received > best_received):
            best_code = code
            best_received = received

    return DeviceCodeResult(best_code, scanned, sender_matches)


def _read_graph_messages(
    session: requests.Session,
    access_token: str,
) -> list[Mapping[str, Any]]:
    response = session.get(
        MESSAGES_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Prefer": 'outlook.body-content-type="html"',
        },
        params={
            "$top": "50",
            "$select": "id,subject,from,receivedDateTime,bodyPreview,body",
            "$orderby": "receivedDateTime desc",
        },
        timeout=30,
    )
    if response.status_code == 401:
        raise AccessTokenExpired("Microsoft Graph 访问令牌已失效")
    response.raise_for_status()
    payload = response.json()
    values = payload.get("value")
    if not isinstance(values, list):
        raise RuntimeError("Microsoft Graph 设备验证邮件响应缺少 value 列表")
    return [item for item in values if isinstance(item, Mapping)]


def poll_github_device_code(
    *,
    client_id: str,
    refresh_token: str,
    not_before: datetime,
    timeout: float,
    interval: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    token_getter: Callable[..., tuple[str, str]] = get_access_token,
    message_reader: Callable[..., list[Mapping[str, Any]]] = _read_graph_messages,
    session_factory: Callable[[], requests.Session] = requests.Session,
) -> PollResult:
    timeout_value = max(0.1, float(timeout))
    interval_value = max(0.1, float(interval))
    deadline = monotonic() + timeout_value
    session = session_factory()
    current_refresh = refresh_token
    access_token = ""
    total_scanned = 0
    total_sender_matches = 0
    last_error: Exception | None = None

    try:
        sleep(min(5.0, timeout_value))
        access_token, current_refresh = token_getter(
            session, client_id, current_refresh
        )
        while monotonic() < deadline:
            try:
                messages = message_reader(session, access_token)
                result = find_github_device_code(messages, not_before=not_before)
                total_scanned += result.scanned
                total_sender_matches += result.sender_matches
                if result.code:
                    return PollResult(
                        code=result.code,
                        refresh_token=current_refresh,
                        scanned=total_scanned,
                        sender_matches=total_sender_matches,
                    )
                last_error = None
            except AccessTokenExpired:
                access_token, current_refresh = token_getter(
                    session, client_id, current_refresh
                )
                last_error = None
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                last_error = exc
                LOG.warning("读取 GitHub 设备验证邮件失败，将继续重试: %s", type(exc).__name__)

            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            sleep(min(interval_value, remaining))
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()

    if last_error is not None:
        LOG.warning("设备验证码邮件轮询结束，最后错误: %s", type(last_error).__name__)
    raise TimeoutError("等待 GitHub 6 位设备验证码超时")


__all__ = [
    "AccessTokenExpired",
    "DeviceCodeResult",
    "PollResult",
    "extract_device_verification_code",
    "find_github_device_code",
    "poll_github_device_code",
]
