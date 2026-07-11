import json
import subprocess
from pathlib import Path

import pytest

from cgr.swebench.cli import pilot_main
from cgr.swebench.integration import (
    DATASET_NAME,
    DEFAULT_BUDGETS,
    FORBIDDEN_MODEL_FIELDS,
    MODES,
    Prediction,
    RepositoryActions,
    SwebenchManifest,
    capture_git_patch,
    deterministic_select,
    doctor_report,
    filter_model_instance,
    freeze_manifest,
    generation_result_template,
    integrity_check,
    official_harness_command,
    selected_ids_hash,
    validate_manifest,
    validate_prediction_hash,
    verify_patch_applies,
    write_predictions,
)


def _records() -> list[dict[str, str]]:
    return [
        {
            "instance_id": f"owner{i % 6}__repo{i % 6}-{i:03d}",
            "repo": f"owner{i % 6}/repo{i % 6}",
            "base_commit": f"{i:040x}",
            "problem_statement": f"Public issue {i}",
            "hints_text": "safe hint",
            "patch": f"gold-{i}",
            "test_patch": f"hidden-test-{i}",
            "FAIL_TO_PASS": "hidden",
            "PASS_TO_PASS": "hidden",
            "version": "answer-location",
        }
        for i in range(18)
    ]


def _manifest(path: Path) -> SwebenchManifest:
    return freeze_manifest(_records(), path, dataset_fingerprint="test-fingerprint")


def test_dataset_safe_field_filter_excludes_gold_and_test_data() -> None:
    safe = filter_model_instance(_records()[0], "/workspace/repo")
    payload = safe.model_dump()

    assert set(payload).isdisjoint(FORBIDDEN_MODEL_FIELDS)
    assert "gold-0" not in json.dumps(payload)
    assert "hidden-test-0" not in json.dumps(payload)
    assert payload["problem_statement"] == "Public issue 0"


def test_safe_instance_forbids_extra_answer_fields() -> None:
    payload = filter_model_instance(_records()[0]).model_dump()
    payload["patch"] = "gold"

    with pytest.raises(ValueError):
        type(filter_model_instance(_records()[0])).model_validate(payload)


def test_frozen_manifest_refuses_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "pilot.json"
    _manifest(path)

    with pytest.raises(FileExistsError, match="frozen manifest"):
        freeze_manifest(_records(), path)


def test_deterministic_selection_is_diverse_sorted_and_stable() -> None:
    first = deterministic_select(_records())
    second = deterministic_select(reversed(_records()))

    assert first == second
    assert len(first) == 10
    assert len({item.repo for item in first}) >= 5
    assert [item.instance_id for item in first] == sorted(
        item.instance_id for item in first
    )


def test_manifest_hash_and_dataset_identity_are_valid(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path / "pilot.json")

    validate_manifest(manifest)
    assert manifest.dataset_name == DATASET_NAME
    assert manifest.selected_ids_sha256 == selected_ids_hash(
        [item.instance_id for item in manifest.instances]
    )


def test_prediction_jsonl_uses_official_schema_and_hash(tmp_path: Path) -> None:
    path = tmp_path / "predictions.jsonl"
    digest = write_predictions(
        path,
        [Prediction(instance_id="a__b-1", model_name_or_path="qwen", model_patch="diff --git a/a b/a")],
    )
    row = json.loads(path.read_text(encoding="utf-8"))

    assert set(row) == {"instance_id", "model_name_or_path", "model_patch"}
    assert path.with_suffix(".sha256").read_text().strip() == digest
    validate_prediction_hash(path)


def test_prediction_hash_detects_post_lock_change(tmp_path: Path) -> None:
    path = tmp_path / "predictions.jsonl"
    write_predictions(path, [Prediction(instance_id="i", model_name_or_path="m", model_patch="p")])
    path.write_text(path.read_text() + "\n")

    with pytest.raises(ValueError, match="hash mismatch"):
        validate_prediction_hash(path)


def _git_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "module.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "add", "module.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    return repo, base


def test_unified_diff_generation_changed_files_and_base_apply(tmp_path: Path) -> None:
    repo, base = _git_repo(tmp_path)
    (repo / "module.py").write_text("VALUE = 2\n")

    patch, changed = capture_git_patch(repo)

    assert patch.startswith("diff --git")
    assert changed == ["module.py"]
    verify_patch_applies(repo, patch, base)


def test_unified_diff_includes_new_text_files(tmp_path: Path) -> None:
    repo, base = _git_repo(tmp_path)
    (repo / "new_module.py").write_text("NEW = True\n")

    patch, changed = capture_git_patch(repo)

    assert changed == ["new_module.py"]
    verify_patch_applies(repo, patch, base)


def test_empty_patch_is_rejected(tmp_path: Path) -> None:
    repo, _ = _git_repo(tmp_path)

    with pytest.raises(ValueError, match="empty"):
        capture_git_patch(repo)


