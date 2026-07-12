from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

from cgr.swebench import sandbox_full_cycle as sandbox
from cgr.swebench import swe_agent_adapter as adapter


def test_sandbox_overlay_uses_pristine_thought_action_without_action_interception() -> None:
    overlay = sandbox._sandbox_overlay()

    assert "type: thought_action" in overlay
    assert "strict_thought_action" not in overlay
    assert "CGR_ACTION_VALIDATOR" not in overlay
    assert "bundles: []" in overlay


def test_sandbox_repository_is_real_clean_git_repo_with_failing_verifier(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    sandbox._prepare_repository(workspace)

    assert sandbox._git(workspace, "status", "--porcelain=v1").stdout == ""
    verifier = subprocess.run(
        [sys.executable, "-m", "unittest", "-q"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    assert verifier.returncode == 1


def test_deterministic_server_receives_real_openai_messages(tmp_path: Path) -> None:
    log = tmp_path / "interactions.jsonl"
    server = sandbox._model_server(log, "/tmp/model.patch")
    thread = sandbox.threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
            data=json.dumps(
                {"model": sandbox.SANDBOX_MODEL, "messages": [{"role": "user", "content": "task"}]}
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.load(response)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    assert payload["choices"][0]["message"]["content"].startswith("DISCUSSION")
    interaction = json.loads(log.read_text(encoding="utf-8"))
    assert interaction["messages"] == [{"role": "user", "content": "task"}]


def test_adapter_local_deployment_uses_preexisting_shared_repository(tmp_path: Path) -> None:
    config = tmp_path / "default.yaml"
    override = tmp_path / "override.yaml"
    config.write_text("agent: {}\n", encoding="utf-8")
    override.write_text("agent: {}\n", encoding="utf-8")
    command = adapter.build_sweagent_command(
        executable="sweagent",
        workspace=tmp_path,
        problem_file=tmp_path / "problem.md",
        output_dir=tmp_path / "output",
        config_path=config,
        local_override_path=override,
        max_calls=4,
        max_steps=4,
        environment={
            "CGR_DRAFT_BASE_URL": "http://127.0.0.1:8000/v1",
            "CGR_DRAFT_API_KEY": "key",
            "CGR_DRAFT_MODEL": "model",
            "CGR_DRAFT_MAX_MODEL_LEN": "4096",
        },
        deployment_type="local",
        deployed_repo_name="c/sandbox/workspace",
    )

    assert command[command.index("--env.deployment.type") + 1] == "local"
    assert command[command.index("--env.repo.type") + 1] == "preexisting"
    assert command[command.index("--env.repo.repo_name") + 1] == "c/sandbox/workspace"
    assert "--env.deployment.image" not in command
    assert "--env.repo.path" not in command


def test_shared_workspace_patch_must_equal_real_git_diff(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    sandbox._prepare_repository(workspace)
    (workspace / "math_utils.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )
    patch = sandbox._git(workspace, "diff", "--binary", "HEAD", "--").stdout

    adapter._verify_shared_workspace_patch(workspace, patch)
