from __future__ import annotations

from cgr.swebench.action_validation import validate_repository_action


ROOT = "/runtime/worktree-42"
FILES = (
    "astropy/utils/introspection.py",
    "astropy/__init__.py",
    "docs/astropy/utils/introspection.py",
)


def test_relative_and_absolute_paths_inside_the_runtime_repository_are_accepted() -> None:
    relative = validate_repository_action(
        "sed -i 's/old/new/' astropy/utils/introspection.py",
        repository_root=ROOT,
        repository_files=FILES,
    )
    absolute = validate_repository_action(
        "cat /runtime/worktree-42/astropy/utils/introspection.py",
        repository_root=ROOT,
        repository_files=FILES,
    )

    assert relative.allowed
    assert absolute.allowed


def test_external_repository_path_is_rejected_before_execution_with_root_feedback() -> None:
    result = validate_repository_action(
        "sed -i 's/old/new/' ~/dev/astropy/astropy/utils/introspection.py",
        repository_root=ROOT,
        repository_files=FILES,
    )

    assert not result.allowed
    assert result.invalid_paths == ("~/dev/astropy/astropy/utils/introspection.py",)
    assert result.suggested_repository_relative_paths == ("astropy/utils/introspection.py",)
    assert result.feedback is not None
    assert ROOT in result.feedback
    assert "ACTION REJECTED BY CGR" in result.feedback
    assert result.metrics["cgr_action_rejections"] == 1


def test_ambiguous_suffix_is_not_silently_selected() -> None:
    result = validate_repository_action(
        "cat ~/dev/vendor/astropy/utils/introspection.py",
        repository_root=ROOT,
        repository_files=(
            "astropy/utils/introspection.py",
            "vendor/astropy/utils/introspection.py",
        ),
    )

    assert not result.allowed
    assert result.suggested_repository_relative_paths == ()


def test_repeated_external_path_gets_stronger_feedback_and_corrected_action_recovers() -> None:
    invalid = "~/dev/astropy/astropy/utils/introspection.py"
    repeated = validate_repository_action(
        f"cat {invalid}",
        repository_root=ROOT,
        repository_files=FILES,
        prior_invalid_paths=(invalid,),
    )
    corrected = validate_repository_action(
        "cat astropy/utils/introspection.py",
        repository_root=ROOT,
        repository_files=FILES,
        prior_invalid_paths=(invalid,),
    )

    assert not repeated.allowed
    assert repeated.feedback is not None
    assert "already attempted" in repeated.feedback
    assert repeated.metrics["repeated_invalid_path_rejections"] == 1
    assert corrected.allowed
    assert corrected.metrics["recovery_after_cgr_rejection"]
    assert corrected.metrics["first_valid_action_after_rejection"]


def test_python_redirection_and_system_paths_are_handled_conservatively() -> None:
    rejected_python = validate_repository_action(
        "python -c \"open('/outside/repo.py', 'w').write('x')\"",
        repository_root=ROOT,
        repository_files=FILES,
    )
    rejected_redirection = validate_repository_action(
        "grep minversion astropy/utils/introspection.py > ../outside.txt",
        repository_root=ROOT,
        repository_files=FILES,
    )
    system = validate_repository_action(
        "python -c \"import sys; print(sys.version)\"; cat /etc/hosts",
        repository_root=ROOT,
        repository_files=FILES,
    )

    assert not rejected_python.allowed
    assert not rejected_redirection.allowed
    assert system.allowed
