"""Task domains supported by the Booster Engine."""

from enum import Enum


class BoosterDomain(str, Enum):
    CODING = "coding"
    MATH = "math"
    PHYSICS = "physics"
    REASONING = "reasoning"
    GENERAL = "general"
