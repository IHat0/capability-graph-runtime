import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cgr.swebench import native_pilot as native
from cgr.swebench.integration import PilotInstance


INSTANCE = PilotInstance(
    instance_id="owner__repo-123",
    repo="owner/repo",
    base_commit="1" * 40,
)


def _source(tmp_path: Path) -> Path:
    source = tmp_path / ".swe-agent-src"
    (source / "config").mkdir(parents=True)
    (source / "config" / "default.yaml").write_text("agent: {}\n", encoding="utf-8")
    (source / "sweagent" / "tools").mkdir(parents=True)
    (source / "sweagent" / "__init__.py").write_text("", encoding="utf-8")
    (source / "sweagent" / "tools" / "parsing.py").write_text(
        'class StrictThoughtActionParser:\n    pass\n\ntype = "strict_thought_action"\n',
        encoding="utf-8",
    )
    return source


def _endpoint(mode: str = "baseline", key: str = "secret-key") -> native.ModelEndpoint:
    return native.ModelEndpoint(  # type: ignore[arg-type]
        mode=mode,
        base_url="http://127.0.0.1:8000/v1" if mode == "baseline" else "http://127.0.0.1:9000/v1",
        api_key=key,
        model="Qwen/Qwen2.5-Coder-7B-Instruct" if mode == "baseline" else "cgr-runtime",
    )


def _identity(source: Path) -> dict[str, str]:
    return {
        "upstream_commit": native.SWE_AGENT_COMMIT,
        "parser_patch_sha256": native.SWE_AGENT_PATCH_SHA256,
        "imported_sweagent_path": str(source / "sweagent" / "__init__.py"),
    }


def _evaluator(tmp_path: Path) -> native.EvaluatorRuntime:
    python = tmp_path / "evaluator-python"
    python.write_text("", encoding="utf-8")
    return native.EvaluatorRuntime(
        python=str(python.resolve()),
        version=native.SWEBENCH_EVALUATOR_VERSION,
        package_path=str(tmp_path / "site-packages" / "swebench" / "__init__.py"),
        harness_path=str(tmp_path / "site-packages" / "swebench" / "harness" / "__init__.py"),
    )


def _fake_sweagent(
    monkeypatch: pytest.MonkeyPatch,
    *,
    model_patch: str | None = "diff --git a/x b/x\n",
    instance_id: str = INSTANCE.instance_id,
    returncode: int = 0,
) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        output = Path(command[command.index("--output_dir") + 1])
        assert output.is_dir()
        if returncode == 0:
            instance_root = output / INSTANCE.instance_id
            instance_root.mkdir()
            (instance_root / f"{INSTANCE.instance_id}.pred").write_text(
                json.dumps(
                    {
                        "model_name_or_path": "official-sweagent",
                        "instance_id": instance_id,
                        "model_patch": model_patch,
                    }
                ),
                encoding="utf-8",
            )
            (instance_root / f"{INSTANCE.instance_id}.traj").write_text(
                "trajectory", encoding="utf-8"
            )
            (instance_root / f"{INSTANCE.instance_id}.info.log").write_text(
                "secret-key", encoding="utf-8"
            )
        return subprocess.CompletedProcess(command, returncode, "stdout secret-key", "stderr secret-key")

    monkeypatch.setattr(native.subprocess, "run", run)
    monkeypatch.setattr(native, "_check_endpoint", lambda _endpoint: None)
    return calls


def _generate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    model_patch: str | None = "diff --git a/x b/x\n",
    instance_id: str = INSTANCE.instance_id,
    returncode: int = 0,
) -> tuple[dict[str, object], list[list[str]]]:
    source = _source(tmp_path)
    calls = _fake_sweagent(
        monkeypatch,
        model_patch=model_patch,
        instance_id=instance_id,
        returncode=returncode,
    )
    result = native.generate_attempt(
        result_root=tmp_path / "results",
        mode="baseline",
        instance=INSTANCE,
        problem_statement="Fix the frozen issue.",
        endpoint=_endpoint(),
        source=source,
        executable="sweagent",
        manifest_hash="manifest-hash",
        dataset_fingerprint="dataset-fingerprint",
        source_identity=_identity(source),
    )
    return result, calls


def test_native_generation_invokes_official_sweagent_directly_without_adapter_or_git_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, calls = _generate(tmp_path, monkeypatch)

    assert result["infrastructure_status"] == "completed"
    assert calls and calls[0][:2] == ["sweagent", "run"]
    flattened = " ".join(calls[0])
    assert "cgr-swebench-swe-agent-adapter" not in flattened
    assert "git apply" not in Path(native.__file__).read_text(encoding="utf-8")
    assert "TemporaryDirectory" not in Path(native.__file__).read_text(encoding="utf-8")


