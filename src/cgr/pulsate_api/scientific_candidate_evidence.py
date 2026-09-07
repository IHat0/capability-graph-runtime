"""Deterministic interpretation of persisted candidate score evidence."""
from __future__ import annotations
import math


def ranked_candidates(document):
    return sorted(
        (candidate for candidate in document.get("candidates", [])
         if isinstance((candidate.get("selection") or {}).get("rank"), int)),
        key=lambda candidate: candidate["selection"]["rank"],
    )


def candidate_scores(candidate):
    return {item["objective_identifier"]: float(item["value"])
            for item in candidate.get("properties_and_calculations", [])
            if isinstance(item.get("value"), (int, float)) and math.isfinite(item["value"])}


def docking_confidence(document):
    candidates = ranked_candidates(document)
    values = sorted(candidate_scores(candidate)["vina_pose_score"] for candidate in candidates
                    if "vina_pose_score" in candidate_scores(candidate))
    if not values:
        return ()
    statements = [
        "Blocking verification checks recorded computational integrity; it does not establish the probability that this ranking is experimentally correct.",
        "The supported conclusion is a pose-score ordering under the recorded screening setup, not measured affinity, efficacy, safety, or selectivity.",
        "Different receptor conformations, protonation states, retained waters, search sampling, or scoring methods could change the ordering; their effects on this ranking have not been quantified here.",
    ]
    uncertainties = [item.get("uncertainty") for candidate in candidates
                     for item in candidate.get("properties_and_calculations", [])
                     if item.get("objective_identifier") == "vina_pose_score"]
    if uncertainties and all(value is None for value in uncertainties):
        statements.append(
            "The available docking score vectors contain no quantified uncertainty. No calibrated ranking confidence, confidence interval, or statistical significance can be assigned from them."
        )
    if len(values) > 1:
        gap = min(abs(right - left) for left, right in zip(values, values[1:]))
        statements.append(
            f"The smallest reported docking-score separation is {gap:.4f} kcal/mol. A numerical separation alone does not establish that the ordering is robust."
        )
    return tuple(statements)


