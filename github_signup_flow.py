#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RuyiPage 直连 GitHub 注册页面流程。"""

from __future__ import annotations

import logging
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from github_email_pool import EmailCredential
from github_launch_code_mail import PollResult, poll_github_launch_code


LOG = logging.getLogger("github_register_v6.flow")
GITHUB_SIGNUP_URL = (
    "https://github.com/signup?ref_cta=Sign+up&ref_loc=header+logged+out"
    "&ref_page=%2F&source=header-home"
)
EMAIL_SELECTOR = "#email"
PASSWORD_SELECTOR = "#password"
USERNAME_SELECTOR = "#login"
MARKETING_CONSENT_SELECTOR = 'css:[id="user_signup[marketing_consent]"]'
COPILOT_OPT_IN_SELECTOR = 'css:[id="user_signup[copilot_opt_in]"]'
CREATE_ACCOUNT_SELECTOR = "css:button.form-control"
LAUNCH_CODE_SELECTORS = tuple(f"#launch-code-{index}" for index in range(8))
SUCCESS_MESSAGE = "Your account was created successfully! Please sign in to continue."
NAVIGATION_COMMAND_TIMEOUT_SECONDS = 15
SUCCESS_STATE_JS = r"""return (() => {
  const selectors = [
    '.js-flash-alert > div:nth-child(1)',
    'div.flash-full:nth-child(2)'
  ];
  for (const selector of selectors) {
    const element = document.querySelector(selector);
    const text = String(element?.innerText || element?.textContent || '')
      .replace(/\s+/g, ' ').trim();
    if (text) return {selector, text};
  }
  return {selector: null, text: ''};
})()"""


def generate_username(
    account: EmailCredential,
    *,
    randbelow: Callable[[int], int] = secrets.randbelow,
) -> str:
    local_part = account.email.split("@", 1)[0]
    if len(local_part) < 9:
        raise ValueError(f"邮箱 @ 前至少需要 9 个字符: {account.email}")
    number = int(randbelow(1000))
    if not 0 <= number < 1000:
        raise ValueError("随机数生成器必须返回 0 到 999")
    return f"{local_part[:5]}{number:03d}{local_part[5:9]}"


def launch_direct_browser(
    *,
    profile_dir: Path,
    snapshot_dir: Path,
    headless: bool,
    launcher: Callable[..., Any] | None = None,
) -> Any:
    if launcher is None:
        try:
            import ruyipage
        except ImportError as exc:
            raise RuntimeError(
                "缺少 ruyiPage，请先安装依赖并运行 python -m ruyipage install"
            ) from exc
        launcher = ruyipage.launch

    profile_dir = profile_dir.resolve()
    snapshot_dir = snapshot_dir.resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    return launcher(
        headless=bool(headless),
        private=False,
        user_dir=str(profile_dir),
        window_size=(1920, 1080),
        timeout_page_load=60,
        timeout_script=60,
        close_on_exit=True,
        failure_snapshot=True,
        snapshot_dir=str(snapshot_dir),
    )


def wait_element(
    page: Any,
    selector: str,
    description: str,
    timeout: float,
) -> Any:
    deadline = time.monotonic() + max(0.1, float(timeout))
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            element = page.ele(selector, timeout=0.25)
            if element:
                return element
        except Exception as exc:
            last_error = exc
        time.sleep(0.25)
    detail = type(last_error).__name__ if last_error else "未找到"
    raise TimeoutError(
        f"等待元素超时: {description}，选择器={selector}，最后状态={detail}"
    )


def click_element(
    page: Any,
    selector: str,
    description: str,
    timeout: float,
) -> None:
    element = wait_element(page, selector, description, timeout)
    try:
        element.click()
    except Exception:
        element.click(by_js=True)


def _ensure_checkbox_checked(element: Any) -> None:
    checked = getattr(element, "is_checked", False)
    if callable(checked):
        checked = checked()
    if checked:
        return
    try:
        element.click()
    except Exception:
        element.click(by_js=True)