def test_native_modes_differ_only_by_model_endpoint_and_identifier(tmp_path: Path) -> None:
    problem = tmp_path / "problem.md"
    problem.write_text("issue", encoding="utf-8")
    baseline = native.render_native_overlay(INSTANCE, _endpoint("baseline"), problem)
    cgr = native.render_native_overlay(INSTANCE, _endpoint("cgr"), problem)
    normalized_baseline = baseline.replace(
        json.dumps(_endpoint("baseline").sweagent_model), '"MODEL"'
    ).replace(json.dumps(_endpoint("baseline").base_url), '"ENDPOINT"')
    normalized_cgr = cgr.replace(json.dumps(_endpoint("cgr").sweagent_model), '"MODEL"').replace(
        json.dumps(_endpoint("cgr").base_url), '"ENDPOINT"'
    )

    assert normalized_baseline == normalized_cgr
    assert "type: strict_thought_action" in baseline
    assert "tools/registry" in baseline
    assert baseline.index("tools/registry") < baseline.index("tools/review_on_submit_m")
    assert "git -C /repo config core.fileMode false" in baseline
    assert "$CGR_NATIVE_API_KEY" in baseline
    assert "secret-key" not in baseline


def test_comparison_rejects_identical_endpoint_identity() -> None:
    endpoint = _endpoint()
    with pytest.raises(ValueError, match="same identity"):
        native._verify_distinct_comparison_endpoints(endpoint, endpoint)


def test_pinned_sweagent_verification_checks_commit_patch_and_import_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    patch = tmp_path / "strict.patch"
    patch.write_text("patch", encoding="utf-8")
    patch_hash = hashlib.sha256(patch.read_bytes()).hexdigest()
    executable = tmp_path / "venv" / "bin" / "sweagent"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    python = executable.parent / "python"
    python.write_text("", encoding="utf-8")
    monkeypatch.setattr(native, "SWE_AGENT_PATCH", patch)
    monkeypatch.setattr(native, "SWE_AGENT_PATCH_SHA256", patch_hash)
    outputs = iter([native.SWE_AGENT_COMMIT + "\n", str(source / "sweagent" / "__init__.py") + "\n"])
    monkeypatch.setattr(
        native,
        "_run_checked",
        lambda _command: subprocess.CompletedProcess([], 0, next(outputs), ""),
    )
    monkeypatch.setattr(
        native.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", ""),
    )

    identity = native.verify_pinned_sweagent(source, str(executable))

    assert identity["upstream_commit"] == native.SWE_AGENT_COMMIT
    assert identity["parser_patch_sha256"] == patch_hash
    assert Path(identity["imported_sweagent_path"]).is_relative_to(source)


def test_pinned_sweagent_verification_rejects_wrong_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    patch = tmp_path / "strict.patch"
    patch.write_text("patch", encoding="utf-8")
    monkeypatch.setattr(native, "SWE_AGENT_PATCH", patch)
    monkeypatch.setattr(native, "SWE_AGENT_PATCH_SHA256", hashlib.sha256(b"patch").hexdigest())
    monkeypatch.setattr(
        native,
        "_run_checked",
        lambda _command: subprocess.CompletedProcess([], 0, "wrong-commit\n", ""),
    )

    with pytest.raises(ValueError, match="source commit"):
        native.verify_pinned_sweagent(source, "sweagent")


def test_pinned_sweagent_verification_rejects_import_outside_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    patch = tmp_path / "strict.patch"
    patch.write_text("patch", encoding="utf-8")
    monkeypatch.setattr(native, "SWE_AGENT_PATCH", patch)
    monkeypatch.setattr(native, "SWE_AGENT_PATCH_SHA256", hashlib.sha256(b"patch").hexdigest())
    outputs = iter([native.SWE_AGENT_COMMIT + "\n", str(tmp_path / "site-packages/sweagent.py")])
    monkeypatch.setattr(
        native,
        "_run_checked",
        lambda _command: subprocess.CompletedProcess([], 0, next(outputs), ""),
    )
    monkeypatch.setattr(
        native.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", ""),
    )
    outside = tmp_path / "site-packages" / "sweagent.py"
    outside.parent.mkdir()
    outside.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="outside .swe-agent-src"):
        native.verify_pinned_sweagent(source, "sweagent")


