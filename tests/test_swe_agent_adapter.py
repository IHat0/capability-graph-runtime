import json
import subprocess
from pathlib import Path

import pytest

from cgr.swebench import swe_agent_adapter as adapter


def _repo(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "math_utils.py").write_text("def add(a, b):\n    return a - b\n")
    for command in (["git", "init", "-q"], ["git", "add", "."], ["git", "commit", "-qm", "base"]):
        subprocess.run(command, cwd=workspace, check=True)
    return workspace


def test_official_command_uses_local_openai_compatible_configuration(tmp_path: Path) -> None:
    config = tmp_path / "source" / "config" / "default.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("agent: {}\n")
    command = adapter.build_sweagent_command(
        executable="sweagent",
        workspace=tmp_path / "repo",
        problem_file=tmp_path / "problem.txt",
        output_dir=tmp_path / "output",
        config_path=config,
        max_calls=5,
        max_steps=8,
        environment={
            "CGR_DRAFT_BASE_URL": "http://127.0.0.1:8000/v1",
            "CGR_DRAFT_MODEL": "Qwen/Qwen2.5-Coder-7B-Instruct",
            "CGR_DRAFT_API_KEY": "not-in-metadata",
            "CGR_DRAFT_MAX_MODEL_LEN": "16384",
        },
    )

    assert command[:4] == ["sweagent", "run", "--config", str(config.resolve())]
    assert "openai/Qwen/Qwen2.5-Coder-7B-Instruct" in command
    assert "thought_action" in command
    assert command[command.index("--problem_statement.data_path") + 1] == str(tmp_path / "problem.txt")
    assert "$CGR_DRAFT_API_KEY" not in command
    assert command[command.index("--agent.model.per_instance_call_limit") + 1] == "5"
    assert command[command.index("--agent.model.max_input_tokens") + 1] == "14336"


def test_adapter_applies_official_patch_and_reports_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = _repo(tmp_path)
    problem = tmp_path / "problem.txt"
    problem.write_text("Fix add.")
    monkeypatch.setenv("CGR_DRAFT_BASE_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("CGR_DRAFT_API_KEY", "secret")
    monkeypatch.setenv("CGR_DRAFT_MODEL", "Qwen/Qwen2.5-Coder-7B-Instruct")
    source = tmp_path / "source"
    (source / "config").mkdir(parents=True)
    (source / "config" / "default.yaml").write_text("agent: {}\n")
    monkeypatch.setenv("CGR_SWE_AGENT_SOURCE", str(source))
    monkeypatch.setattr(adapter.shutil, "which", lambda _value: "/fake/sweagent")

    def fake_run(
        command: list[str], *, workspace: Path, timeout: int, environment: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        output = Path(command[command.index("--output_dir") + 1])
        output.mkdir(parents=True, exist_ok=True)
        (output / "candidate.patch").write_text(
            "diff --git a/math_utils.py b/math_utils.py\n--- a/math_utils.py\n+++ b/math_utils.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n"
        )
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(adapter, "run_official_sweagent", fake_run)

    assert adapter.adapter_main([
        "--workspace", str(workspace), "--problem-file", str(problem), "--mode", "baseline",
        "--max-steps", "8", "--max-calls", "5",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["official_sweagent"]["tag"] == "v1.1.0"
    assert (workspace / "math_utils.py").read_text().endswith("return a + b\n")
    assert subprocess.run(["git", "diff", "--quiet"], cwd=workspace).returncode == 1


def test_adapter_rejects_empty_or_forbidden_official_patch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="produced no non-empty"):
        adapter.collect_official_patch(tmp_path)
    with pytest.raises(ValueError, match="forbidden"):
        adapter.apply_official_patch(tmp_path, "diff --git a/.git/config b/.git/config\n--- a/.git/config\n+++ b/.git/config\n")


def test_adapter_failure_redacts_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = _repo(tmp_path)
    problem = tmp_path / "problem.txt"
    problem.write_text("Fix add.")
    monkeypatch.setenv("CGR_DRAFT_API_KEY", "never-log-me")
    source = tmp_path / "source"
    (source / "config").mkdir(parents=True)
    (source / "config" / "default.yaml").write_text("agent: {}\n")
    monkeypatch.setenv("CGR_SWE_AGENT_SOURCE", str(source))
    monkeypatch.setattr(adapter.shutil, "which", lambda _value: None)

    assert adapter.adapter_main([
        "--workspace", str(workspace), "--problem-file", str(problem), "--mode", "baseline",
        "--max-steps", "8", "--max-calls", "5",
    ]) == 1
    assert "never-log-me" not in capsys.readouterr().out


def test_official_failure_preserves_bounded_redacted_process_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = _repo(tmp_path)
    problem = tmp_path / "problem.txt"
    problem.write_text("Fix add.")
    source = tmp_path / "source"
    (source / "config").mkdir(parents=True)
    (source / "config" / "default.yaml").write_text("agent: {}\n")
    monkeypatch.setenv("CGR_SWE_AGENT_SOURCE", str(source))
    monkeypatch.setenv("CGR_DRAFT_BASE_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("CGR_DRAFT_API_KEY", "hide-this-key")
    monkeypatch.setenv("CGR_DRAFT_MODEL", "qwen")
    monkeypatch.setattr(adapter.shutil, "which", lambda _value: "/fake/sweagent")
    monkeypatch.setattr(
        adapter,
        "run_official_sweagent",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 1, "provider saw hide-this-key", "failed: hide-this-key"
        ),
    )

    assert adapter.adapter_main([
        "--workspace", str(workspace), "--problem-file", str(problem), "--mode", "baseline",
        "--max-steps", "8", "--max-calls", "5",
    ]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["exit_code"] == 1
    assert payload["stdout"] == "provider saw [REDACTED]"
    assert payload["stderr"] == "failed: [REDACTED]"
    assert "hide-this-key" not in json.dumps(payload)


def test_ec2_smoke_preflight_is_recorded_without_blocking_adapter() -> None:
    script = Path("scripts/ec2_sweagent_smoke.sh").read_text(encoding="utf-8")

    assert '"$CGR_SWE_AGENT_EXECUTABLE" run --help >"$preflight_stdout"' in script
    assert "preflight_status=$?" in script
    assert "adapter_status=$?" in script
    assert "final_status=0" in script
    assert "=== SWE-agent preflight ===" in script
    assert "=== Adapter ===" in script
    assert "=== Final workspace ===" in script
    assert "=== Trajectories/artifacts ===" in script
