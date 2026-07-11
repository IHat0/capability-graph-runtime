import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cgr.swebench.qwen_action_contract import (
    extract_v1_1_thought_action,
    validate_qwen_action_contract,
)
from cgr.swebench.swe_agent_adapter import LOCAL_QWEN_OVERLAY


def test_overlay_has_complete_qwen_bash_contract() -> None:
    assert "Never emit more than one fenced block." in LOCAL_QWEN_OVERLAY
    assert "executed by Bash" in LOCAL_QWEN_OVERLAY
    assert "Never place raw Python source" in LOCAL_QWEN_OVERLAY
    assert "quoted heredoc" in LOCAL_QWEN_OVERLAY
    assert "inspect" in LOCAL_QWEN_OVERLAY
    assert "verify" in LOCAL_QWEN_OVERLAY
    assert "submit only after a successful diff" in LOCAL_QWEN_OVERLAY
    assert "history_processors: []" in LOCAL_QWEN_OVERLAY
    assert "edit_anthropic" not in LOCAL_QWEN_OVERLAY
    assert "function_calling" not in LOCAL_QWEN_OVERLAY


def test_official_sweagent_install_is_pinned_to_the_upstream_commit() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    smoke_script = Path("scripts/ec2_sweagent_smoke.sh").read_text(encoding="utf-8")
    commit = "0f3acafacabc0def8cc76b4e48acb4b6cf302cb9"

    assert f"SWE-agent.git@{commit}" in pyproject
    assert f"SWE-agent.git@{commit}" in smoke_script


@pytest.mark.parametrize(
    "response, expected",
    [
        ("DISCUSSION\nInspect source.\n```bash\nsed -n '1,20p' math_utils.py\n```", "sed -n '1,20p' math_utils.py"),
        ("DISCUSSION\nMake focused edit.\n```bash\nsed -i 's/return a - b/return a + b/' math_utils.py\n```", "sed -i 's/return a - b/return a + b/' math_utils.py"),
        ("DISCUSSION\nVerify behavior.\n```bash\npython -c \"from math_utils import add; assert add(2, 3) == 5\"\n```", "python -c \"from math_utils import add; assert add(2, 3) == 5\""),
        ("DISCUSSION\nSubmit focused diff.\n```bash\ngit add math_utils.py && git diff --cached\n```", "git add math_utils.py && git diff --cached"),
    ],
)
def test_strict_contract_accepts_valid_bash_actions(response: str, expected: str) -> None:
    assert validate_qwen_action_contract(response) == expected
    assert extract_v1_1_thought_action(response) == expected


def test_strict_contract_rejects_captured_and_synthetic_qwen_failures() -> None:
    fixture = Path("tests/fixtures/sweagent_qwen_action_failures.json")
    responses = json.loads(fixture.read_text(encoding="utf-8"))["responses"]

    for item in responses:
        with pytest.raises(ValueError):
            validate_qwen_action_contract(item["response"])

    assert extract_v1_1_thought_action(responses[0]["response"]).startswith("# test_edge_cases.py")
    assert extract_v1_1_thought_action(responses[1]["response"]).startswith("sed -i")
    assert extract_v1_1_thought_action(responses[2]["response"]).startswith("from math_utils")


def test_simulated_bash_trajectory_produces_focused_patch(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "math_utils.py").write_text("def add(a, b):\n    return a - b\n")
    for command in (["git", "init", "-q"], ["git", "add", "."], ["git", "commit", "-qm", "base"]):
        subprocess.run(command, cwd=workspace, check=True)
    python = sys.executable.replace("\\", "/")
    responses = [
        "DISCUSSION\nInspect source.\n```bash\nsed -n '1,20p' math_utils.py\n```",
        "DISCUSSION\nMake focused edit.\n```bash\nsed -i 's/return a - b/return a + b/' math_utils.py\n```",
        f"DISCUSSION\nVerify behavior.\n```bash\n'{python}' -c \"from math_utils import add; assert add(2, 3) == 5\"\n```",
        "DISCUSSION\nSubmit focused diff.\n```bash\ngit add math_utils.py && git diff --cached\n```",
    ]
    output = ""
    bash = shutil.which("bash") or str(Path("C:/Program Files/Git/bin/bash.exe"))
    if not Path(bash).is_file() and shutil.which(bash) is None:
        pytest.skip("Bash is required for the SWE-agent thought_action simulation.")
    for response in responses:
        action = validate_qwen_action_contract(response)
        result = subprocess.run(
            [bash, "-lc", action], cwd=workspace, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        output = result.stdout

    assert "return a + b" in (workspace / "math_utils.py").read_text()
    assert "diff --git a/math_utils.py b/math_utils.py" in output
