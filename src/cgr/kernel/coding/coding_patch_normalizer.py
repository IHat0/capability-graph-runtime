"""Robust normalization of model text into strict coding patches."""

import ast
import json
import re
from typing import Any

from .coding_patch import CodingPatch


class CodingPatchNormalizationError(ValueError):
    """Normalization failure carrying a safe, capped raw-output preview."""

    def __init__(self, message: str, raw_output: str) -> None:
        super().__init__(message)
        self.raw_output_preview = raw_output[:1000]


class CodingPatchNormalizer:
    """Normalize common model output variants into a validated CodingPatch."""

    _FENCED_JSON = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)

    def normalize(
        self,
        text: str,
        allowed_filenames: set[str] | None = None,
    ) -> CodingPatch:
        candidates: list[str] = [text]
        candidates.extend(self._FENCED_JSON.findall(text))
        balanced = self._first_balanced_object(text)
        if balanced is not None:
            candidates.append(balanced)

        for candidate in candidates:
            try:
                parsed = json.loads(candidate.strip())
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            patch = self._from_object(parsed, allowed_filenames)
            if patch is not None:
                return patch

        if allowed_filenames is not None and len(allowed_filenames) == 1:
            filename = next(iter(allowed_filenames))
            if self._looks_like_python(text):
                return self._validate_files({filename: text}, allowed_filenames)

        raise CodingPatchNormalizationError(
            "Model output could not be normalized into non-empty coding patch "
            "JSON with a 'files' mapping.",
            text,
        )

    def _from_object(
        self,
        parsed: Any,
        allowed_filenames: set[str] | None,
    ) -> CodingPatch | None:
        if not isinstance(parsed, dict):
            return None
        files = parsed.get("files")
        explanation = parsed.get("explanation", "")
        if files is None and parsed and all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in parsed.items()
        ):
            files = parsed
            explanation = ""
        if not isinstance(files, dict):
            return None
        return self._validate_files(files, allowed_filenames, explanation)

    @staticmethod
    def _validate_files(
        files: dict[Any, Any],
        allowed_filenames: set[str] | None,
        explanation: Any = "",
    ) -> CodingPatch:
        if not files:
            raise CodingPatchNormalizationError(
                "Coding patch files must not be empty.", json.dumps({"files": files})
            )
        normalized: dict[str, str] = {}
        for filename, content in files.items():
            if not isinstance(filename, str) or not filename.strip():
                raise CodingPatchNormalizationError(
                    "Coding patch filenames must be non-empty strings.",
                    json.dumps({"files": files}),
                )
            if not isinstance(content, str) or not content.strip():
                raise CodingPatchNormalizationError(
                    f"Coding patch content for '{filename}' must be non-empty.",
                    json.dumps({"files": files}),
                )
            if allowed_filenames is not None and filename not in allowed_filenames:
                raise CodingPatchNormalizationError(
                    f"Coding patch contains unknown filename '{filename}'.",
                    json.dumps({"files": files}),
                )
            normalized[filename] = content
        return CodingPatch(
            files=normalized,
            explanation=explanation if isinstance(explanation, str) else "",
        )

    @staticmethod
    def _first_balanced_object(text: str) -> str | None:
        for start, character in enumerate(text):
            if character != "{":
                continue
            depth = 0
            in_string = False
            escaped = False
            for index in range(start, len(text)):
                current = text[index]
                if in_string:
                    if escaped:
                        escaped = False
                    elif current == "\\":
                        escaped = True
                    elif current == '"':
                        in_string = False
                    continue
                if current == '"':
                    in_string = True
                elif current == "{":
                    depth += 1
                elif current == "}":
                    depth -= 1
                    if depth == 0:
                        return text[start : index + 1]
        return None

    @staticmethod
    def _looks_like_python(text: str) -> bool:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return False
        if not tree.body:
            return False
        return not all(
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            for node in tree.body
        )
