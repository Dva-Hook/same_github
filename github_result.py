#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验矩阵 Artifact，汇总成功账号并安全回写邮箱池。"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from github_cli import ChineseArgumentParser
from github_email_pool import EMAIL_RE, parse_credential_line, remove_consumed_emails


USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")


@dataclass(frozen=True, slots=True, repr=False)
class AcceptedAccount:
    email: str
    password: str
    username: str
    api_line: str

    def __repr__(self) -> str:
        return f"AcceptedAccount(email={self.email!r}, username={self.username!r})"


@dataclass(frozen=True, slots=True)
class CollectionSummary:
    accepted: tuple[AcceptedAccount, ...]
    total_artifacts: int
    failed: int
    ignored: int
    missing: int


@dataclass(frozen=True, slots=True)
class ApplySummary:
    total_tasks: int
    successful: int
    failed: int
    ignored: int
    missing: int
    appended: int
    removed: int
    remaining: int


def _as_nonempty_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _accepted_account(payload: Mapping[str, Any]) -> AcceptedAccount | None:
    assigned_email = _as_nonempty_string(payload.get("assigned_email"))
    if assigned_email is None or not EMAIL_RE.fullmatch(assigned_email):
        return None
    if payload.get("success") is not True or payload.get("schema_version") != 1:
        return None

    verification = payload.get("verification")
    if not isinstance(verification, Mapping):
        return None
    if verification.get("code_submitted") is not True:
        return None
    if verification.get("success_banner") is not True:
        return None
    if verification.get("login_confirmed") is not True:
        return None

    account = payload.get("account")
    if not isinstance(account, Mapping):
        return None
    email = _as_nonempty_string(account.get("email"))
    password = _as_nonempty_string(account.get("password"))
    username = _as_nonempty_string(account.get("username"))
    api_line = _as_nonempty_string(account.get("api_line"))
    if None in (email, password, username, api_line):
        return None
    assert email is not None and password is not None
    assert username is not None and api_line is not None
    if not EMAIL_RE.fullmatch(email) or email.casefold() != assigned_email.casefold():
        return None
    if not USERNAME_RE.fullmatch(username):
        return None
    try:
        credential = parse_credential_line(api_line, source_index=1)
    except ValueError:
        return None
    if credential.email.casefold() != assigned_email.casefold():
        return None
    if credential.mailbox_password != password:
        return None
    return AcceptedAccount(email, password, username, credential.raw_line)


def collect_results(
    results_dir: Path | str,
    *,
    expected_tasks: int | None = None,
) -> CollectionSummary:
    root = Path(results_dir).resolve()
    paths = sorted(root.rglob("result.json")) if root.is_dir() else []
    if expected_tasks is not None:
        expected = int(expected_tasks)
        if expected < 0 or expected < len(paths):
            raise ValueError(
                f"预期任务数量 {expected} 小于已发现 Artifact 数量 {len(paths)}"
            )
    else:
        expected = len(paths)

    accepted: list[AcceptedAccount] = []
    seen: set[str] = set()
    failed = 0
    ignored = 0
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            ignored += 1
            continue
        if not isinstance(payload, Mapping):
            ignored += 1
            continue
        if payload.get("schema_version") != 1:
            ignored += 1
            continue
        if payload.get("success") is False:
            failed += 1
            continue
        account = _accepted_account(payload)
        if account is None:
            ignored += 1
            continue
        normalized = account.email.casefold()
        if normalized in seen:
            ignored += 1
            continue
        seen.add(normalized)
        accepted.append(account)

    return CollectionSummary(
        accepted=tuple(accepted),
        total_artifacts=len(paths),
        failed=failed,
        ignored=ignored,
        missing=max(0, expected - len(paths)),
    )


def format_account_record(account: AcceptedAccount) -> str:
    return (
        f"账号：{account.email}\n"
        f"密码：{account.password}\n"
        f"用户名：{account.username}\n"
        "API：\n"
        f"{account.api_line}\n\n"
    )


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _existing_account_emails(text: str) -> set[str]:
    emails: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("账号："):
            continue
        email = line.removeprefix("账号：").strip()
        if email:
            emails.add(email.casefold())
    return emails


def apply_results(
    results_dir: Path | str,
    pool_path: Path | str,
    output_path: Path | str,
    *,
    expected_tasks: int | None = None,
) -> ApplySummary:
    collection = collect_results(results_dir, expected_tasks=expected_tasks)
    output = Path(output_path).resolve()
    existing = output.read_text(encoding="utf-8-sig") if output.is_file() else ""
    completed = _existing_account_emails(existing)
    additions = [
        account
        for account in collection.accepted
        if account.email.casefold() not in completed
    ]
    if additions:
        _atomic_write_text(
            output,
            existing + "".join(format_account_record(account) for account in additions),
        )
    elif not output.is_file():
        _atomic_write_text(output, existing)

    removal = remove_consumed_emails(
        pool_path, (account.email for account in collection.accepted)
    )
    total_tasks = expected_tasks if expected_tasks is not None else collection.total_artifacts
    return ApplySummary(
        total_tasks=int(total_tasks),
        successful=len(collection.accepted),
        failed=collection.failed,
        ignored=collection.ignored,
        missing=collection.missing,
        appended=len(additions),
        removed=removal.removed,
        remaining=removal.remaining,
    )


def render_actions_summary(summary: ApplySummary) -> str:
    return (
        "# GitHub V6 注册汇总\n\n"
        "| 项目 | 数量 |\n"
        "| --- | ---: |\n"
        f"| 任务总数 | {summary.total_tasks} |\n"
        f"| 成功账号 | {summary.successful} |\n"
        f"| 注册失败 | {summary.failed} |\n"
        f"| 忽略结果 | {summary.ignored} |\n"
        f"| 缺失结果 | {summary.missing} |\n"
        f"| 新增输出 | {summary.appended} |\n"
        f"| 删除邮箱 | {summary.removed} |\n"
        f"| 剩余邮箱 | {summary.remaining} |\n"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="汇总 GitHub V6 注册结果")
    parser.add_argument("--结果目录", required=True, help="下载后的 Artifact 根目录")
    parser.add_argument("--邮箱文件", default="email.txt", help="V6 邮箱池文件")
    parser.add_argument(
        "--成功账号文件", default="注册成功账号.txt", help="成功账号汇总文件"
    )
    parser.add_argument("--摘要文件", help="GitHub Actions Summary 文件")
    parser.add_argument("--预期任务数量", type=int, help="本次矩阵任务总数")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = apply_results(
            args.结果目录,
            args.邮箱文件,
            args.成功账号文件,
            expected_tasks=args.预期任务数量,
        )
    except Exception as exc:
        print(f"结果汇总失败: {exc}")
        return 1

    rendered = render_actions_summary(summary)
    if args.摘要文件:
        summary_path = Path(args.摘要文件).resolve()
        existing = (
            summary_path.read_text(encoding="utf-8")
            if summary_path.is_file()
            else ""
        )
        _atomic_write_text(summary_path, existing + rendered)
    print(
        "结果汇总完成："
        f"成功 {summary.successful}，失败 {summary.failed}，"
        f"忽略 {summary.ignored}，缺失 {summary.missing}，"
        f"删除邮箱 {summary.removed}，剩余邮箱 {summary.remaining}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AcceptedAccount",
    "ApplySummary",
    "CollectionSummary",
    "apply_results",
    "build_parser",
    "collect_results",
    "format_account_record",
    "main",
    "render_actions_summary",
]
