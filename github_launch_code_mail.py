#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通过 Microsoft Graph 读取 GitHub 注册邮件中的 8 位验证码。"""

from __future__ import annotations

import html
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlsplit

import requests


LOG = logging.getLogger("github_register_v6.mail")
TOKEN_ENDPOINTS = (
    "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
    "https://login.live.com/oauth20_token.srf",
)
MESSAGES_URL = "https://graph.microsoft.com/v1.0/me/messages"
SUBJECT_MARKER = "your github launch code"
CODE_RE = re.compile(r"(?<!\d)(\d{8})(?!\d)")


class AccessTokenExpired(RuntimeError):
    """Graph access token 已失效。"""


@dataclass(frozen=True, slots=True)
class LaunchCodeResult:
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


def get_access_token(
    session: requests.Session,
    client_id: str,
    refresh_token: str,
) -> tuple[str, str]:
    errors: list[str] = []
    for endpoint in TOKEN_ENDPOINTS:
        try:
            response = session.post(
                endpoint,
                data={
                    "client_id": client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "scope": "https://graph.microsoft.com/.default",
                },
                timeout=20,
            )
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"{urlsplit(endpoint).netloc}: {type(exc).__name__}")
            continue

        access_token = str(payload.get("access_token") or "").strip()
        if access_token:
            rotated = str(payload.get("refresh_token") or refresh_token).strip()
            return access_token, rotated
        error_name = str(payload.get("error") or f"HTTP {response.status_code}")
        errors.append(f"{urlsplit(endpoint).netloc}: {error_name}")

    raise RuntimeError("获取 Microsoft Graph 访问令牌失败: " + "; ".join(errors))


def _normalized_text(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip()


def _message_text(message: Mapping[str, Any]) -> str:
    body = message.get("body")
    body_content = body.get("content") if isinstance(body, Mapping) else ""
    return _normalized_text(
        " ".join(
            (
                str(message.get("subject") or ""),
                str(message.get("bodyPreview") or ""),
                str(body_content or ""),
            )
        )
    )


def extract_github_launch_code(message: Mapping[str, Any]) -> str | None:
    match = CODE_RE.search(_message_text(message))
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


def _sender_identity(message: Mapping[str, Any]) -> tuple[str, str]:
    sender = message.get("from")
    address_data = sender.get("emailAddress") if isinstance(sender, Mapping) else None
    if not isinstance(address_data, Mapping):
        return "", ""
    name = _normalized_text(address_data.get("name")).casefold()
    address = _normalized_text(address_data.get("address")).casefold()
    return name, address


def _is_github_sender(message: Mapping[str, Any]) -> bool:
    name, address = _sender_identity(message)
    domain = address.rsplit("@", 1)[-1] if "@" in address else ""
    return "github" in name or domain == "github.com" or domain.endswith(".github.com")


def find_github_launch_code(
    messages: Iterable[Mapping[str, Any]],
    *,
    not_before: datetime,
) -> LaunchCodeResult:
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
        subject = _normalized_text(message.get("subject")).casefold()
        if SUBJECT_MARKER not in subject:
            continue
        received = _parse_graph_datetime(message.get("receivedDateTime"))
        if received is None or received < threshold:
            continue
        code = extract_github_launch_code(message)
        if code and (best_received is None or received > best_received):
            best_code = code
            best_received = received

    return LaunchCodeResult(best_code, scanned, sender_matches)


def _read_graph_messages(
    session: requests.Session,
    access_token: str,
) -> list[Mapping[str, Any]]:
    response = session.get(
        MESSAGES_URL,
        headers={"Authorization": f"Bearer {access_token}"},
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
        raise RuntimeError("Microsoft Graph 邮件响应缺少 value 列表")
    return [item for item in values if isinstance(item, Mapping)]


def poll_github_launch_code(
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
                result = find_github_launch_code(messages, not_before=not_before)
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
                LOG.warning("读取 GitHub 验证邮件失败，将继续重试: %s", type(exc).__name__)

            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            sleep(min(interval_value, remaining))
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()

    if last_error is not None:
        LOG.warning("GitHub 验证邮件轮询结束，最后错误: %s", type(last_error).__name__)
    raise TimeoutError("等待 GitHub 8 位邮箱验证码超时")


__all__ = [
    "AccessTokenExpired",
    "LaunchCodeResult",
    "PollResult",
    "extract_github_launch_code",
    "find_github_launch_code",
    "get_access_token",
    "poll_github_launch_code",
]
