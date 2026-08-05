from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "github-register-v6.yml"


def _workflow() -> tuple[str, dict[str, object]]:
    text = WORKFLOW.read_text(encoding="utf-8")
    data = yaml.load(text, Loader=yaml.BaseLoader)
    assert isinstance(data, dict)
    return text, data


def test_workflow_is_valid_chinese_manual_matrix() -> None:
    text, data = _workflow()

    assert data["name"] == "GitHub V6 多并发注册"
    assert "workflow_dispatch" in data["on"]
    assert set(data["jobs"]) == {"prepare", "register", "collect"}
    assert data["jobs"]["prepare"]["name"] == "准备任务矩阵"
    assert data["jobs"]["collect"]["name"] == "汇总并回写邮箱池"
    assert "任务数量（留空自动使用邮箱池，最多 256）" in text
    assert "最大并发数量（1-20）" in text


def test_matrix_limits_unique_allocation_and_three_attempts() -> None:
    text, data = _workflow()
    register = data["jobs"]["register"]

    assert register["strategy"]["fail-fast"] == "false"
    assert register["strategy"]["max-parallel"] == "${{ fromJSON(needs.prepare.outputs.max_parallel) }}"
    assert register["strategy"]["matrix"]["index"] == "${{ fromJSON(needs.prepare.outputs.indices) }}"
    assert "resolve_task_count" in text
    assert "resolve_max_parallel" in text
    assert "matrix_indices" in text
    assert '"--任务索引"' not in text
    assert "--任务索引 \"${{ matrix.index }}\"" in text
    assert "--最大尝试次数 3" in text
    assert "for attempt in 1 2 3" not in text


def test_workflow_is_direct_only_and_uses_current_official_actions() -> None:
    text, _data = _workflow()

    assert "proxy" not in text.casefold()
    assert "actions/checkout@v6" in text
    assert "actions/setup-python@v6" in text
    assert "actions/cache@v5" in text
    assert "actions/upload-artifact@v4" in text
    assert "actions/download-artifact@v5" in text
    assert "xvfb-run" in text


def test_only_collect_has_write_permission_and_runs_always() -> None:
    _text, data = _workflow()
    jobs = data["jobs"]

    assert data["permissions"]["contents"] == "read"
    assert "permissions" not in jobs["register"]
    assert jobs["collect"]["permissions"]["contents"] == "write"
    assert jobs["collect"]["if"] == "always()"


def test_branch_concurrency_and_conflict_recalculation_are_present() -> None:
    text, data = _workflow()

    assert data["concurrency"]["cancel-in-progress"] == "false"
    assert "github.repository" in data["concurrency"]["group"]
    assert "github.ref_name" in data["concurrency"]["group"]
    assert "for push_attempt in 1 2 3" in text
    assert 'git fetch origin "$branch"' in text
    assert 'git checkout -B "$branch" "origin/$branch"' in text
    assert "python github_result.py" in text
    assert 'git push origin "HEAD:$branch"' in text


def test_artifacts_are_always_uploaded_before_failure_is_reported() -> None:
    text, data = _workflow()
    execute_position = text.index("执行注册任务")
    upload_position = text.index("上传任务结果")
    fail_position = text.index("标记失败任务")

    assert execute_position < upload_position < fail_position
    assert "if: always()" in text
    assert "if-no-files-found: error" in text
    assert "GitHub-V6-注册结果-${{ matrix.index }}" in text
    download_step = next(
        step
        for step in data["jobs"]["collect"]["steps"]
        if step.get("uses") == "actions/download-artifact@v5"
    )
    assert download_step["with"].get("merge-multiple", "false") == "false"


def test_all_declared_step_names_are_chinese() -> None:
    _text, data = _workflow()
    for job in data["jobs"].values():
        for step in job.get("steps", []):
            name = step.get("name")
            if name:
                assert any("\u4e00" <= character <= "\u9fff" for character in name), name
