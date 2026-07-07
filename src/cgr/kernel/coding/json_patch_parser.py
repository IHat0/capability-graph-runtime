"""Parser for structured coding patches in model text."""

import json
import re

from .coding_patch import CodingPatch


class JsonPatchParser:
    """Parse raw or fenced JSON into a validated coding patch."""

    _FENCED_JSON = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)

    def parse(self, text: str) -> CodingPatch:
        candidates = [text]
        candidates.extend(self._FENCED_JSON.findall(text))
        decoder = json.JSONDecoder()
        for candidate in candidates:
            stripped = candidate.strip()
            starts = [0] if stripped.startswith("{") else []
            starts.extend(
                index for index, character in enumerate(stripped) if character == "{"
            )
            for start in dict.fromkeys(starts):
                try:
                    value, _ = decoder.raw_decode(stripped[start:])
                    return CodingPatch.model_validate(value)
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
        raise ValueError("Model output did not contain a valid coding patch JSON object.")
