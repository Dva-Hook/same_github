#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在注册浏览器会话中完成一次 GitHub 登录确认。"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlsplit

from github_device_code_mail import PollResult, poll_github_device_code
from github_email_pool import EmailCredential


LOG = logging.getLogger("github_register_v6.post_login")
GITHUB_LOGIN_URL = "https://github.com/login"
LOGIN_FIELD_SELECTOR = "#login_field"
PASSWORD_SELECTOR = "#password"
SIGN_IN_SELECTOR = "css:input.btn"
OTP_SELECTOR = "#otp"
DASHBOARD_SELECTOR = ".styles-module__contextCrumbLast__tI2e3"
NAVIGATION_COMMAND_TIMEOUT_SECONDS = 15
LOGIN_STATE_JS = r"""return (() => {
  const dashboard = document.querySelector(
    '.styles-module__contextCrumbLast__tI2e3'
  );
  const dashboardText = String(
    dashboard?.innerText || dashboard?.textContent || ''
  ).replace(/\s+/g, ' ').trim();
  const userLogin = String(
    document.querySelector('meta[name="user-login"]')?.content || ''
  ).trim();
  return {
    href: String(location.href || ''),
    dashboard: Boolean(dashboard),
    dashboardText,
    userLogin,
    loginForm: Boolean(document.querySelector('#login_field, form[action="/session"]')),
    otp: Boolean(document.querySelector('#otp'))
  };
})()"""


@dataclass(frozen=True, slots=True)
class PostRegistrationLoginResult:
    success: bool
    otp_used: bool
    scanned: int = 0
    sender_matches: int = 0


def wait_element(page: Any, selector: str, description: str, timeout: float) -> Any:
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


def click_element(page: Any, selector: str, description: str, timeout: float) -> None:
    element = wait_element(page, selector, description, timeout)
    try:
        element.click()
    except Exception:
        element.click(by_js=True)


