from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import github_signup_flow as flow
from github_email_pool import parse_credential_line
from github_launch_code_mail import PollResult


@pytest.fixture
def account():
    return parse_credential_line(
        "CarlyJohnston@example.com----password1----client-id----refresh-token",
        source_index=1,
    )


class Element:
    def __init__(self, *, checked: bool = False) -> None:
        self.is_checked = checked
        self.inputs: list[tuple[str, bool]] = []
        self.clicks: list[dict[str, object]] = []

    def input(self, value: str, *, clear: bool) -> None:
        self.inputs.append((value, clear))

    def click(self, **kwargs: object) -> None:
        self.clicks.append(kwargs)
        self.is_checked = True


def test_username_and_fixed_selectors(account) -> None:
    assert flow.generate_username(account, randbelow=lambda _: 7) == "Carly007John"
    assert flow.CREATE_ACCOUNT_SELECTOR == "css:button.form-control"
    assert flow.LAUNCH_CODE_SELECTORS == tuple(f"#launch-code-{i}" for i in range(8))
    with pytest.raises(ValueError, match="至少需要 9 个字符"):
        short = parse_credential_line(
            "short@example.com----password----client----refresh", source_index=1
        )
        flow.generate_username(short)


def test_direct_browser_launch_never_sets_proxy(tmp_path: Path) -> None:
    launcher = Mock(return_value=object())

    result = flow.launch_direct_browser(
        profile_dir=tmp_path / "profile",
        snapshot_dir=tmp_path / "snapshots",
        headless=False,
        launcher=launcher,
    )

    assert result is launcher.return_value
    options = launcher.call_args.kwargs
    assert "proxy" not in options
    assert options["private"] is False
    assert options["user_dir"] == str((tmp_path / "profile").resolve())
    assert options["window_size"] == (1920, 1080)
    assert options["failure_snapshot"] is True


def test_fill_form_checks_options_waits_then_clicks(account) -> None:
    elements = {
        flow.EMAIL_SELECTOR: Element(),
        flow.PASSWORD_SELECTOR: Element(),
        flow.USERNAME_SELECTOR: Element(),
        flow.MARKETING_CONSENT_SELECTOR: Element(checked=False),
        flow.COPILOT_OPT_IN_SELECTOR: Element(checked=True),
    }
    events: list[tuple[str, object]] = []
    submitted = datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc)
    waiter = Mock(side_effect=lambda _page, selector, _description, _timeout: elements[selector])

    result = flow.fill_signup_form(
        object(),
        account,
        "Carly007John",
        timeout=30,
        waiter=waiter,
        clicker=lambda _page, selector, _description, _timeout: events.append(
            ("点击", selector)
        ),
        sleep=lambda seconds: events.append(("等待", seconds)),
        utcnow=lambda: submitted,
    )

    assert result == submitted
    assert elements[flow.EMAIL_SELECTOR].inputs == [(account.email, True)]
    assert elements[flow.PASSWORD_SELECTOR].inputs == [(account.mailbox_password, True)]
    assert elements[flow.USERNAME_SELECTOR].inputs == [("Carly007John", True)]
    assert len(elements[flow.MARKETING_CONSENT_SELECTOR].clicks) == 1
    assert not elements[flow.COPILOT_OPT_IN_SELECTOR].clicks
    assert events == [("等待", 5.0), ("点击", "css:button.form-control")]


def test_enters_all_eight_digits(account) -> None:
    elements = {selector: Element() for selector in flow.LAUNCH_CODE_SELECTORS}
    waiter = lambda _page, selector, _description, _timeout: elements[selector]

    flow.enter_launch_code(object(), "52778203", timeout=20, waiter=waiter)

    for index, selector in enumerate(flow.LAUNCH_CODE_SELECTORS):
        assert elements[selector].inputs == [("52778203"[index], True)]
    with pytest.raises(ValueError, match="8 位数字"):
        flow.enter_launch_code(object(), "1234567", timeout=20, waiter=waiter)


class SuccessPage:
    def __init__(self, results: list[dict[str, str]]) -> None:
        self.results = iter(results)

    def run_js(self, _script: str, timeout: float) -> dict[str, str]:
        assert timeout == 5
        return next(self.results)


def test_success_requires_exact_message() -> None:
    page = SuccessPage(
        [
            {"text": "Your account is ready"},
            {"text": flow.SUCCESS_MESSAGE},
        ]
    )
    clock = iter([0.0, 0.0, 0.1, 0.1])

    assert flow.wait_for_registration_success(
        page,
        timeout=1,
        monotonic=clock.__next__,
        sleep=lambda _seconds: None,
    )


def test_perform_registration_uses_mail_and_success_contract(account) -> None:
    page = Mock()
    page.get = Mock()
    submitted = datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc)
    poller = Mock(
        return_value=PollResult(
            code="52778203",
            refresh_token="rotated",
            scanned=2,
            sender_matches=1,
        )
    )

    fill = Mock(return_value=submitted)
    wait_stage = Mock()
    enter = Mock()
    success = Mock(return_value=True)
    result = flow.perform_registration(
        page,
        account,
        "Carly007John",
        form_timeout=60,
        mail_timeout=180,
        success_timeout=30,
        form_filler=fill,
        stage_waiter=wait_stage,
        mail_poller=poller,
        code_enterer=enter,
        success_waiter=success,
    )

    page.get.assert_called_once_with(flow.GITHUB_SIGNUP_URL, wait="interactive", timeout=60)
    fill.assert_called_once_with(page, account, "Carly007John", timeout=60)
    wait_stage.assert_called_once_with(page, timeout=60)
    poller.assert_called_once_with(
        client_id=account.client_id,
        refresh_token=account.refresh_token,
        not_before=submitted,
        timeout=180,
    )
    enter.assert_called_once_with(page, "52778203", timeout=60)
    success.assert_called_once_with(page, timeout=30)
    assert result.code == "52778203"


def test_perform_registration_rejects_missing_success_banner(account) -> None:
    page = SimpleNamespace(get=lambda *_args, **_kwargs: None)
    with pytest.raises(TimeoutError, match="注册成功提示"):
        flow.perform_registration(
            page,
            account,
            "Carly007John",
            form_timeout=60,
            mail_timeout=180,
            success_timeout=30,
            form_filler=lambda *_args, **_kwargs: datetime.now(timezone.utc),
            stage_waiter=lambda *_args, **_kwargs: None,
            mail_poller=lambda **_kwargs: PollResult("52778203", "refresh", 1, 1),
            code_enterer=lambda *_args, **_kwargs: None,
            success_waiter=lambda *_args, **_kwargs: False,
        )
