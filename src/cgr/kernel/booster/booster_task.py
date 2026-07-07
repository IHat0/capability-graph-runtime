"""Booster task contract."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cgr.kernel.coding import CodeTestCase

from .booster_domain import BoosterDomain


class BoosterTask(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    domain: BoosterDomain
    prompt: str = Field(min_length=1)
    input_data: dict[str, Any] = Field(default_factory=dict)
    expected_output: Any | None = None
    required_output_keys: set[str] = Field(default_factory=set)
    test_files: dict[str, str] = Field(default_factory=dict)
    test_commands: list[CodeTestCase] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)
