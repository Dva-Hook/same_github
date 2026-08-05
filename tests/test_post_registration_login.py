from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock

import github_post_registration_login as login
from github_device_code_mail import PollResult
from github_email_pool import parse_credential_line


NOW = datetime(2026, 8, 6, 10, 0, tzinfo=timezone.utc)


def _account():
    return parse_credential_line(
        "CarlyJohnston@example.com----password1----client-id----refresh-token",
        source_index=1,
    )


def test_login_without_device_code_requires_dashboard() -> None:
    page = Mock()
    form = Mock(return_value=NOW)
    outcome = Mock(return_value="dashboard")
    mail = Mock()

    result = login.perform_post_registration_login(
        page,
        _account(),
        form_timeout=60,
        mail_timeout=180,
        success_timeout=30,
        form_filler=form,
        outcome_waiter=outcome,
        mail_poller=mail,
    )

    page.get.assert_called_once_with(login.GITHUB_LOGIN_URL, wait="none", timeout=15)
    form.assert_called_once_with(page, _account(), timeout=60)
    outcome.assert_called_once_with(page, timeout=60)
    mail.assert_not_called()
    assert result.success is True
    assert result.otp_used is False


def test_login_completes_device_code_before_dashboard() -> None:
    page = Mock()
    poller = Mock(return_value=PollResult("123456", "rotated", 4, 1))
    enter = Mock()
    dashboard = Mock(return_value=True)

    result = login.perform_post_registration_login(
        page,
        _account(),
        form_timeout=60,
        mail_timeout=180,
        success_timeout=30,
        form_filler=lambda *_args, **_kwargs: NOW,
        outcome_waiter=lambda *_args, **_kwargs: "otp",
        mail_poller=poller,
        code_enterer=enter,
        dashboard_waiter=dashboard,
    )

    poller.assert_called_once_with(
        client_id="client-id",
        refresh_token="refresh-token",
        not_before=NOW,
        timeout=180,
    )
    enter.assert_called_once_with(page, "123456", timeout=60)
    dashboard.assert_called_once_with(page, timeout=30)
    assert result.success is True
    assert result.otp_used is True
    assert result.scanned == 4


def test_dashboard_state_requires_confirmed_logged_in_signal() -> None:
    assert login.is_dashboard_state(
        {"dashboard": True, "dashboardText": "Dashboard", "userLogin": ""}
    )
    assert login.is_dashboard_state(
        {"dashboard": False, "dashboardText": "", "userLogin": "Carly007John"}
    )
    assert not login.is_dashboard_state(
        {"dashboard": False, "dashboardText": "", "userLogin": ""}
    )


def test_existing_login_page_is_reused_without_duplicate_navigation() -> None:
    page = Mock()
    page.run_js.return_value = {
        "href": "https://github.com/login?return_to=%2Fdashboard",
        "loginForm": True,
    }

    login.prepare_login_page(page)

    page.get.assert_not_called()


def test_missing_login_form_triggers_nonblocking_navigation() -> None:
    page = Mock()
    page.run_js.return_value = {
        "href": "https://github.com/account_verifications",
        "loginForm": False,
    }

    login.prepare_login_page(page)

    page.get.assert_called_once_with(login.GITHUB_LOGIN_URL, wait="none", timeout=15)
