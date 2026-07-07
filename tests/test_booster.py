import json

import pytest
from pydantic import ValidationError

from cgr.kernel.booster import (
    BoosterBenchmarkRunner,
    BoosterCandidate,
    BoosterComparisonResult,
    BoosterDomain,
    BoosterEngine,
    BoosterMode,
    BoosterResult,
    BoosterTask,
    BoosterTrace,
)
from cgr.kernel.coding import CodeTestCase
from cgr.kernel.runtime import KernelRuntime
from cgr.plugins.agents import (
    LocalBoosterBaseModelPlugin,
    LocalBoosterCriticModelPlugin,
)


def coding_task(task_id: str = "local.greeting") -> BoosterTask:
    data = {
        "local.greeting": (
            'Change the program so it prints "hello CGR".',
            {"app.py": 'print("hello")\n'},
            {"app.py": 'print("hello CGR")\n'},
        ),
        "local.add": (
            "Fix add so it returns a + b.",
            {"math_utils.py": "def add(a, b):\n    return a - b\n"},
            {"math_utils.py": "def add(a, b):\n    return a + b\n"},
        ),
        "local.is_even": (
            "Fix is_even for even and odd numbers.",
            {"number_utils.py": "def is_even(n):\n    return n % 2 == 1\n"},
            {"number_utils.py": "def is_even(n):\n    return n % 2 == 0\n"},
        ),
    }
    prompt, files, expected = data[task_id]
    return BoosterTask(
        id=task_id,
        domain=BoosterDomain.CODING,
        prompt=prompt,
        input_data={"files": files},
        expected_output=expected,
    )


def local_engine() -> BoosterEngine:
    runtime = KernelRuntime()
    runtime.register_plugin(LocalBoosterBaseModelPlugin())
    runtime.register_plugin(LocalBoosterCriticModelPlugin())
    return BoosterEngine(
        runtime,
        base_capability_id="model.code",
        critic_capability_id="model.reason",
    )


def result(mode: BoosterMode, score: float) -> BoosterResult:
    trace = BoosterTrace(
        task_id="task",
        mode=mode,
        steps=[],
        candidate_ids=["candidate"],
        selected_candidate_id="candidate",
    )
    candidate = BoosterCandidate(
        candidate_id="candidate", mode=mode, text="answer", score=score
    )
    return BoosterResult(
        task_id="task",
        domain=BoosterDomain.GENERAL,
        mode=mode,
        output_text="answer",
        passed=score == 1.0,
        score=score,
        candidates=[candidate],
        trace=trace,
    )


def test_booster_enum_values() -> None:
    assert [domain.value for domain in BoosterDomain] == [
        "coding",
        "math",
        "physics",
        "reasoning",
        "general",
    ]
    assert [mode.value for mode in BoosterMode] == [
        "baseline",
        "single_model",
        "multi_model",
    ]


def test_booster_models_are_immutable_and_validate_fields() -> None:
    task = coding_task()
    candidate = BoosterCandidate(
        candidate_id="candidate", mode=BoosterMode.BASELINE, text="answer"
    )
    trace = BoosterTrace(
        task_id="task", mode=BoosterMode.BASELINE, steps=[], candidate_ids=[]
    )

    with pytest.raises(ValidationError):
        task.prompt = "changed"
    with pytest.raises(ValidationError):
        BoosterTask(id="", domain=BoosterDomain.GENERAL, prompt="prompt")
    with pytest.raises(ValidationError):
        candidate.score = 2.0
    with pytest.raises(ValidationError):
        BoosterCandidate(
            candidate_id="candidate",
            mode=BoosterMode.BASELINE,
            text="answer",
            score=1.1,
        )
    with pytest.raises(ValidationError):
        BoosterTrace(
            task_id="task",
            mode=BoosterMode.BASELINE,
            steps=[],
            candidate_ids=[],
            model_calls=-1,
        )
    with pytest.raises(ValidationError):
        BoosterResult(
            task_id="task",
            domain=BoosterDomain.GENERAL,
            mode=BoosterMode.BASELINE,
            output_text="",
            passed=False,
            score=0,
            candidates=[],
            trace=trace,
        )


def test_comparison_properties_select_improved_best_mode() -> None:
    comparison = BoosterComparisonResult(
        task_id="task",
        domain=BoosterDomain.GENERAL,
        baseline=result(BoosterMode.BASELINE, 0.0),
        boosted_single=result(BoosterMode.SINGLE_MODEL, 0.5),
        boosted_multi=result(BoosterMode.MULTI_MODEL, 1.0),
    )

    assert comparison.single_improved is True
    assert comparison.multi_improved is True
    assert comparison.best_score == 1.0
    assert comparison.best_mode == BoosterMode.MULTI_MODEL


def test_baseline_calls_base_model_once() -> None:
    solved = local_engine().solve(coding_task(), BoosterMode.BASELINE)

    assert solved.trace.model_calls == 1
    assert solved.trace.candidate_ids == ["baseline"]