def test_repository_actions_enforce_paths_and_safe_commands(tmp_path: Path) -> None:
    repo, _ = _git_repo(tmp_path)
    actions = RepositoryActions(repo)

    assert "module.py" in actions.list_files()
    assert "VALUE = 1" in actions.read_file("module.py")
    assert actions.inspect_symbols("module.py") == []
    with pytest.raises(ValueError, match="escapes"):
        actions.read_file("../outside")
    with pytest.raises(ValueError, match="allowlist"):
        actions.run_safe(["git", "log"])
    with pytest.raises(ValueError, match="Arbitrary Python"):
        actions.run_safe(["python", "-c", "print('unsafe')"])


def test_doctor_reports_docker_unavailable_without_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cgr.swebench.integration.shutil.which", lambda _: None)

    report = doctor_report(tmp_path / "missing-manifest.json")

    assert report["docker_cli_available"] is False
    assert report["docker_daemon_available"] is False
    assert report["frozen_manifest_exists"] is False


def test_generation_result_separates_local_and_official_evaluation() -> None:
    result = generation_result_template("cgr_multi", "qwen", "provider", "mini-swe-agent")

    assert result["local_verification_passed"] is False
    assert result["official_evaluation_run"] is False
    assert result["official_resolved"] is None
    assert result["budget"] == DEFAULT_BUDGETS["cgr_multi"].model_dump()


def test_official_harness_subprocess_shape() -> None:
    command = official_harness_command("gold", ["sympy__sympy-20590"], "cgr-gold-smoke")

    assert command[1:3] == ["-m", "swebench.harness.run_evaluation"]
    assert command[command.index("--dataset_name") + 1] == DATASET_NAME
    assert command[command.index("--predictions_path") + 1] == "gold"
    assert command[command.index("--max_workers") + 1] == "1"


def test_integrity_accepts_same_instances_and_model_for_all_modes(tmp_path: Path) -> None:
    manifest_path = tmp_path / "pilot.json"
    manifest = _manifest(manifest_path)
    result_root = tmp_path / "results"
    for mode in MODES:
        write_predictions(
            result_root / mode / "predictions.jsonl",
            [
                Prediction(
                    instance_id=item.instance_id,
                    model_name_or_path="qwen-2.5-coder-7b",
                    model_patch="diff --git a/a b/a",
                )
                for item in manifest.instances
            ],
        )

    assert integrity_check(manifest_path, result_root)["passed"] is True


def test_integrity_rejects_inconsistent_model_identity(tmp_path: Path) -> None:
    manifest_path = tmp_path / "pilot.json"
    manifest = _manifest(manifest_path)
    result_root = tmp_path / "results"
    for index, mode in enumerate(MODES):
        write_predictions(
            result_root / mode / "predictions.jsonl",
            [
                Prediction(
                    instance_id=item.instance_id,
                    model_name_or_path=f"model-{index}",
                    model_patch="diff --git a/a b/a",
                )
                for item in manifest.instances
            ],
        )

    with pytest.raises(ValueError, match="inconsistent model"):
        integrity_check(manifest_path, result_root)


def test_integrity_rejects_forbidden_generation_fields(tmp_path: Path) -> None:
    manifest_path = tmp_path / "pilot.json"
    _manifest(manifest_path)
    result_root = tmp_path / "results"
    generation = result_root / "baseline" / "generation-results.json"
    generation.parent.mkdir(parents=True)
    generation.write_text(json.dumps({"test_patch": "leak"}))

    with pytest.raises(ValueError, match="forbidden field"):
        integrity_check(manifest_path, result_root)


def test_pilot_dry_run_requires_frozen_manifest_but_not_model(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = tmp_path / "pilot.json"
    _manifest(manifest_path)

    assert (
        pilot_main(
            [
                "--all-modes",
                "--dry-run",
                "--manifest",
                str(manifest_path),
                "--result-root",
                str(tmp_path / "results"),
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["modes"] == list(MODES)
    assert len(output["instance_ids"]) == 10


def test_resume_and_real_generation_require_explicit_agent_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = tmp_path / "pilot.json"
    _manifest(manifest_path)
    for key in (
        "CGR_DRAFT_API_KEY",
        "CGR_DRAFT_BASE_URL",
        "CGR_DRAFT_MODEL",
        "CGR_SWEBENCH_AGENT_COMMAND",
    ):
        monkeypatch.delenv(key, raising=False)

    assert pilot_main(["--resume", "--manifest", str(manifest_path)]) == 2


def test_mode_budgets_are_explicit_and_bounded() -> None:
    assert set(DEFAULT_BUDGETS) == set(MODES)
    for budget in DEFAULT_BUDGETS.values():
        assert 0 < budget.maximum_model_calls <= 20
        assert 0 < budget.maximum_steps <= 40
        assert 0 < budget.timeout_seconds <= 3600


def test_manifest_validation_rejects_non_ten_instance_set(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path / "pilot.json")
    invalid = manifest.model_copy(update={"instances": manifest.instances[:9]})

    with pytest.raises(ValueError, match="exactly ten"):
        validate_manifest(invalid)


def test_windows_style_escape_and_git_metadata_are_rejected(tmp_path: Path) -> None:
    repo, _ = _git_repo(tmp_path)
    actions = RepositoryActions(repo)

    with pytest.raises(ValueError):
        actions.read_file(".git/config")
    with pytest.raises(ValueError):
        actions.read_file("..\\outside")