def fill_login_form(
    page: Any,
    account: EmailCredential,
    *,
    timeout: float,
    waiter: Callable[..., Any] = wait_element,
    clicker: Callable[..., None] = click_element,
    utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> datetime:
    for selector, description, value in (
        (LOGIN_FIELD_SELECTOR, "GitHub 账号输入框", account.email),
        (PASSWORD_SELECTOR, "GitHub 密码输入框", account.mailbox_password),
    ):
        waiter(page, selector, description, timeout).input(value, clear=True)
    submitted_at = utcnow().astimezone(timezone.utc)
    clicker(page, SIGN_IN_SELECTOR, "GitHub 登录按钮", timeout)
    return submitted_at


def read_login_state(page: Any) -> dict[str, Any]:
    try:
        state = page.run_js(LOGIN_STATE_JS, timeout=5)
    except Exception:
        return {}
    return dict(state) if isinstance(state, dict) else {}


def prepare_login_page(page: Any) -> None:
    state = read_login_state(page)
    current_url = str(state.get("href") or "").strip()
    parsed = urlsplit(current_url)
    already_on_login_page = (
        parsed.netloc.casefold() == "github.com"
        and parsed.path.rstrip("/") == "/login"
        and bool(state.get("loginForm"))
    )
    if already_on_login_page:
        LOG.info("当前页面已经是 GitHub 登录页，直接复用现有登录表单")
        return

    LOG.info("在当前浏览器打开 GitHub 登录页")
    page.get(
        GITHUB_LOGIN_URL,
        wait="none",
        timeout=NAVIGATION_COMMAND_TIMEOUT_SECONDS,
    )


def is_dashboard_state(state: dict[str, Any]) -> bool:
    dashboard_text = re.sub(
        r"\s+", " ", str(state.get("dashboardText") or "")
    ).strip()
    exact_dashboard = bool(state.get("dashboard")) and (
        dashboard_text.casefold() == "dashboard"
    )
    authenticated_meta = bool(str(state.get("userLogin") or "").strip()) and not bool(
        state.get("loginForm")
    )
    return exact_dashboard or authenticated_meta


def wait_for_login_outcome(
    page: Any,
    *,
    timeout: float,
    interval: float = 0.25,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    deadline = monotonic() + max(0.1, float(timeout))
    while monotonic() < deadline:
        state = read_login_state(page)
        if is_dashboard_state(state):
            return "dashboard"
        if state.get("otp"):
            return "otp"
        remaining = deadline - monotonic()
        if remaining > 0:
            sleep(min(max(0.05, float(interval)), remaining))
    raise TimeoutError("登录后未出现 Dashboard 或设备验证码输入框")


def enter_device_code(
    page: Any,
    code: str,
    *,
    timeout: float,
    waiter: Callable[..., Any] = wait_element,
) -> None:
    if not re.fullmatch(r"\d{6}", str(code or "")):
        raise ValueError("GitHub 设备验证码必须是 6 位数字")
    waiter(page, OTP_SELECTOR, "GitHub 设备验证码输入框", timeout).input(
        code, clear=True
    )


def wait_for_dashboard(
    page: Any,
    *,
    timeout: float,
    interval: float = 0.25,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    deadline = monotonic() + max(0.1, float(timeout))
    while monotonic() < deadline:
        if is_dashboard_state(read_login_state(page)):
            return True
        remaining = deadline - monotonic()
        if remaining > 0:
            sleep(min(max(0.05, float(interval)), remaining))
    return False


def perform_post_registration_login(
    page: Any,
    account: EmailCredential,
    *,
    form_timeout: float,
    mail_timeout: float,
    success_timeout: float,
    form_filler: Callable[..., datetime] = fill_login_form,
    outcome_waiter: Callable[..., str] = wait_for_login_outcome,
    mail_poller: Callable[..., PollResult] = poll_github_device_code,
    code_enterer: Callable[..., None] = enter_device_code,
    dashboard_waiter: Callable[..., bool] = wait_for_dashboard,
) -> PostRegistrationLoginResult:
    prepare_login_page(page)
    submitted_at = form_filler(page, account, timeout=form_timeout)
    outcome = outcome_waiter(page, timeout=form_timeout)
    if outcome == "dashboard":
        LOG.info("GitHub 首次登录成功，无需设备验证码")
        return PostRegistrationLoginResult(success=True, otp_used=False)

    LOG.info("GitHub 登录要求设备验证码，开始读取验证邮件")
    mail_result = mail_poller(
        client_id=account.client_id,
        refresh_token=account.refresh_token,
        not_before=submitted_at,
        timeout=mail_timeout,
    )
    LOG.info(
        "已读取 GitHub 设备验证邮件：扫描 %d 封，发件人匹配 %d 封",
        mail_result.scanned,
        mail_result.sender_matches,
    )
    code_enterer(page, mail_result.code, timeout=form_timeout)
    if not dashboard_waiter(page, timeout=success_timeout):
        raise TimeoutError("输入设备验证码后等待 GitHub Dashboard 超时")
    LOG.info("GitHub 首次登录及设备验证成功")
    return PostRegistrationLoginResult(
        success=True,
        otp_used=True,
        scanned=mail_result.scanned,
        sender_matches=mail_result.sender_matches,
    )


__all__ = [
    "DASHBOARD_SELECTOR",
    "GITHUB_LOGIN_URL",
    "LOGIN_FIELD_SELECTOR",
    "LOGIN_STATE_JS",
    "OTP_SELECTOR",
    "PASSWORD_SELECTOR",
    "PostRegistrationLoginResult",
    "SIGN_IN_SELECTOR",
    "enter_device_code",
    "fill_login_form",
    "is_dashboard_state",
    "perform_post_registration_login",
    "prepare_login_page",
    "read_login_state",
    "wait_for_dashboard",
    "wait_for_login_outcome",
]
