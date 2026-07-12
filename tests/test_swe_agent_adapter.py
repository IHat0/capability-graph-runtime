import json
import os
import shutil
import stat
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


def _git(workspace: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(workspace), *arguments],
        capture_output=True,
        text=True,
        check=check,
    )


def _run_post_startup_commands(workspace: Path) -> int | None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Bash is required to exercise SWE-agent startup commands.")
    for index, command in enumerate(adapter.POST_STARTUP_COMMANDS):
        if subprocess.run([bash, "-c", command], cwd=workspace, check=False).returncode:
            return index
    return None


def _mode_contaminated_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    image = workspace / "asset.png"
    source = workspace / "module.py"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    source.write_text("VALUE = 1\n", encoding="utf-8")
    for path in (image, source):
        os.chmod(path, path.stat().st_mode | stat.S_IXUSR)
    _git(workspace, "init", "-q")
    _git(workspace, "config", "user.email", "test@example.com")
    _git(workspace, "config", "user.name", "Test")
    _git(workspace, "add", "asset.png", "module.py")
    _git(workspace, "commit", "-qm", "base")
    for path in (image, source):
        os.chmod(path, path.stat().st_mode & ~stat.S_IXUSR)
    return workspace, image, source


def test_official_command_uses_local_openai_compatible_configuration(tmp_path: Path) -> None:
    config = tmp_path / "source" / "config" / "default.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("agent: {}\n")
    override = adapter.write_local_model_override(tmp_path / "output")
    command = adapter.build_sweagent_command(
        executable="sweagent",
        workspace=tmp_path / "repo",
        problem_file=tmp_path / "problem.txt",
        output_dir=tmp_path / "output",
        config_path=config,
        local_override_path=override,
        max_calls=5,
        max_steps=8,
        environment={
            "CGR_DRAFT_BASE_URL": "http://127.0.0.1:8000/v1",
            "CGR_DRAFT_MODEL": "Qwen/Qwen2.5-Coder-7B-Instruct",
            "CGR_DRAFT_API_KEY": "not-in-metadata",
            "CGR_DRAFT_MAX_MODEL_LEN": "16384",
        },
    )

    assert command[:6] == [
        "sweagent", "run", "--config", str(config.resolve()), "--config", str(override.resolve())
    ]
    assert "openai/Qwen/Qwen2.5-Coder-7B-Instruct" in command
    assert "--agent.tools.parse_function.type" not in command
    assert command[command.index("--problem_statement.path") + 1] == str(tmp_path / "problem.txt")
    assert "--problem_statement.data_path" not in command
    assert "--agent.history_processors" not in command
    assert "[]" not in command
    assert "$CGR_DRAFT_API_KEY" not in command
    assert command[command.index("--agent.model.per_instance_call_limit") + 1] == "5"
    assert command[command.index("--agent.model.max_input_tokens") + 1] == "14336"
    assert override.is_absolute()
    assert override.is_file()
    overlay = override.read_text(encoding="utf-8")
    assert "history_processors: []" in overlay
    assert "post_startup_commands:" in overlay
    for command in adapter.POST_STARTUP_COMMANDS:
        assert json.dumps(command) in overlay
    assert "Every response MUST contain exactly this structure" in overlay
    assert "Never emit more than one fenced block." in overlay
    assert "executed by Bash" in overlay
    assert "type: strict_thought_action" in overlay
    assert "edit_anthropic" not in overlay
    assert "function_calling" not in overlay
    assert "cache_control" not in overlay
    assert "/repo" not in overlay
    assert "/astropy" not in overlay


