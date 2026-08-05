from __future__ import annotations

from pathlib import Path

import pytest

from github_email_pool import (
    load_email_pool,
    matrix_indices,
    parse_credential_line,
    remove_consumed_emails,
    resolve_max_parallel,
    resolve_task_count,
    select_email_credential,
)


def _line(email: str, suffix: str) -> str:
    return f"{email}----password-{suffix}----client-{suffix}----refresh-{suffix}"


def test_parse_credential_line_normalizes_fields_and_redacts_secrets() -> None:
    item = parse_credential_line(
        "  CarlyJohnston@example.com ---- pass ---- client ---- refresh  ",
        source_index=7,
    )

    assert item.email == "CarlyJohnston@example.com"
    assert item.mailbox_password == "pass"
    assert item.client_id == "client"
    assert item.refresh_token == "refresh"
    assert item.raw_line == "CarlyJohnston@example.com----pass----client----refresh"
    assert item.source_index == 7
    assert "pass" not in repr(item)
    assert "client" not in repr(item)
    assert "refresh" not in repr(item)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "missing@example.com----password----client",
        "bad-email----password----client----refresh",
        "blank@example.com--------client----refresh",
    ],
)
def test_parse_rejects_invalid_records(raw: str) -> None:
    with pytest.raises(ValueError, match="第 3 行"):
        parse_credential_line(raw, source_index=3)


def test_load_rejects_duplicate_email_case_insensitively(tmp_path: Path) -> None:
    pool = tmp_path / "email.txt"
    pool.write_text(
        _line("FirstPerson@example.com", "1")
        + "\n"
        + _line("firstperson@EXAMPLE.COM", "2")
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="重复邮箱"):
        load_email_pool(pool)


def test_matrix_indices_are_unique_and_one_based(tmp_path: Path) -> None:
    pool = tmp_path / "email.txt"
    pool.write_text(
        _line("A12345678@example.com", "1")
        + "\n"
        + _line("B12345678@example.com", "2")
        + "\n",
        encoding="utf-8",
    )

    assert select_email_credential(pool, 1).email == "A12345678@example.com"
    assert select_email_credential(pool, 2).email == "B12345678@example.com"
    assert matrix_indices(2) == [1, 2]
    with pytest.raises(ValueError, match="从 1 开始"):
        select_email_credential(pool, 0)
    with pytest.raises(IndexError, match="无法分配"):
        select_email_credential(pool, 3)


def test_task_and_parallel_limits_are_hard_bounded(tmp_path: Path) -> None:
    pool = tmp_path / "email.txt"
    pool.write_text(
        "\n".join(_line(f"Person{index:03d}@example.com", str(index)) for index in range(1, 258))
        + "\n",
        encoding="utf-8",
    )

    assert resolve_task_count(pool, "") == 256
    assert resolve_task_count(pool, "12") == 12
    assert resolve_max_parallel("20", 256) == 20
    assert resolve_max_parallel("20", 3) == 3
    with pytest.raises(ValueError, match="1 到 256"):
        resolve_task_count(pool, "257")
    with pytest.raises(ValueError, match="1 到 20"):
        resolve_max_parallel("21", 10)


def test_requested_tasks_cannot_exceed_pool_capacity(tmp_path: Path) -> None:
    pool = tmp_path / "email.txt"
    pool.write_text(_line("OnlyPerson@example.com", "1") + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="只有 1 个"):
        resolve_task_count(pool, "2")


def test_remove_successes_is_case_insensitive_atomic_and_idempotent(tmp_path: Path) -> None:
    pool = tmp_path / "email.txt"
    first = _line("A12345678@example.com", "1")
    second = _line("B12345678@example.com", "2")
    pool.write_text(first + "\n" + second + "\n", encoding="utf-8")

    result = remove_consumed_emails(pool, {"a12345678@EXAMPLE.COM"})
    repeated = remove_consumed_emails(pool, {"a12345678@example.com"})

    assert result.requested == 1
    assert result.removed == 1
    assert result.removed_emails == ("A12345678@example.com",)
    assert result.remaining == 1
    assert repeated.removed == 0
    assert pool.read_text(encoding="utf-8") == second + "\n"
    assert not pool.with_name("email.txt.tmp").exists()

