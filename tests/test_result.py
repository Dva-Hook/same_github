from __future__ import annotations

import json
from pathlib import Path

import pytest

import github_result as result_module


def _api(email: str, suffix: str) -> str:
    return f"{email}----password-{suffix}----client-{suffix}----refresh-{suffix}"


def _write_result(
    path: Path,
    *,
    success: bool,
    email: str,
    assigned_email: str | None = None,
    username: str = "Carly007John",
    code_submitted: bool = True,
    success_banner: bool = True,
    login_confirmed: bool = True,
    recovered_existing: bool = False,
    suffix: str = "1",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "schema_version": 1,
        "success": success,
        "assigned_email": assigned_email or email,
        "attempts": 1,
    }
    if success:
        payload["account"] = {
            "email": email,
            "password": f"password-{suffix}",
            "username": username,
            "api_line": _api(email, suffix),
        }
        payload["verification"] = {
            "code_submitted": code_submitted,
            "success_banner": success_banner,
            "login_confirmed": login_confirmed,
            "recovered_existing": recovered_existing,
        }
    else:
        payload["errors"] = [{"attempt": 1, "type": "RuntimeError", "message": "失败"}]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_collect_accepts_only_matching_verified_success(tmp_path: Path) -> None:
    _write_result(
        tmp_path / "job-1" / "result.json",
        success=True,
        email="A12345678@example.com",
        assigned_email="a12345678@EXAMPLE.COM",
    )
    _write_result(
        tmp_path / "job-2" / "result.json",
        success=True,
        email="wrong@example.com",
        assigned_email="B12345678@example.com",
        suffix="2",
    )
    _write_result(
        tmp_path / "job-3" / "result.json",
        success=True,
        email="C12345678@example.com",
        code_submitted=False,
        suffix="3",
    )
    _write_result(
        tmp_path / "job-4" / "result.json",
        success=False,
        email="D12345678@example.com",
        suffix="4",
    )
    (tmp_path / "job-5").mkdir()
    (tmp_path / "job-5" / "result.json").write_text("{broken", encoding="utf-8")

    summary = result_module.collect_results(tmp_path)

    assert [item.email for item in summary.accepted] == ["A12345678@example.com"]
    assert summary.total_artifacts == 5
    assert summary.failed == 1
    assert summary.ignored == 3


def test_collect_deduplicates_successes_case_insensitively(tmp_path: Path) -> None:
    _write_result(
        tmp_path / "job-1" / "result.json",
        success=True,
        email="A12345678@example.com",
    )
    _write_result(
        tmp_path / "job-2" / "result.json",
        success=True,
        email="a12345678@EXAMPLE.COM",
    )

    summary = result_module.collect_results(tmp_path)

    assert len(summary.accepted) == 1
    assert summary.ignored == 1


def test_apply_is_idempotent_and_keeps_failed_email(tmp_path: Path) -> None:
    results = tmp_path / "artifacts"
    pool = tmp_path / "email.txt"
    output = tmp_path / "注册成功账号.txt"
    success_email = "A12345678@example.com"
    failed_email = "B12345678@example.com"
    pool.write_text(
        _api(success_email, "1") + "\n" + _api(failed_email, "2") + "\n",
        encoding="utf-8",
    )
    output.write_text("", encoding="utf-8")
    _write_result(
        results / "job-1" / "result.json",
        success=True,
        email=success_email,
    )
    _write_result(
        results / "job-2" / "result.json",
        success=False,
        email=failed_email,
        suffix="2",
    )

    first = result_module.apply_results(results, pool, output, expected_tasks=2)
    second = result_module.apply_results(results, pool, output, expected_tasks=2)

    assert first.appended == 1
    assert first.removed == 1
    assert first.remaining == 1
    assert second.appended == 0
    assert second.removed == 0
    assert pool.read_text(encoding="utf-8") == _api(failed_email, "2") + "\n"
    assert output.read_text(encoding="utf-8") == (
        f"账号：{success_email}\n"
        "密码：password-1\n"
        "用户名：Carly007John\n"
        "API：\n"
        f"{_api(success_email, '1')}\n\n"
    )


def test_missing_artifacts_are_reported_without_deleting_unknown_email(tmp_path: Path) -> None:
    results = tmp_path / "artifacts"
    results.mkdir()
    pool = tmp_path / "email.txt"
    output = tmp_path / "注册成功账号.txt"
    pool.write_text(_api("A12345678@example.com", "1") + "\n", encoding="utf-8")
    output.write_text("", encoding="utf-8")

    summary = result_module.apply_results(results, pool, output, expected_tasks=3)

    assert summary.missing == 3
    assert summary.removed == 0
    assert "A12345678@example.com" in pool.read_text(encoding="utf-8")


def test_api_password_mismatch_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "job" / "result.json"
    _write_result(path, success=True, email="A12345678@example.com")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["account"]["password"] = "different"
    path.write_text(json.dumps(payload), encoding="utf-8")

    summary = result_module.collect_results(tmp_path)

    assert not summary.accepted
    assert summary.ignored == 1


def test_success_without_confirmed_login_is_ignored(tmp_path: Path) -> None:
    _write_result(
        tmp_path / "job" / "result.json",
        success=True,
        email="A12345678@example.com",
        login_confirmed=False,
    )

    summary = result_module.collect_results(tmp_path)

    assert not summary.accepted
    assert summary.ignored == 1


def test_collect_accepts_login_confirmed_existing_account_recovery(tmp_path: Path) -> None:
    _write_result(
        tmp_path / "job" / "result.json",
        success=True,
        email="A12345678@example.com",
        code_submitted=False,
        success_banner=False,
        login_confirmed=True,
        recovered_existing=True,
    )

    summary = result_module.collect_results(tmp_path)

    assert [item.email for item in summary.accepted] == ["A12345678@example.com"]


def test_cli_writes_chinese_actions_summary(tmp_path: Path) -> None:
    results = tmp_path / "artifacts"
    pool = tmp_path / "email.txt"
    output = tmp_path / "注册成功账号.txt"
    summary_path = tmp_path / "summary.md"
    email = "A12345678@example.com"
    pool.write_text(_api(email, "1") + "\n", encoding="utf-8")
    output.write_text("", encoding="utf-8")
    _write_result(results / "job-1" / "result.json", success=True, email=email)

    exit_code = result_module.main(
        [
            "--结果目录",
            str(results),
            "--邮箱文件",
            str(pool),
            "--成功账号文件",
            str(output),
            "--摘要文件",
            str(summary_path),
            "--预期任务数量",
            "1",
        ]
    )

    text = summary_path.read_text(encoding="utf-8")
    assert exit_code == 0
    assert "GitHub V6 注册汇总" in text
    assert "成功账号" in text
    assert "剩余邮箱" in text


def test_expected_task_count_must_not_be_less_than_artifacts(tmp_path: Path) -> None:
    _write_result(
        tmp_path / "job" / "result.json",
        success=False,
        email="A12345678@example.com",
    )

    with pytest.raises(ValueError, match="预期任务数量"):
        result_module.collect_results(tmp_path, expected_tasks=0)