def test_successful_official_prediction_is_retained_and_hashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, _ = _generate(tmp_path, monkeypatch)
    attempt = Path(str(result["artifact_directory"]))
    prediction = Path(str(result["prediction_path"]))

    assert prediction == attempt / INSTANCE.instance_id / f"{INSTANCE.instance_id}.pred"
    assert result["prediction_status"] == "patch_submitted"
    assert result["prediction_sha256"] == hashlib.sha256(prediction.read_bytes()).hexdigest()
    hashes = json.loads((attempt / "artifact-sha256.json").read_text(encoding="utf-8"))
    assert hashes[f"{INSTANCE.instance_id}/{INSTANCE.instance_id}.pred"] == result["prediction_sha256"]
    assert native._hash_artifacts(attempt) == hashes


def test_null_model_patch_is_completed_unresolved_prediction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, _ = _generate(tmp_path, monkeypatch, model_patch=None)

    assert result["infrastructure_status"] == "completed"
    assert result["prediction_status"] == "unresolved"
    assert result["generation_exit_code"] == 0


def test_process_crash_without_prediction_is_infrastructure_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, _ = _generate(tmp_path, monkeypatch, returncode=1)

    assert result["infrastructure_status"] == "failed"
    assert result["prediction_path"] is None
    assert "exited with code 1" in str(result["generation_error"])


def test_normal_process_exit_without_prediction_is_infrastructure_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    monkeypatch.setattr(native, "_check_endpoint", lambda _endpoint: None)
    monkeypatch.setattr(
        native.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "", ""),
    )

    result = native.generate_attempt(
        result_root=tmp_path / "results",
        mode="baseline",
        instance=INSTANCE,
        problem_statement="Issue",
        endpoint=_endpoint(),
        source=source,
        executable="sweagent",
        manifest_hash="manifest-hash",
        dataset_fingerprint="dataset-fingerprint",
        source_identity=_identity(source),
    )

    assert result["infrastructure_status"] == "failed"
    assert "no .pred artifact" in str(result["generation_error"])


def test_mismatched_official_prediction_instance_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, _ = _generate(tmp_path, monkeypatch, instance_id="wrong__instance-1")

    assert result["infrastructure_status"] == "failed"
    assert "instance ID differs" in str(result["generation_error"])


def test_generation_artifacts_redact_api_key_everywhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, calls = _generate(tmp_path, monkeypatch)
    attempt = Path(str(result["artifact_directory"]))

    assert "secret-key" not in " ".join(calls[0])
    for path in attempt.rglob("*"):
        if path.is_file():
            assert b"secret-key" not in path.read_bytes(), path
    assert "[REDACTED]" in (attempt / "sweagent.stdout.log").read_text(encoding="utf-8")


def test_failure_traceback_and_result_redact_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    monkeypatch.setattr(
        native,
        "_check_endpoint",
        lambda _endpoint: (_ for _ in ()).throw(RuntimeError("secret-key unavailable")),
    )
    result = native.generate_attempt(
        result_root=tmp_path / "results",
        mode="baseline",
        instance=INSTANCE,
        problem_statement="Issue",
        endpoint=_endpoint(),
        source=source,
        executable="sweagent",
        manifest_hash="manifest-hash",
        dataset_fingerprint="dataset-fingerprint",
        source_identity=_identity(source),
    )
    attempt = Path(str(result["artifact_directory"]))

    assert "secret-key" not in json.dumps(result)
    assert "secret-key" not in (attempt / "failure-traceback.txt").read_text(encoding="utf-8")


def _completed_attempt(tmp_path: Path) -> tuple[Path, Path]:
    attempt = tmp_path / "attempt-001"
    prediction = attempt / INSTANCE.instance_id / f"{INSTANCE.instance_id}.pred"
    prediction.parent.mkdir(parents=True)
    prediction.write_text(
        json.dumps(
            {
                "model_name_or_path": "official",
                "instance_id": INSTANCE.instance_id,
                "model_patch": None,
            }
        ),
        encoding="utf-8",
    )
    digest = hashlib.sha256(prediction.read_bytes()).hexdigest()
    (attempt / "generation-result.json").write_text(
        json.dumps(
            {
                "mode": "baseline",
                "instance_id": INSTANCE.instance_id,
                "repository": INSTANCE.repo,
                "base_commit": INSTANCE.base_commit,
                "dataset_name": native.DATASET_NAME,
                "manifest_hash": "manifest-hash",
                "infrastructure_status": "completed",
                "prediction_path": str(prediction.resolve()),
                "prediction_sha256": digest,
            }
        ),
        encoding="utf-8",
    )
    return attempt, prediction