def test_single_model_generates_candidates_and_improves_add() -> None:
    engine = local_engine()
    task = coding_task("local.add")

    baseline = engine.solve(task, BoosterMode.BASELINE)
    boosted = engine.solve(task, BoosterMode.SINGLE_MODEL)

    assert baseline.score == 0.0
    assert boosted.score == 1.0
    assert {candidate.candidate_id for candidate in boosted.candidates} >= {
        "candidate_1",
        "candidate_2",
        "candidate_3",
    }


def test_multi_model_uses_critic_and_improves_over_single() -> None:
    engine = local_engine()
    task = coding_task("local.is_even")

    single = engine.solve(task, BoosterMode.SINGLE_MODEL)
    multi = engine.solve(task, BoosterMode.MULTI_MODEL)

    assert single.score == 0.0
    assert multi.score == 1.0
    assert any(
        "modulo comparison to zero" in (candidate.critique or "")
        for candidate in multi.candidates
    )


def test_model_failure_returns_error_result() -> None:
    failed = BoosterEngine(KernelRuntime()).solve(coding_task())

    assert failed.passed is False
    assert failed.score == 0.0
    assert failed.error_type == "CapabilityNotFoundError"


def test_coding_scoring_accepts_raw_and_fenced_json() -> None:
    engine = local_engine()
    task = coding_task()
    payload = json.dumps(
        {"files": task.expected_output, "explanation": "fixed"}
    )

    raw = engine._score_candidate(task, payload)
    fenced = engine._score_candidate(task, f"```json\n{payload}\n```")

    assert raw[0:2] == (1.0, True)
    assert fenced[0:2] == (1.0, True)


def test_coding_scoring_prioritizes_tests_over_exact_text() -> None:
    task = BoosterTask(
        id="functional-add",
        domain=BoosterDomain.CODING,
        prompt="Fix add.",
        expected_output={"math_utils.py": "def add(a, b):\n    return a + b\n"},
        test_files={
            "test_task.py": (
                "from math_utils import add\n"
                "assert add(1, 2) == 3\n"
                "assert add(-5, 5) == 0\n"
            )
        },
        test_commands=[
            CodeTestCase(name="add", command=["python", "test_task.py"])
        ],
    )
    text_different = json.dumps(
        {
            "files": {
                "math_utils.py": (
                    "def add(a: float, b: float) -> float:\n"
                    "    \"\"\"Return the sum.\"\"\"\n"
                    "    return a + b\n"
                )
            }
        }
    )

    score, verified, _, messages = local_engine()._score_candidate(
        task, text_different
    )

    assert (score, verified) == (1.0, True)
    assert any("exit code 0" in message for message in messages)


def test_coding_scoring_returns_zero_when_tests_fail() -> None:
    task = BoosterTask(
        id="failed-add",
        domain=BoosterDomain.CODING,
        prompt="Fix add.",
        test_files={
            "test_task.py": "from math_utils import add\nassert add(1, 2) == 3\n"
        },
        test_commands=[
            CodeTestCase(name="add", command=["python", "test_task.py"])
        ],
    )
    wrong = json.dumps(
        {"files": {"math_utils.py": "def add(a, b):\n    return a - b\n"}}
    )

    assert local_engine()._score_candidate(task, wrong)[0:2] == (0.0, False)


def test_required_output_key_scoring() -> None:
    task = BoosterTask(
        id="schema",
        domain=BoosterDomain.REASONING,
        prompt="Return structured output.",
        required_output_keys={"answer", "reason"},
    )

    score, verified, structured, _ = local_engine()._score_candidate(
        task, '{"answer":"yes","reason":"evidence"}'
    )

    assert (score, verified) == (1.0, True)
    assert structured == {"answer": "yes", "reason": "evidence"}


def test_compare_and_benchmark_runner_compute_improvement() -> None:
    engine = local_engine()
    tasks = [
        coding_task(name)
        for name in ("local.greeting", "local.add", "local.is_even")
    ]

    comparison = engine.compare(tasks[1])
    report = BoosterBenchmarkRunner(engine).run("local", tasks)

    assert comparison.single_improved is True
    assert comparison.boosted_multi is not None
    assert report["baseline_average_score"] == pytest.approx(1 / 3)
    assert report["boosted_single_average_score"] == pytest.approx(2 / 3)
    assert report["boosted_multi_average_score"] == 1.0
    assert report["single_improvement_rate"] == pytest.approx(1 / 3)
    assert report["multi_improvement_rate"] == pytest.approx(2 / 3)


def test_benchmark_runner_handles_empty_suite() -> None:
    report = BoosterBenchmarkRunner(local_engine()).run("empty", [])

    assert report["total_tasks"] == 0
    assert report["baseline_average_score"] == 0.0
    assert report["single_improvement_rate"] == 0.0
    assert report["multi_improvement_rate"] == 0.0
