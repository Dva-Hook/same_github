from __future__ import annotations

import json
from pathlib import Path

import pytest

import github_register_job as job
from github_email_pool import parse_credential_line


@pytest.fixture
def account():
    return parse_credential_line(
        "CarlyJohnston@example.com----password1----client-id----refresh-token",
        source_index=1,
    )


def test_job_uses_three_distinct_profiles_and_stops_on_success(
    tmp_path: Path, account
) -> None:
    profiles: list[Path] = []
    outcomes = iter([RuntimeError("第一次失败"), RuntimeError("第二次失败"), None])

    def attempt(*, profile_dir: Path, **_kwargs: object) -> str:
        profiles.append(profile_dir)
        outcome = next(outcomes)
        if outcome:
            raise outcome
        return "Carly007John"

    result = job.run_job(
        account,
        output_dir=tmp_path,
        max_attempts=3,
        attempt_runner=attempt,
    )

    assert result["success"] is True
    assert result["attempts"] == 3
    assert len(set(profiles)) == 3
    assert all(not profile.exists() for profile in profiles)


def test_success_result_has_complete_verification_schema(tmp_path: Path, account) -> None:
    result = job.run_job(
        account,
        output_dir=tmp_path,
        max_attempts=3,
        attempt_runner=lambda **_kwargs: "Carly007John",
    )

    persisted = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    account_text = (tmp_path / "账号.txt").read_text(encoding="utf-8")
    assert result == persisted
    assert persisted["schema_version"] == 1
    assert persisted["assigned_email"] == account.email
    assert persisted["verification"] == {
        "code_submitted": True,
        "success_banner": True,
    }
    assert persisted["account"]["username"] == "Carly007John"
    assert persisted["account"]["api_line"] == account.raw_line
    assert account_text == job.format_account_record(account, "Carly007John")


def test_failed_job_never_contains_credentials(tmp_path: Path, account) -> None:
    def fail(**_kwargs: object) -> str:
        raise RuntimeError(
            f"失败 {account.mailbox_password} {account.client_id} {account.refresh_token}"
        )

    result = job.run_job(
        account,
        output_dir=tmp_path,
        max_attempts=3,
        attempt_runner=fail,
    )

    text = json.dumps(result, ensure_ascii=False)
    assert result["success"] is False
    assert result["attempts"] == 3
    assert account.mailbox_password not in text
    assert account.client_id not in text
    assert account.refresh_token not in text
    assert not (tmp_path / "账号.txt").exists()


def test_max_attempts_is_hard_limited(account, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="1 到 3"):
        job.run_job(
            account,
            output_dir=tmp_path,
            max_attempts=4,
            attempt_runner=lambda **_kwargs: "name",
        )


def test_cli_uses_chinese_flags_and_has_no_proxy_option() -> None:
    parser = job.build_parser()
    options = {option for action in parser._actions for option in action.option_strings}

    assert "--邮箱文件" in options
    assert "--任务索引" in options
    assert "--最大尝试次数" in options
    assert "--proxy" not in options
    with pytest.raises(SystemExit):
        parser.parse_args(["--任务索引", "1", "--输出目录", "out", "--最大尝试次数", "4"])


def test_default_attempt_always_closes_browser(tmp_path: Path, account) -> None:
    page = object()
    events: list[str] = []

    username = job.run_single_attempt(
        account=account,
        username="Carly007John",
        profile_dir=tmp_path / "profile",
        snapshot_dir=tmp_path / "snapshots",
        headless=False,
        form_timeout=60,
        mail_timeout=180,
        success_timeout=30,
        browser_launcher=lambda **_kwargs: page,
        performer=lambda *_args, **_kwargs: events.append("执行"),
        browser_closer=lambda value: events.append("关闭") if value is page else None,
    )

    assert username == "Carly007John"
    assert events == ["执行", "关闭"]


def test_default_attempt_closes_browser_after_error(tmp_path: Path, account) -> None:
    page = object()
    events: list[str] = []

    with pytest.raises(RuntimeError, match="页面失败"):
        job.run_single_attempt(
            account=account,
            username="Carly007John",
            profile_dir=tmp_path / "profile",
            snapshot_dir=tmp_path / "snapshots",
            headless=False,
            form_timeout=60,
            mail_timeout=180,
            success_timeout=30,
            browser_launcher=lambda **_kwargs: page,
            performer=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("页面失败")
            ),
            browser_closer=lambda value: events.append("关闭") if value is page else None,
        )

    assert events == ["关闭"]