import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class CandidateEvidenceQuery(BaseModel):
    """A model selects references and operations, never scientific values."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal["compare", "confidence", "unsupported"]
    candidate_ranks: tuple[int, ...] = Field(default=(), max_length=64)
    reuse_previous_selection: bool = False
    metric_identifiers: tuple[str, ...] = Field(default=(), max_length=16)
    supporting_quote: str = Field(min_length=1, max_length=8192)


class CandidateEvidenceReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    question: str
    source_execution_identifier: str
    source_artifact_identifiers: tuple[str, ...]
    source_artifact_sha256: tuple[str, ...]
    query: CandidateEvidenceQuery | None = None
    candidate_identifiers: tuple[str, ...] = ()
    metric_identifiers: tuple[str, ...] = ()
    favored_candidate_identifiers: tuple[str, ...] = ()
    conclusion_changed: bool | None = None
    response: str = Field(min_length=1, max_length=8192)
    new_scientific_computation: Literal[False] = False


_QUERY_INSTRUCTION = (
    'You are a literal evidence-query parser. Do not answer the question. Return only JSON with '
    'action ("compare", "confidence", or "unsupported"), candidate_ranks (list of existing rank integers, '
    'empty for all), reuse_previous_selection (boolean), metric_identifiers (list of explicitly requested '
    'metric identifiers; empty means original ranking policy), supporting_quote (exact full question). '
    'Resolve candidate names or ordinal references using candidate_index. '
    '"Those" or "these" referring to the previous discussed subset means reuse_previous_selection=true. '
    'Asking why a rank lost means compare, not new computation. Changing the metric subset over existing '
    'values is compare. Requests for new calculations, new data, changed structures or experimental '
    'conclusions are unsupported. Never return scientific values or an answer.'
)


def review_candidate_evidence(*, document, campaign, question, provider,
                              execution_identifier, references, previous=None):
    provenance = dict(question=question, source_execution_identifier=execution_identifier,
                      source_artifact_identifiers=tuple(r.artifact_identifier for r in references),
                      source_artifact_sha256=tuple(r.content_sha256 for r in references))
    def clarification(message, query=None):
        return CandidateEvidenceReview(**provenance, query=query, response=message)
    candidates = ranked_candidates(document)
    by_rank = {item["selection"]["rank"]: item for item in candidates}
    if len(by_rank) != len(candidates) or not candidates:
        return clarification("The recorded evidence has no unambiguous candidate ranking to reference. Please specify a candidate and generation; the existing result is preserved.")
    objectives = {item["objective_identifier"]: item for item in campaign.get("objectives", [])}
    if provider is None:
        return clarification("The evidence-query interpreter is unavailable. The completed calculation is preserved.")
    try:
        response = provider.complete([
            {"role": "system", "content": _QUERY_INSTRUCTION},
            {"role": "user", "content": json.dumps({
                "question": question,
                "candidate_index": [{"rank": item["selection"]["rank"],
                                     "label": item.get("display_name", item["candidate_identifier"])}
                                    for item in candidates],
                "available_metrics": list(objectives),
                "previous_candidate_ranks": [
                    item["selection"]["rank"] for item in candidates
                    if previous and item["candidate_identifier"] in previous.candidate_identifiers],
            })},
        ])
        query = CandidateEvidenceQuery.model_validate_json(response)
        if query.supporting_quote != question:
            raise ValueError("Query is not grounded in this turn")
        if query.action == "unsupported":
            return clarification("This follow-up requires information or computation beyond the recorded evidence. No new calculation ran and the existing result is preserved. Please specify an evidence-only comparison or the additional inputs and computation required.", query)
        if query.reuse_previous_selection:
            if not previous or not previous.candidate_identifiers:
                raise ValueError("There is no previous candidate subset")
            selected = [item for item in candidates if item["candidate_identifier"] in previous.candidate_identifiers]
        elif query.candidate_ranks:
            if len(set(query.candidate_ranks)) != len(query.candidate_ranks):
                raise ValueError("Duplicate candidate ranks")
            selected = [by_rank[rank] for rank in query.candidate_ranks]
        else:
            selected = candidates
        metrics = query.metric_identifiers or tuple(key for key, value in objectives.items() if value["weight"] > 0)
        if not metrics or len(set(metrics)) != len(metrics) or any(key not in objectives for key in metrics):
            raise ValueError("Unsupported metric selection")
        if any(key not in candidate_scores(item) for item in selected for key in metrics):
            raise ValueError("A selected candidate lacks requested evidence")
    except (AttributeError, KeyError, TypeError, ValueError, RuntimeError):
        return clarification("I could not resolve this follow-up unambiguously against the recorded candidates and metrics. Please identify the candidate names or ranks and the evidence to compare. The completed result is preserved; no new calculation ran.")

    selected = sorted(selected, key=lambda item: item["selection"]["rank"])
    original_leader = selected[0]["candidate_identifier"]
    if not query.metric_identifiers:
        favored = [selected[0]]
    else:
        def dominates(left, right):
            a, b = candidate_scores(left), candidate_scores(right)
            comparisons = [a[key] <= b[key] if objectives[key]["direction"] == "minimize" else a[key] >= b[key] for key in metrics]
            return all(comparisons) and any(a[key] != b[key] for key in metrics)
        favored = [item for item in selected if not any(dominates(other, item) for other in selected if other is not item)]
    ids = tuple(item["candidate_identifier"] for item in selected)
    favored_ids = tuple(item["candidate_identifier"] for item in favored)
    baseline_favored = ((previous.favored_candidate_identifiers if previous and set(previous.candidate_identifiers) == set(ids)
                         else (original_leader,)))
    changed = set(favored_ids) != set(baseline_favored)
    labels = lambda items: ", ".join(str(item.get("display_name", item["candidate_identifier"])) for item in items)
    text = ["This is a read-only analysis of the verified, persisted evidence. No new docking calculation ran.",
            "Compared candidates: " + labels(selected) + ".",
            "Evidence used: " + ", ".join(metrics) + "."]
    for item in selected:
        scores = candidate_scores(item)
        text.append(str(item.get("display_name", item["candidate_identifier"])) + ": " +
                    "; ".join(f"{key} = {scores[key]:.4f}" + (" kcal/mol" if key == "vina_pose_score" else "") for key in metrics) + ".")
    if len(selected) == 2:
        left, right = selected
        for key in metrics:
            gap = candidate_scores(right)[key] - candidate_scores(left)[key]
            text.append(f"{right.get('display_name', right['candidate_identifier'])} minus {left.get('display_name', left['candidate_identifier'])}: {gap:+.4f}"
                        + (" kcal/mol" if key == "vina_pose_score" else "") + f" for {key}; the recorded direction is {objectives[key]['direction']}.")
    if len(favored) == 1:
        text.append("The recorded evidence favors " + labels(favored) + " under the selected criteria.")
    else:
        text.append("These criteria do not identify a unique winner; non-dominated candidates are " + labels(favored) + ".")
    text.append("The favored candidate set " + ("changes" if changed else "does not change") + " relative to the preceding comparison or original ranking within this subset.")
    inactive = [key for key, value in objectives.items() if value["weight"] == 0]
    if inactive:
        text.append("The original ranking assigned zero weight to " + ", ".join(inactive) + "; those descriptors did not cause a candidate to lose.")
    text.extend(docking_confidence({"candidates": selected}))
    return CandidateEvidenceReview(**provenance, query=query, candidate_identifiers=ids,
        metric_identifiers=tuple(metrics), favored_candidate_identifiers=favored_ids,
        conclusion_changed=changed, response=" ".join(text))
