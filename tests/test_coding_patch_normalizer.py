import inspect
from typing import Any

import pytest

from cgr.kernel.coding import (
    CodeTestCase,
    CodingPatchNormalizationError,
    CodingPatchNormalizer,
    PythonTestRunner,
)
from cgr.kernel.contracts import ExecutionContext, ExecutionRequest
from cgr.kernel.runtime import KernelRuntime
from cgr.kernel.swe import SWEABRunner, SWETask
from cgr.plugins.agents import (
    MultiModelCodingAgentPlugin,
    SingleModelCodingAgentPlugin,
)
from cgr.plugins.providers.openai_compatible import (
    OpenAICompatibleChatConfig,
    OpenAICompatibleChatPlugin,
)


NORMAL = '{"files":{"app.py":"print(\\"ok\\")\\n"}}'


@pytest.mark.parametrize(
    "text",
    [
        NORMAL,
        f"```json\n{NORMAL}\n```",
        f"Here is the patch:\n{NORMAL}\nDone.",
    ],
)
def test_normalizer_parses_json_variants(text: str) -> None:
    patch = CodingPatchNormalizer().normalize(text, {"app.py"})

    assert patch.files == {"app.py": 'print("ok")\n'}


def test_normalizer_wraps_flat_filename_mapping() -> None:
    patch = CodingPatchNormalizer().normalize(
        '{"app.py":"print(1)\\n"}', {"app.py"}
    )

    assert patch.files == {"app.py": "print(1)\n"}


def test_normalizer_wraps_raw_python_for_single_allowed_file() -> None:
    patch = CodingPatchNormalizer().normalize(
        "def answer():\n    return 42\n", {"app.py"}
    )

    assert patch.files == {"app.py": "def answer():\n    return 42\n"}


def test_normalizer_can_defer_raw_python_fallback_for_format_retry() -> None:
    source = "def answer():\n    return 42\n"

    with pytest.raises(CodingPatchNormalizationError):
        CodingPatchNormalizer().normalize(
            source, {"app.py"}, allow_raw_python=False
        )

    patch = CodingPatchNormalizer().raw_python_single_file_patch(
        source, {"app.py"}
    )

    assert patch is not None
    assert patch.files == {"app.py": source}


def test_normalizer_remaps_filename_placeholder_for_single_allowed_file() -> None:
    patch = CodingPatchNormalizer().normalize(
        '{"files":{"filename.py":"def answer():\\n    return 42\\n"}}',
        {"src/router.py"},
    )

    assert patch.files == {"src/router.py": "def answer():\n    return 42\n"}
    assert patch.placeholder_filename_remapped is True
    assert patch.placeholder_filename_original == "filename.py"
    assert patch.placeholder_filename_target == "src/router.py"


def test_normalizer_remaps_solution_placeholder_for_single_allowed_file() -> None:
    patch = CodingPatchNormalizer().normalize(
        '{"files":{"solution.py":"VALUE = 7\\n"}}',
        {"src/config.py"},
    )

    assert patch.files == {"src/config.py": "VALUE = 7\n"}
    assert patch.placeholder_filename_remapped is True
    assert patch.placeholder_filename_original == "solution.py"
    assert patch.placeholder_filename_target == "src/config.py"


def test_normalizer_does_not_remap_placeholder_with_multiple_allowed_files() -> None:
    with pytest.raises(ValueError, match="unknown filename"):
        CodingPatchNormalizer().normalize(
            '{"files":{"filename.py":"def answer():\\n    return 42\\n"}}',
            {"src/a.py", "src/b.py"},
        )


def test_normalizer_rejects_non_placeholder_unknown_file() -> None:
    with pytest.raises(ValueError, match="unknown filename"):
        CodingPatchNormalizer().normalize(
            '{"files":{"mystery.py":"def answer():\\n    return 42\\n"}}',
            {"src/app.py"},
        )


def test_remapped_placeholder_candidate_is_tested_normally() -> None:
    patch = CodingPatchNormalizer().normalize(
        '{"files":{"filename.py":"def answer():\\n    return 42\\n"}}',
        {"src/app.py"},
    )

    passed, messages = PythonTestRunner().run(
        patch.files,
        {
            "visible_tests.py": (
                "from src.app import answer\n"
                "assert answer() == 42\n"
            )
        },
        [CodeTestCase(name="visible", command=["python", "visible_tests.py"])],
    )

    assert passed, messages


def test_normalizer_rejects_empty_and_unknown_files() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        CodingPatchNormalizer().normalize('{"files":{}}')
    with pytest.raises(ValueError, match="unknown filename"):
        CodingPatchNormalizer().normalize(
            '{"files":{"other.py":"print(1)"}}', {"app.py"}
        )


def test_normalizer_uses_no_eval_and_reports_helpful_error() -> None:
    source = inspect.getsource(CodingPatchNormalizer)
    assert "eval(" not in source

    with pytest.raises(
        CodingPatchNormalizationError, match="could not be normalized"
    ) as error:
        CodingPatchNormalizer().normalize("This is not code or JSON.", {"app.py"})

    assert error.value.raw_output_preview == "This is not code or JSON."


class SequenceClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.prompts: list[str] = []

    def create_chat_completion(
        self,
        config: OpenAICompatibleChatConfig,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        self.prompts.append(messages[-1]["content"])
        index = min(len(self.prompts) - 1, len(self._responses) - 1)
        return {"choices": [{"message": {"content": self._responses[index]}}]}


def _model_runtime(
    responses: list[str], capability_id: str = "model.code"
) -> tuple[KernelRuntime, SequenceClient, OpenAICompatibleChatPlugin]:
    runtime = KernelRuntime()
    client = SequenceClient(responses)
    plugin = OpenAICompatibleChatPlugin(
        config=OpenAICompatibleChatConfig(
            api_key="local", model="format", base_url="http://localhost"
        ),
        client=client,
        capability_id=capability_id,
        plugin_id=f"format.{capability_id}",
    )
    runtime.register_plugin(plugin)
    return runtime, client, plugin


def _swe_task() -> SWETask:
    return SWETask(
        id="format",
        issue='Print "ok".',
        files={"app.py": 'print("wrong")\n'},
        expected_files={"app.py": 'print("ok")\n'},
        test_files={
            "test_task.py": (
                "import subprocess, sys\n"
                "result = subprocess.run([sys.executable, 'app.py'], "
                "capture_output=True, text=True)\n"
                "assert result.stdout == 'ok\\n'\n"
            )
        },
        test_commands=[
            CodeTestCase(name="format", command=["python", "test_task.py"])
        ],
    )


@pytest.mark.parametrize(
    "response",
    [f"```json\n{NORMAL}\n```", 'print("ok")\n'],
)
def test_baseline_normalizes_fenced_json_and_raw_python(response: str) -> None:
    runtime, _, plugin = _model_runtime([response])

    case = SWEABRunner(runtime)._run_case(
        _swe_task(), "baseline", plugin.metadata.id
    )

    assert case.passed is True


def test_single_agent_retries_invalid_format() -> None:
    runtime, client, _ = _model_runtime(["Here is an answer, but no code.", NORMAL])
    agent = SingleModelCodingAgentPlugin(runtime)
    runtime.register_plugin(agent)

    result = runtime.execute(
        agent.metadata.id,
        ExecutionRequest(
            capability=agent.metadata.capabilities[0],
            context=ExecutionContext(),
            payload={"issue": "Print ok.", "files": {"app.py": "print('bad')\n"}},
        ),
    )

    assert result.output["files"] == {"app.py": 'print("ok")\n'}
    assert "could not be parsed as a coding patch" in client.prompts[1]
    assert "Use the exact allowed path: app.py." in client.prompts[1]
    assert result.output["format_retry_used"] is True
    assert result.output["format_retry_succeeded"] is True


def test_single_agent_recovers_malformed_single_file_json_after_format_retry() -> None:
    malformed = (
        '{\n  "files": {\n    "filename.py": "def answer():\n'
        '    \"\"\"Return the answer.\"\"\"\n'
        "    return 42\n" + '\n  }\n}'
    )
    runtime, client, _ = _model_runtime(
        [malformed, '{"files":{"src/config.py":"def answer():\\n    return 42\\n"}}']
    )
    agent = SingleModelCodingAgentPlugin(runtime)
    runtime.register_plugin(agent)

    result = runtime.execute(
        agent.metadata.id,
        ExecutionRequest(
            capability=agent.metadata.capabilities[0],
            context=ExecutionContext(),
            payload={
                "issue": "Return 42.",
                "files": {"src/config.py": "def answer():\n    return 0\n"},
                "allowed_files_to_edit": ["src/config.py"],
            },
        ),
    )

    assert result.output["files"] == {"src/config.py": "def answer():\n    return 42\n"}
    assert "Use the exact allowed path: src/config.py." in client.prompts[1]
    assert result.output["format_retry_used"] is True
    assert result.output["format_retry_succeeded"] is True
    assert result.output["raw_python_single_file_fallback_used"] is False


def test_multi_agent_retries_invalid_draft_format() -> None:
    runtime, client, _ = _model_runtime(["Malformed response", NORMAL])
    critic_runtime_client = SequenceClient(["No correction needed."])
    critic = OpenAICompatibleChatPlugin(
        config=OpenAICompatibleChatConfig(
            api_key="local", model="critic", base_url="http://localhost"
        ),
        client=critic_runtime_client,
        capability_id="model.reason",
        plugin_id="format.critic",
    )
    runtime.register_plugin(critic)
    agent = MultiModelCodingAgentPlugin(runtime)
    runtime.register_plugin(agent)

    result = runtime.execute(
        agent.metadata.id,
        ExecutionRequest(
            capability=agent.metadata.capabilities[0],
            context=ExecutionContext(),
            payload={
                "issue": _swe_task().issue,
                "files": _swe_task().files,
                "test_files": _swe_task().test_files,
                "test_commands": _swe_task().test_commands,
            },
        ),
    )

    assert result.output["files"] == {"app.py": 'print("ok")\n'}
    assert len(client.prompts) == 2


def test_raw_output_preview_is_exposed_after_failed_retry() -> None:
    runtime, _, plugin = _model_runtime(["first invalid", "second invalid"])

    case = SWEABRunner(runtime)._run_case(
        _swe_task(), "baseline", plugin.metadata.id
    )

    assert case.passed is False
    assert case.raw_output_preview == "second invalid"
    assert "could not be normalized" in (case.error_message or "")
