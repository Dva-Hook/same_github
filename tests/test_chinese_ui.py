from __future__ import annotations

import logging
from pathlib import Path

import github_register_job
import github_result


ROOT = Path(__file__).resolve().parents[1]


def test_python_cli_help_headers_are_chinese() -> None:
    for parser in (github_register_job.build_parser(), github_result.build_parser()):
        text = parser.format_help()
        assert "用法：" in text
        assert "选项：" in text
        assert "显示帮助并退出" in text
        assert "usage:" not in text
        assert "options:" not in text
        assert "show this help message and exit" not in text


def test_readme_and_data_files_are_present_and_chinese() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "GitHub V6 多并发注册" in readme
    assert "最多 256" in readme
    assert "最大并发 20" in readme
    assert "直连" in readme
    assert "自动提交" in readme
    assert (ROOT / "email.txt").read_bytes() == b""
    assert (ROOT / "注册成功账号.txt").read_bytes() == b""


def test_repository_contains_no_known_real_token_shape() -> None:
    excluded = {".git", ".venv", "__pycache__", ".pytest_cache"}
    bad: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in excluded for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if ("M." + "C550_") in text:
            bad.append(str(path.relative_to(ROOT)))
    assert not bad


def test_ignore_rules_cover_runtime_identity_and_failure_data() -> None:
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")

    for entry in (
        ".venv/",
        "__pycache__/",
        ".pytest_cache/",
        "artifacts/",
        "profiles/",
        "失败截图/",
        "*.log",
    ):
        assert entry in text


def test_job_logging_uses_chinese_level_names() -> None:
    original = {
        logging.INFO: logging.getLevelName(logging.INFO),
        logging.WARNING: logging.getLevelName(logging.WARNING),
        logging.ERROR: logging.getLevelName(logging.ERROR),
    }
    try:
        github_register_job._localize_logging_levels()
        assert logging.getLevelName(logging.INFO) == "信息"
        assert logging.getLevelName(logging.WARNING) == "警告"
        assert logging.getLevelName(logging.ERROR) == "错误"
    finally:
        for level, name in original.items():
            logging.addLevelName(level, name)
