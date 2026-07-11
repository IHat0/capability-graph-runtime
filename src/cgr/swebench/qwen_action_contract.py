"""Offline conformance checks for Qwen responses sent to SWE-agent thought_action."""

from __future__ import annotations

import re


_BLOCK = re.compile(r"\ADISCUSSION\s*\n(?P<thought>[^`]*?)\n```bash\s*\n(?P<action>.*?)\n```\s*\Z", re.DOTALL)
_RAW_PYTHON = re.compile(r"^\s*(?:from\s+\S+\s+import\s+|import\s+|def\s+|class\s+|assert\s+)", re.MULTILINE)


def validate_qwen_action_contract(response: str) -> str:
    """Return the Bash action only when it matches CGR's strict Qwen contract."""
    match = _BLOCK.fullmatch(response)
    if match is None or response.count("```") != 2:
        raise ValueError("Response must contain DISCUSSION and exactly one Bash fenced block.")
    action = match.group("action").strip()
    if not action:
        raise ValueError("Bash action must not be empty.")
    if _RAW_PYTHON.search(action):
        raise ValueError("Raw Python source is not a Bash action; use a Bash heredoc if needed.")
    return action


def extract_v1_1_thought_action(response: str) -> str:
    """Reference the pinned v1.1.0 parser's last-fenced-block extraction behavior."""
    marker = re.compile(r"^```(\S*)\s*\n|^```\s*$", re.MULTILINE)
    stack: list[re.Match[str]] = []
    last_valid: tuple[re.Match[str], re.Match[str]] | None = None
    for match in marker.finditer(response):
        if stack and not match.group(1):
            start = stack.pop()
            if not stack:
                last_valid = (start, match)
        elif match.group(1) is not None:
            stack.append(match)
    if last_valid is None:
        raise ValueError("No action found in model response.")
    start, end = last_valid
    return response[start.end() : end.start()].strip()
