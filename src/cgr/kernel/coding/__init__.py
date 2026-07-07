"""Coding-agent contracts and parsing utilities."""

from .coding_patch import CodingPatch
from .coding_prompt import build_patch_prompt
from .coding_task import CodingTask
from .json_patch_parser import JsonPatchParser

__all__ = ["CodingPatch", "CodingTask", "JsonPatchParser", "build_patch_prompt"]
