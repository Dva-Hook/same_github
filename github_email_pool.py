#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitHub V6 邮箱池解析、矩阵分配和原子回写。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DELIMITER = "----"
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
MAX_TASKS = 256
MAX_PARALLEL = 20


@dataclass(frozen=True, slots=True, repr=False)
class EmailCredential:
    email: str
    mailbox_password: str
    client_id: str
    refresh_token: str
    raw_line: str
    source_index: int

    def __repr__(self) -> str:
        return f"EmailCredential(email={self.email!r}, source_index={self.source_index})"


@dataclass(frozen=True, slots=True)
class RemovalResult:
    requested: int
    removed: int
    removed_emails: tuple[str, ...]
    remaining: int


def parse_credential_line(raw: str, *, source_index: int) -> EmailCredential:
    line = str(raw or "").strip()
    parts = [part.strip() for part in line.split(DELIMITER, 3)]
    if len(parts) != 4 or any(not part for part in parts):
        raise ValueError(
            f"邮箱文件第 {source_index} 行格式错误，应为 "
            "邮箱----密码----client_id----refresh_token"
        )
    email, password, client_id, refresh_token = parts
    if not EMAIL_RE.fullmatch(email):
        raise ValueError(f"邮箱文件第 {source_index} 行邮箱格式错误")
    return EmailCredential(
        email=email,
        mailbox_password=password,
        client_id=client_id,
        refresh_token=refresh_token,
        raw_line=DELIMITER.join(parts),
        source_index=int(source_index),
    )


def load_email_pool(path: Path | str) -> list[EmailCredential]:
    pool_path = Path(path).expanduser().resolve()
    if not pool_path.is_file():
        raise FileNotFoundError(f"邮箱文件不存在: {pool_path}")

    credentials: list[EmailCredential] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(
        pool_path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        if not raw_line.strip():
            continue
        credential = parse_credential_line(raw_line, source_index=line_number)
        normalized = credential.email.casefold()
        if normalized in seen:
            raise ValueError(f"邮箱文件存在重复邮箱: {credential.email}")
        seen.add(normalized)
        credentials.append(credential)

    if not credentials:
        raise ValueError(f"邮箱文件没有可用记录: {pool_path}")
    return credentials


def select_email_credential(path: Path | str, job_index: int) -> EmailCredential:
    index = int(job_index)
    if index < 1:
        raise ValueError(f"任务索引必须从 1 开始: {index}")
    credentials = load_email_pool(path)
    if index > len(credentials):
        raise IndexError(
            f"邮箱文件只有 {len(credentials)} 个账号，无法分配第 {index} 个任务"
        )
    return credentials[index - 1]


def _bounded_integer(name: str, raw: str | int, upper: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}必须是整数") from exc
    if not 1 <= value <= upper:
        raise ValueError(f"{name}必须在 1 到 {upper} 之间")
    return value


def resolve_task_count(path: Path | str, requested: str | int | None) -> int:
    available = len(load_email_pool(path))
    normalized = "" if requested is None else str(requested).strip()
    if not normalized:
        return min(available, MAX_TASKS)
    count = _bounded_integer("任务数量", normalized, MAX_TASKS)
    if count > available:
        raise ValueError(f"邮箱文件只有 {available} 个账号，无法创建 {count} 个任务")
    return count


def resolve_max_parallel(requested: str | int, task_count: int) -> int:
    parallel = _bounded_integer("最大并发数量", requested, MAX_PARALLEL)
    count = _bounded_integer("任务数量", task_count, MAX_TASKS)
    return min(parallel, count, MAX_PARALLEL)


def matrix_indices(task_count: int) -> list[int]:
    count = _bounded_integer("任务数量", task_count, MAX_TASKS)
    return list(range(1, count + 1))


def remove_consumed_emails(
    path: Path | str, emails: Iterable[str]
) -> RemovalResult:
    pool_path = Path(path).expanduser().resolve()
    if not pool_path.is_file():
        raise FileNotFoundError(f"邮箱文件不存在: {pool_path}")

    consumed = {
        str(email or "").strip().casefold()
        for email in emails
        if str(email or "").strip()
    }
    original_lines = pool_path.read_text(encoding="utf-8-sig").splitlines()
    original_count = sum(1 for line in original_lines if line.strip())
    if not consumed:
        return RemovalResult(0, 0, (), original_count)

    kept: list[str] = []
    removed: list[str] = []
    for line_number, raw_line in enumerate(original_lines, start=1):
        if not raw_line.strip():
            kept.append(raw_line)
            continue
        credential = parse_credential_line(raw_line, source_index=line_number)
        if credential.email.casefold() in consumed:
            removed.append(credential.email)
        else:
            kept.append(raw_line)

    if not removed:
        return RemovalResult(len(consumed), 0, (), original_count)

    rendered = "\n".join(kept)
    if kept:
        rendered += "\n"
    temporary = pool_path.with_name(pool_path.name + ".tmp")
    temporary.write_text(rendered, encoding="utf-8", newline="\n")
    os.replace(temporary, pool_path)
    return RemovalResult(
        requested=len(consumed),
        removed=len(removed),
        removed_emails=tuple(removed),
        remaining=sum(1 for line in kept if line.strip()),
    )


__all__ = [
    "DELIMITER",
    "EMAIL_RE",
    "MAX_PARALLEL",
    "MAX_TASKS",
    "EmailCredential",
    "RemovalResult",
    "load_email_pool",
    "matrix_indices",
    "parse_credential_line",
    "remove_consumed_emails",
    "resolve_max_parallel",
    "resolve_task_count",
    "select_email_credential",
]