def test_post_startup_file_mode_normalization_preserves_real_content_changes(
    tmp_path: Path,
) -> None:
    workspace, image, source = _mode_contaminated_repo(tmp_path)
    executable_entries = _git(workspace, "ls-tree", "-r", "HEAD").stdout
    expected_entries = ("100755 blob", "asset.png", "module.py")
    if not all(entry in executable_entries for entry in expected_entries):
        pytest.skip("The current filesystem does not expose executable-bit changes to Git.")

    _git(workspace, "config", "core.fileMode", "true")
    if _git(workspace, "diff", "--quiet", check=False).returncode == 0:
        pytest.skip("The current Git platform does not report transferred file-mode changes.")
    before = _git(workspace, "diff", "--summary")
    assert "mode change 100755 => 100644 asset.png" in before.stdout
    assert "mode change 100755 => 100644 module.py" in before.stdout

    assert _run_post_startup_commands(workspace) is None
    assert _git(workspace, "diff", "--quiet", "--ignore-submodules", "--", check=False).returncode == 0
    assert _git(workspace, "diff", "--cached", "--quiet", "--ignore-submodules", "--", check=False).returncode == 0

    source.write_text("VALUE = 2\n", encoding="utf-8")
    assert _git(workspace, "diff", "--quiet", "--ignore-submodules", "--", check=False).returncode == 1
    assert _run_post_startup_commands(workspace) == 1
    _git(workspace, "restore", "module.py")

    source.write_text("VALUE = 3\n", encoding="utf-8")
    _git(workspace, "add", "module.py")
    assert _git(workspace, "diff", "--cached", "--quiet", "--ignore-submodules", "--", check=False).returncode == 1
    assert _run_post_startup_commands(workspace) == 2
    assert image.read_bytes().startswith(b"\x89PNG")


def test_post_startup_clean_gate_blocks_first_model_response(tmp_path: Path) -> None:
    workspace = _repo(tmp_path)
    (workspace / "math_utils.py").write_text("def add(a, b):\n    return a + b\n")

    model_responses: list[str] = []
    if _run_post_startup_commands(workspace) is None:
        model_responses.append("first model response")

    assert model_responses == []


def test_post_startup_hook_derives_arbitrary_deployed_repository_path(tmp_path: Path) -> None:
    workspace = tmp_path / "randomized" / "workspace" / "project-4f19"
    workspace.mkdir(parents=True)
    (workspace / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(workspace, "init", "-q")
    _git(workspace, "config", "user.email", "test@example.com")
    _git(workspace, "config", "user.name", "Test")
    _git(workspace, "add", "module.py")
    _git(workspace, "commit", "-qm", "base")

    assert _run_post_startup_commands(workspace) is None
    commands = "\n".join(adapter.POST_STARTUP_COMMANDS)
    assert "/repo" not in commands
    assert "/workspace/project" not in commands
    assert "/astropy" not in commands


def test_post_startup_hook_fails_clearly_outside_deployed_repository(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Bash is required to exercise SWE-agent startup commands.")

    result = subprocess.run(
        [bash, "-c", adapter.POST_STARTUP_COMMANDS[0]],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Unable to identify the deployed Git repository." in result.stderr


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
    (source / "sweagent").mkdir()
    (source / "sweagent" / "__init__.py").write_text("")
    (source / "tools").mkdir()
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
    assert (tmp_path / ".cgr-sweagent-trajectories" / "cgr-local-qwen.yaml").is_file()


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
    (source / "sweagent").mkdir()
    (source / "sweagent" / "__init__.py").write_text("")
    (source / "tools").mkdir()
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
    (source / "sweagent").mkdir()
    (source / "sweagent" / "__init__.py").write_text("")
    (source / "tools").mkdir()
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


def test_pinned_source_validation_rejects_site_packages_only_install(tmp_path: Path) -> None:
    source = tmp_path / ".swe-agent-src"
    source.mkdir()
    assert adapter._is_valid_sweagent_source(source) is False

    (source / "sweagent").mkdir()
    (source / "sweagent" / "__init__.py").write_text("", encoding="utf-8")
    (source / "config").mkdir()
    (source / "config" / "default.yaml").write_text("agent: {}\n", encoding="utf-8")
    (source / "tools").mkdir()

    assert adapter._is_valid_sweagent_source(source) is True