def test_evaluator_receives_exact_official_prediction_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt, prediction = _completed_attempt(tmp_path)
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        cwd = Path(str(kwargs["cwd"]))
        run_id = command[command.index("--run_id") + 1]
        report = cwd / f"gold.{run_id}.json"
        report.write_text(json.dumps({"resolved_ids": [INSTANCE.instance_id]}), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, f"Report written to {report.name}\n", "")

    monkeypatch.setattr(native.subprocess, "run", run)
    result = native.evaluate_attempt(
        attempt,
        instance=INSTANCE,
        manifest_hash="manifest-hash",
        evaluator=_evaluator(tmp_path),
    )

    evaluator_input = Path(calls[0][calls[0].index("--predictions_path") + 1])
    assert evaluator_input.suffix == ".json"
    assert evaluator_input.samefile(prediction)
    assert calls[0][0] == _evaluator(tmp_path).python
    assert result["resolved"] is True
    assert result["infrastructure_status"] == "completed"
    assert Path(str(result["official_report_path"])).is_file()


def test_evaluate_refuses_modified_prediction_without_invoking_evaluator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt, prediction = _completed_attempt(tmp_path)
    prediction.write_text("modified", encoding="utf-8")
    monkeypatch.setattr(
        native.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("evaluator must not run"),
    )

    with pytest.raises(ValueError, match="changed after generation"):
        native.evaluate_attempt(
            attempt,
            instance=INSTANCE,
            manifest_hash="manifest-hash",
            evaluator=_evaluator(tmp_path),
        )


def test_evaluate_only_never_invokes_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = SimpleNamespace(
        instances=[INSTANCE], selected_ids_sha256="manifest-hash", dataset_fingerprint="fingerprint"
    )
    monkeypatch.setattr(native, "load_manifest", lambda _path: manifest)
    monkeypatch.setattr(native, "verify_evaluator_runtime", lambda: _evaluator(tmp_path))
    monkeypatch.setattr(native, "_latest_completed_attempt", lambda *args: tmp_path / "attempt")
    monkeypatch.setattr(
        native,
        "evaluate_attempt",
        lambda *args, **kwargs: {"exit_code": 0, "resolved": False},
    )
    monkeypatch.setattr(
        native,
        "generate_attempt",
        lambda *args, **kwargs: pytest.fail("generation must not run"),
    )

    result = native.native_pilot_main(
        [
            "--mode",
            "baseline",
            "--evaluate-only",
            "--instance-id",
            INSTANCE.instance_id,
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--result-root",
            str(tmp_path / "results"),
        ]
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out)["runs"][0]["resolved"] is False


def test_missing_evaluator_python_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CGR_SWEBENCH_EVALUATOR_PYTHON", raising=False)

    with pytest.raises(ValueError, match="CGR_SWEBENCH_EVALUATOR_PYTHON is required"):
        native.verify_evaluator_runtime()


def test_evaluator_python_that_cannot_import_swebench_fails_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python = tmp_path / "python"
    python.write_text("", encoding="utf-8")
    monkeypatch.setenv("CGR_SWEBENCH_EVALUATOR_PYTHON", str(python))
    monkeypatch.setattr(native.os, "access", lambda *_args: True)
    monkeypatch.setattr(
        native.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, "", "ModuleNotFoundError: No module named 'swebench'"
        ),
    )

    with pytest.raises(ValueError, match="cannot import swebench"):
        native.verify_evaluator_runtime()


def test_evaluator_version_must_match_frozen_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python = tmp_path / "python"
    python.write_text("", encoding="utf-8")
    monkeypatch.setenv("CGR_SWEBENCH_EVALUATOR_PYTHON", str(python))
    monkeypatch.setattr(native.os, "access", lambda *_args: True)
    monkeypatch.setattr(
        native.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {
                    "version": "4.1.0",
                    "package_path": "/venv/swebench/__init__.py",
                    "harness_path": "/venv/swebench/harness/__init__.py",
                }
            ),
            "",
        ),
    )

    with pytest.raises(ValueError, match="expected 3.0.17, found 4.1.0"):
        native.verify_evaluator_runtime()