def fill_signup_form(
    page: Any,
    account: EmailCredential,
    username: str,
    *,
    timeout: float,
    waiter: Callable[..., Any] = wait_element,
    clicker: Callable[..., None] = click_element,
    sleep: Callable[[float], None] = time.sleep,
    utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> datetime:
    LOG.info("等待 GitHub 注册表单")
    for selector, description, value in (
        (EMAIL_SELECTOR, "GitHub 邮箱输入框", account.email),
        (PASSWORD_SELECTOR, "GitHub 密码输入框", account.mailbox_password),
        (USERNAME_SELECTOR, "GitHub 用户名输入框", username),
    ):
        waiter(page, selector, description, timeout).input(value, clear=True)

    for selector, description in (
        (MARKETING_CONSENT_SELECTOR, "邮件偏好复选框"),
        (COPILOT_OPT_IN_SELECTOR, "GitHub Copilot 复选框"),
    ):
        _ensure_checkbox_checked(waiter(page, selector, description, timeout))

    LOG.info("注册表单填写完成，等待 5 秒后点击创建账号")
    sleep(5.0)
    submitted_at = utcnow().astimezone(timezone.utc)
    clicker(page, CREATE_ACCOUNT_SELECTOR, "创建账号按钮", timeout)
    LOG.info("已点击创建账号，等待邮箱验证码页面")
    return submitted_at


def wait_for_launch_code_stage(
    page: Any,
    *,
    timeout: float,
    waiter: Callable[..., Any] = wait_element,
) -> None:
    waiter(page, LAUNCH_CODE_SELECTORS[0], "GitHub 验证码第一位输入框", timeout)


def enter_launch_code(
    page: Any,
    code: str,
    *,
    timeout: float,
    waiter: Callable[..., Any] = wait_element,
) -> None:
    if not re.fullmatch(r"\d{8}", str(code)):
        raise ValueError("GitHub 邮箱验证码必须是 8 位数字")
    for index, (selector, character) in enumerate(zip(LAUNCH_CODE_SELECTORS, code)):
        element = waiter(
            page,
            selector,
            f"GitHub 验证码第 {index + 1} 位输入框",
            timeout,
        )
        element.input(character, clear=True)


def wait_for_registration_success(
    page: Any,
    *,
    timeout: float,
    interval: float = 0.25,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    deadline = monotonic() + max(0.1, float(timeout))
    while monotonic() < deadline:
        try:
            state = page.run_js(SUCCESS_STATE_JS, timeout=5)
        except Exception:
            state = None
        if isinstance(state, dict):
            text = re.sub(r"\s+", " ", str(state.get("text") or "")).strip()
            if text == SUCCESS_MESSAGE:
                return True
        remaining = deadline - monotonic()
        if remaining > 0:
            sleep(min(max(0.05, float(interval)), remaining))
    return False


def perform_registration(
    page: Any,
    account: EmailCredential,
    username: str,
    *,
    form_timeout: float,
    mail_timeout: float,
    success_timeout: float,
    form_filler: Callable[..., datetime] = fill_signup_form,
    stage_waiter: Callable[..., None] = wait_for_launch_code_stage,
    mail_poller: Callable[..., PollResult] = poll_github_launch_code,
    code_enterer: Callable[..., None] = enter_launch_code,
    success_waiter: Callable[..., bool] = wait_for_registration_success,
) -> PollResult:
    LOG.info("发送 GitHub 注册页非阻塞导航命令")
    page.get(
        GITHUB_SIGNUP_URL,
        wait="none",
        timeout=NAVIGATION_COMMAND_TIMEOUT_SECONDS,
    )
    LOG.info("导航命令已返回，开始等待 GitHub 注册表单")
    submitted_at = form_filler(
        page, account, username, timeout=form_timeout
    )
    stage_waiter(page, timeout=form_timeout)
    LOG.info("邮箱验证码页面已出现，开始读取 GitHub 验证邮件")
    mail_result = mail_poller(
        client_id=account.client_id,
        refresh_token=account.refresh_token,
        not_before=submitted_at,
        timeout=mail_timeout,
    )
    LOG.info(
        "已读取 GitHub 验证邮件：扫描 %d 封，发件人匹配 %d 封",
        mail_result.scanned,
        mail_result.sender_matches,
    )
    code_enterer(page, mail_result.code, timeout=form_timeout)
    if not success_waiter(page, timeout=success_timeout):
        raise TimeoutError("等待 GitHub 注册成功提示超时")
    return mail_result


def close_browser(page: Any) -> None:
    page.quit(timeout=10, force=True)


__all__ = [
    "COPILOT_OPT_IN_SELECTOR",
    "CREATE_ACCOUNT_SELECTOR",
    "EMAIL_SELECTOR",
    "GITHUB_SIGNUP_URL",
    "LAUNCH_CODE_SELECTORS",
    "MARKETING_CONSENT_SELECTOR",
    "PASSWORD_SELECTOR",
    "SUCCESS_MESSAGE",
    "USERNAME_SELECTOR",
    "click_element",
    "close_browser",
    "enter_launch_code",
    "fill_signup_form",
    "generate_username",
    "launch_direct_browser",
    "perform_registration",
    "wait_element",
    "wait_for_launch_code_stage",
    "wait_for_registration_success",
]
