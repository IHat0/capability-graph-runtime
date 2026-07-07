"""Execution modes supported by the Booster Engine."""

from enum import Enum


class BoosterMode(str, Enum):
    BASELINE = "baseline"
    SINGLE_MODEL = "single_model"
    MULTI_MODEL = "multi_model"