def _mock_native_main_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = SimpleNamespace(
        instances=[INSTANCE], selected_ids_sha256="manifest-hash", dataset_fingerprint="fingerprint"
    )
    source = _source(tmp_path)
    record = {
        "instance_id": INSTANCE.instance_id,
        "repo": INSTANCE.repo,
        "base_commit": INSTANCE.base_commit,
        "problem_statement": "Issue",
    }
    monkeypatch.setattr(native, "load_manifest", lambda _path: manifest)
    monkeypatch.setattr(native, "load_verified_records", lambda: ([record], "fingerprint"))
    monkeypatch.setattr(native, "_sweagent_source", lambda _configured: source)
    monkeypatch.setattr(native, "_sweagent_executable", lambda _configured: "official-sweagent")
    monkeypatch.setattr(native, "verify_pinned_sweagent", lambda *_args: _identity(source))
    monkeypatch.setenv("CGR_DRAFT_BASE_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("CGR_DRAFT_API_KEY", "key")
    monkeypatch.setenv("CGR_DRAFT_MODEL", "Qwen/Qwen2.5-Coder-7B-Instruct")
    monkeypatch.setenv("CGR_DRAFT_MAX_MODEL_LEN", "16384")


def test_generate_only_never_requires_or_imports_swebench_evaluator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _mock_native_main_generation(tmp_path, monkeypatch)
    monkeypatch.delenv("CGR_SWEBENCH_EVALUATOR_PYTHON", raising=False)
    monkeypatch.setattr(
        native,
        "verify_evaluator_runtime",
        lambda: pytest.fail("generate-only must not inspect evaluator runtime"),
    )
    monkeypatch.setattr(
        native,
        "generate_attempt",
        lambda **kwargs: {
            "infrastructure_status": "completed",
            "artifact_directory": str(tmp_path / "attempt-001"),
        },
    )

    result = native.native_pilot_main(
        ["--mode", "baseline", "--instance-id", INSTANCE.instance_id, "--generate-only"]
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_combined_run_rejects_missing_evaluator_before_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _mock_native_main_generation(tmp_path, monkeypatch)
    monkeypatch.delenv("CGR_SWEBENCH_EVALUATOR_PYTHON", raising=False)
    monkeypatch.setattr(
        native,
        "generate_attempt",
        lambda **kwargs: pytest.fail("generation must not run before evaluator preflight"),
    )

    result = native.native_pilot_main(
        [
            "--mode",
            "baseline",
            "--instance-id",
            INSTANCE.instance_id,
            "--generate-and-evaluate",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert result == 1
    assert "CGR_SWEBENCH_EVALUATOR_PYTHON is required" in output["error"]


def test_generate_and_evaluate_uses_separate_python_runtimes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _mock_native_main_generation(tmp_path, monkeypatch)
    evaluator = _evaluator(tmp_path)
    agent_python = str(tmp_path / "sweagent-python")
    monkeypatch.setenv("CGR_SWE_AGENT_PYTHON", agent_python)
    monkeypatch.setattr(native, "verify_evaluator_runtime", lambda: evaluator)
    attempt = tmp_path / "attempt-001"
    attempt.mkdir()
    observed: dict[str, object] = {}

    def generate(**kwargs: object) -> dict[str, object]:
        observed["sweagent_executable"] = kwargs["executable"]
        return {"infrastructure_status": "completed", "artifact_directory": str(attempt)}

    def evaluate(*args: object, **kwargs: object) -> dict[str, object]:
        observed["evaluator"] = kwargs["evaluator"]
        return {"exit_code": 0, "resolved": False}

    monkeypatch.setattr(native, "generate_attempt", generate)
    monkeypatch.setattr(native, "evaluate_attempt", evaluate)

    result = native.native_pilot_main(
        [
            "--mode",
            "baseline",
            "--instance-id",
            INSTANCE.instance_id,
            "--generate-and-evaluate",
        ]
    )

    assert result == 0
    assert observed["sweagent_executable"] == "official-sweagent"
    assert observed["evaluator"] == evaluator
    assert evaluator.python != agent_python
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_native_cli_and_ec2_runner_are_separate_from_legacy_adapter() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    script = Path("scripts/ec2_native_sweagent_astropy.sh").read_text(encoding="utf-8")

    assert 'cgr-swebench-native-pilot = "cgr.swebench.native_pilot:native_pilot_main"' in pyproject
    assert "cgr-swebench-swe-agent-adapter" not in script
    assert "--generate-and-evaluate" in script
    assert "set +e" in script and "PIPESTATUS[0]" in script
    assert "/tmp" not in script
    assert 'CGR_SWEBENCH_EVALUATOR_PYTHON="$PWD/.venv-swebench-eval/bin/python"' in script
    setup = Path("scripts/setup_swebench_evaluator.sh").read_text(encoding="utf-8")
    assert "evaluator_version='3.0.17'" in setup
    assert '"swebench==$evaluator_version"' in setup
