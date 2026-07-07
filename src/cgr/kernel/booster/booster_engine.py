"""Central CGR model performance Booster Engine."""

import json
import re
from time import perf_counter
from typing import Any

from cgr.kernel.coding import JsonPatchParser, PythonTestRunner
from cgr.kernel.contracts import (
    Capability,
    CapabilityVersion,
    ExecutionContext,
    ExecutionRequest,
    ExecutionStatus,
)
from cgr.kernel.model import ModelMessage, ModelRequest, ModelRole
from cgr.kernel.runtime import KernelRuntime

from .booster_candidate import BoosterCandidate
from .booster_comparison_result import BoosterComparisonResult
from .booster_domain import BoosterDomain
from .booster_mode import BoosterMode
from .booster_result import BoosterResult
from .booster_task import BoosterTask
from .booster_trace import BoosterTrace


class BoosterEngine:
    """Wrap model capabilities with candidate, critique, repair, and scoring."""

    def __init__(
        self,
        runtime: KernelRuntime,
        base_capability_id: str = "model.reason",
        critic_capability_id: str | None = None,
        max_candidates: int = 3,
        max_repair_attempts: int = 2,
    ) -> None:
        if max_candidates < 1:
            raise ValueError("max_candidates must be positive.")
        if max_repair_attempts < 0:
            raise ValueError("max_repair_attempts must not be negative.")
        self._runtime = runtime
        self._base_capability_id = base_capability_id
        self._critic_capability_id = critic_capability_id
        self._max_candidates = max_candidates
        self._max_repair_attempts = max_repair_attempts

    def solve(
        self,
        task: BoosterTask,
        mode: BoosterMode = BoosterMode.SINGLE_MODEL,
    ) -> BoosterResult:
        started = perf_counter()
        candidates: list[BoosterCandidate] = []
        steps: list[str] = []
        verifier_messages: list[str] = []
        model_calls = 0

        def call(capability_id: str, prompt: str) -> str:
            nonlocal model_calls
            model_calls += 1
            return self._call_model(capability_id, prompt)

        try:
            if mode == BoosterMode.BASELINE:
                steps.append("Called base model once with the direct task prompt.")
                text = call(
                    self._base_capability_id,
                    self._baseline_prompt(task),
                )
                candidate, messages = self._candidate(
                    task, "baseline", mode, text
                )
                candidates.append(candidate)
                verifier_messages.extend(messages)
            else:
                critic_capability = self._base_capability_id
                if mode == BoosterMode.MULTI_MODEL:
                    if self._critic_capability_id is None:
                        raise ValueError(
                            "critic_capability_id is required for multi-model mode."
                        )
                    critic_capability = self._critic_capability_id

                steps.append(
                    f"Generated {self._max_candidates} candidate answers."
                )
                for index in range(1, self._max_candidates + 1):
                    text = call(
                        self._base_capability_id,
                        self._candidate_prompt(task, index),
                    )
                    candidate, messages = self._candidate(
                        task, f"candidate_{index}", mode, text
                    )
                    candidates.append(candidate)
                    verifier_messages.extend(messages)

                steps.append("Critiqued generated candidates.")
                repairs = 0
                for index, candidate in enumerate(list(candidates)):
                    critique = call(
                        critic_capability,
                        self._critique_prompt(task, candidate.text),
                    )
                    candidates[index] = candidate.model_copy(
                        update={"critique": critique}
                    )
                    if candidate.score >= 1.0 or repairs >= self._max_repair_attempts:
                        continue
                    repairs += 1
                    repaired_text = call(
                        self._base_capability_id,
                        self._repair_prompt(task, candidate.text, critique),
                    )
                    repaired, messages = self._candidate(
                        task,
                        f"repair_{repairs}",
                        mode,
                        repaired_text,
                        critique=critique,
                        repair_of=candidate.candidate_id,
                    )
                    candidates.append(repaired)
                    verifier_messages.extend(messages)
                steps.append(f"Repaired {repairs} weak candidates.")

            selected = max(candidates, key=lambda candidate: candidate.score)
            steps.append(f"Selected {selected.candidate_id} by deterministic score.")
            trace = BoosterTrace(
                task_id=task.id,
                mode=mode,
                steps=steps,
                candidate_ids=[candidate.candidate_id for candidate in candidates],
                selected_candidate_id=selected.candidate_id,
                verifier_messages=verifier_messages,
                model_calls=model_calls,
            )
            return BoosterResult(
                task_id=task.id,
                domain=task.domain,
                mode=mode,
                output_text=selected.text,
                structured_output=selected.structured_output,
                passed=selected.verified,
                score=selected.score,
                candidates=candidates,
                trace=trace,
                duration_ms=(perf_counter() - started) * 1000,
            )
        except Exception as exc:
            trace = BoosterTrace(
                task_id=task.id,
                mode=mode,
                steps=steps + ["Stopped after model or orchestration failure."],
                candidate_ids=[candidate.candidate_id for candidate in candidates],
                verifier_messages=verifier_messages,
                model_calls=model_calls,
            )
            return BoosterResult(
                task_id=task.id,
                domain=task.domain,
                mode=mode,
                output_text="",
                passed=False,
                score=0.0,
                candidates=candidates,
                trace=trace,
                error_type=type(exc).__name__,
                error_message=str(exc),
                duration_ms=(perf_counter() - started) * 1000,
            )

    def compare(
        self, task: BoosterTask, include_multi: bool = True
    ) -> BoosterComparisonResult:
        baseline = self.solve(task, BoosterMode.BASELINE)
        single = self.solve(task, BoosterMode.SINGLE_MODEL)
        multi = (
            self.solve(task, BoosterMode.MULTI_MODEL)
            if include_multi and self._critic_capability_id is not None
            else None
        )
        return BoosterComparisonResult(
            task_id=task.id,
            domain=task.domain,
            baseline=baseline,
            boosted_single=single,
            boosted_multi=multi,
        )

    def _call_model(self, capability_id: str, prompt: str) -> str:
        capability = Capability(
            id=capability_id,
            name="Booster Model",
            description="Model capability orchestrated by the Booster Engine.",
            version=CapabilityVersion(major=1, minor=0, patch=0),
        )
        result = self._runtime.execute_capability(
            ExecutionRequest[ModelRequest](
                capability=capability,
                context=ExecutionContext(),
                payload=ModelRequest(
                    messages=[ModelMessage(role=ModelRole.USER, content=prompt)]
                ),
            )
        )
        if result.status != ExecutionStatus.SUCCESS:
            raise RuntimeError(result.error or "Booster model execution failed.")
        output = result.output
        if not isinstance(output, dict) or not isinstance(output.get("text"), str):
            raise ValueError("Model output did not contain text.")
        return output["text"]

    def _candidate(
        self,
        task: BoosterTask,
        candidate_id: str,
        mode: BoosterMode,
        text: str,
        critique: str | None = None,
        repair_of: str | None = None,
    ) -> tuple[BoosterCandidate, list[str]]:
        score, verified, structured, messages = self._score_candidate(task, text)
        return (
            BoosterCandidate(
                candidate_id=candidate_id,
                mode=mode,
                text=text,
                structured_output=structured,
                score=score,
                verified=verified,
                critique=critique,
                repair_of=repair_of,
            ),
            messages,
        )

    def _score_candidate(
        self, task: BoosterTask, candidate_text: str
    ) -> tuple[float, bool, dict[str, Any] | None, list[str]]:
        if task.domain == BoosterDomain.CODING:
            try:
                structured = JsonPatchParser().parse(candidate_text).model_dump()
            except ValueError:
                return 0.0, False, None, ["Coding output was not valid patch JSON."]
            files = structured["files"]
            if task.test_files and task.test_commands:
                passed, messages = PythonTestRunner().run(
                    files,
                    task.test_files,
                    task.test_commands,
                )
                return (
                    1.0 if passed else 0.0,
                    passed,
                    structured,
                    messages,
                )
            if task.expected_output is not None:
                passed = files == task.expected_output
                return (
                    1.0 if passed else 0.0,
                    passed,
                    structured,
                    [
                        "Coding files matched expected output."
                        if passed
                        else "Coding files did not match expected output."
                    ],
                )
            return 0.7, True, structured, ["Coding output had a non-empty files mapping."]

        if task.expected_output is not None:
            passed = candidate_text.strip() == str(task.expected_output).strip()
            return (
                1.0 if passed else 0.0,
                passed,
                None,
                [
                    "Answer matched expected output."
                    if passed
                    else "Answer did not match expected output."
                ],
            )

        if task.required_output_keys:
            parsed_output = self._parse_json_object(candidate_text)
            passed = parsed_output is not None and task.required_output_keys.issubset(
                parsed_output
            )
            return (
                1.0 if passed else 0.0,
                passed,
                parsed_output,
                ["Required output keys were present." if passed else "Required output keys were missing."],
            )

        return 0.5, False, None, ["Non-empty output received; no formal verifier configured."]

    @staticmethod
    def _parse_json_object(text: str) -> dict[str, Any] | None:
        candidates = [text]
        candidates.extend(
            re.findall(r"```json\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
        )
        decoder = json.JSONDecoder()
        for candidate in candidates:
            for index, character in enumerate(candidate):
                if character != "{":
                    continue
                try:
                    value, _ = decoder.raw_decode(candidate[index:])
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    return value
        return None

    @staticmethod
    def _task_context(task: BoosterTask) -> str:
        return f"Task:\n{task.prompt}\nInput data:\n{json.dumps(task.input_data, indent=2)}"

    def _baseline_prompt(self, task: BoosterTask) -> str:
        return f"BASELINE EXECUTION\n{self._task_context(task)}"

    def _candidate_prompt(self, task: BoosterTask, index: int) -> str:
        instruction = "Produce the best possible answer."
        if task.domain == BoosterDomain.CODING:
            instruction = (
                "Return ONLY JSON with files mapping filenames to complete patched "
                "contents and a short explanation."
            )
        return (
            f"CANDIDATE GENERATION {index}\n{self._task_context(task)}\n"
            f"{instruction}"
        )

    def _critique_prompt(self, task: BoosterTask, candidate: str) -> str:
        return (
            f"CRITIQUE\n{self._task_context(task)}\nCandidate:\n{candidate}\n"
            "Identify correctness issues, missing requirements, and specific improvements."
        )

    def _repair_prompt(
        self, task: BoosterTask, candidate: str, critique: str
    ) -> str:
        return (
            f"REPAIR\n{self._task_context(task)}\nCandidate:\n{candidate}\n"
            f"Critique:\n{critique}\nReturn the improved final answer only."
        )
