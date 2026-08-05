#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""执行一个 GitHub Actions 矩阵注册任务。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

from github_email_pool import EmailCredential, select_email_credential
from github_cli import ChineseArgumentParser
from github_signup_flow import (
    close_browser,
    generate_username,
    launch_direct_browser,
    perform_registration,
)


LOG = logging.getLogger("github_register_v6.job")
SCHEMA_VERSION = 1


def format_account_record(account: EmailCredential, username: str) -> str:
    return (
        f"账号：{account.email}\n"
        f"密码：{account.mailbox_password}\n"
        f"用户名：{username}\n"
        "API：\n"
        f"{account.raw_line}\n\n"
    )


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _write_result(path: Path, result: dict[str, Any]) -> None:
    _atomic_write_text(
        path,
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _redact_error(exc: BaseException, account: EmailCredential) -> str:
    text = str(exc).strip() or type(exc).__name__
    for secret in (
        account.mailbox_password,
        account.client_id,
        account.refresh_token,
        account.raw_line,
    ):
        if secret:
            text = text.replace(secret, "<已隐藏>")
    return text[:500]


def run_single_attempt(
    *,
    account: EmailCredential,
    username: str,
    profile_dir: Path,
    snapshot_dir: Path,
    headless: bool,
    form_timeout: float,
    mail_timeout: float,
    success_timeout: float,
    browser_launcher: Callable[..., Any] = launch_direct_browser,
    performer: Callable[..., Any] = perform_registration,
    browser_closer: Callable[[Any], None] = close_browser,
) -> str:
    page: Any = None
    try:
        page = browser_launcher(
            profile_dir=profile_dir,
            snapshot_dir=snapshot_dir,
            headless=headless,
        )
        performer(
            page,
            account,
            username,
            form_timeout=form_timeout,
            mail_timeout=mail_timeout,
            success_timeout=success_timeout,
        )
        return username
    finally:
        if page is not None:
            browser_closer(page)


def run_job(
    account: EmailCredential,
    *,
    output_dir: Path,
    max_attempts: int = 1,
    headless: bool = False,
    form_timeout: float = 60.0,
    mail_timeout: float = 180.0,
    success_timeout: float = 30.0,
    attempt_runner: Callable[..., str] = run_single_attempt,
) -> dict[str, Any]:
    attempts_limit = int(max_attempts)
    if attempts_limit != 1:
        raise ValueError("最大尝试次数必须为 1，避免重复提交同一邮箱注册")

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    errors: list[dict[str, str | int]] = []
    succeeded_username: str | None = None
    completed_attempts = 0

    for attempt in range(1, attempts_limit + 1):
        completed_attempts = attempt
        profile_dir = output_dir / "profiles" / f"attempt-{attempt}"
        snapshot_dir = output_dir / "失败截图" / f"attempt-{attempt}"
        profile_dir.mkdir(parents=True, exist_ok=False)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        username = generate_username(account)
        LOG.info("开始第 %d/%d 次注册尝试: %s", attempt, attempts_limit, account.email)
        try:
            succeeded_username = attempt_runner(
                account=account,
                username=username,
                profile_dir=profile_dir,
                snapshot_dir=snapshot_dir,
                headless=headless,
                form_timeout=form_timeout,
                mail_timeout=mail_timeout,
                success_timeout=success_timeout,
            )
            LOG.info("GitHub 注册成功: %s", account.email)
            break
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            redacted = _redact_error(exc, account)
            errors.append(
                {
                    "attempt": attempt,
                    "type": type(exc).__name__,
                    "message": redacted,
                }
            )
            LOG.warning(
                "第 %d/%d 次注册失败: %s（%s）",
                attempt,
                attempts_limit,
                account.email,
                type(exc).__name__,
            )
        finally:
            shutil.rmtree(profile_dir, ignore_errors=True)

    if succeeded_username:
        result: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "success": True,
            "assigned_email": account.email,
            "attempts": completed_attempts,
            "account": {
                "email": account.email,
                "password": account.mailbox_password,
                "username": succeeded_username,
                "api_line": account.raw_line,
            },
            "verification": {
                "code_submitted": True,
                "success_banner": True,
            },
        }
        _atomic_write_text(
            output_dir / "账号.txt",
            format_account_record(account, succeeded_username),
        )
    else:
        result = {
            "schema_version": SCHEMA_VERSION,
            "success": False,
            "assigned_email": account.email,
            "attempts": completed_attempts,
            "errors": errors,
        }

    _write_result(output_dir / "result.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        description="使用 RuyiPage 直连执行一个 GitHub 注册矩阵任务"
    )
    parser.add_argument("--邮箱文件", default="email.txt", help="V6 四字段邮箱文件")
    parser.add_argument("--任务索引", type=int, required=True, help="从 1 开始的矩阵索引")
    parser.add_argument("--输出目录", required=True, help="当前任务的 Artifact 目录")
    parser.add_argument(
        "--最大尝试次数",
        type=int,
        choices=(1,),
        default=1,
        help="每个邮箱只提交 1 次注册，验证码在当前页面内重试",
    )
    parser.add_argument("--表单超时秒数", type=float, default=60.0)
    parser.add_argument("--邮件超时秒数", type=float, default=180.0)
    parser.add_argument("--成功确认超时秒数", type=float, default=30.0)
    parser.add_argument("--无头", action="store_true", help="使用无头浏览器")
    return parser


def _localize_logging_levels() -> None:
    logging.addLevelName(logging.INFO, "信息")
    logging.addLevelName(logging.WARNING, "警告")
    logging.addLevelName(logging.ERROR, "错误")
    logging.addLevelName(logging.CRITICAL, "严重错误")


def _configure_logging(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _localize_logging_levels()
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
    )
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    file_handler = logging.FileHandler(
        output_dir / "任务日志.txt", encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root = logging.getLogger()
    for handler in tuple(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.INFO)
    root.addHandler(stream)
    root.addHandler(file_handler)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = Path(args.输出目录).resolve()
    _configure_logging(output_dir)
    try:
        account = select_email_credential(Path(args.邮箱文件), args.任务索引)
    except Exception as exc:
        LOG.error("邮箱分配失败: %s", exc)
        return 2

    LOG.info("任务 %d 已分配邮箱: %s", args.任务索引, account.email)
    try:
        result = run_job(
            account,
            output_dir=output_dir,
            max_attempts=args.最大尝试次数,
            headless=bool(args.无头),
            form_timeout=args.表单超时秒数,
            mail_timeout=args.邮件超时秒数,
            success_timeout=args.成功确认超时秒数,
        )
    except KeyboardInterrupt:
        LOG.warning("收到中断，当前任务停止")
        return 130
    except Exception as exc:
        LOG.error("任务执行异常: %s", type(exc).__name__)
        return 2
    return 0 if result.get("success") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "SCHEMA_VERSION",
    "build_parser",
    "format_account_record",
    "main",
    "run_job",
    "run_single_attempt",
]
