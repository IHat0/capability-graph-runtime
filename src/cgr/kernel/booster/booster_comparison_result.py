"""Baseline versus boosted comparison result."""

from pydantic import BaseModel, ConfigDict

from .booster_domain import BoosterDomain
from .booster_mode import BoosterMode
from .booster_result import BoosterResult


class BoosterComparisonResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    domain: BoosterDomain
    baseline: BoosterResult
    boosted_single: BoosterResult
    boosted_multi: BoosterResult | None = None

    @property
    def single_improved(self) -> bool:
        return self.boosted_single.score > self.baseline.score

    @property
    def multi_improved(self) -> bool:
        return (
            self.boosted_multi is not None
            and self.boosted_multi.score > self.baseline.score
        )

    @property
    def best_score(self) -> float:
        return self._best_result().score

    @property
    def best_mode(self) -> BoosterMode:
        return self._best_result().mode

    def _best_result(self) -> BoosterResult:
        results = [self.baseline, self.boosted_single]
        if self.boosted_multi is not None:
            results.append(self.boosted_multi)
        return max(results, key=lambda result: result.score)
