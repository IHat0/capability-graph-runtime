"""Generic shortlist constraints never change the evaluated collection."""
import json
import pytest
from cgr.pulsate_api.scientific_requirements import (
    CandidateSelectionConstraint, ProviderNeutralScientificRequirementInterpreter,
    validate_requirement_proposal,
)

@pytest.mark.parametrize("quantity,count", [("two", 2), ("three", 3), ("seven", 7), ("25", 25)])
def test_literal_shortlist_quantity_survives_composition(quantity, count):
    question = f"Rank the collection and recommend {quantity} candidates for review."
    class Provider:
        provider_kind = "test"
        model_name = "test"
        def complete(self, messages):
            return json.dumps({
                "requirements": [{"operation": "verify_and_rank_results",
                    "requested_output": "verified_candidate_ranking", "supporting_quote": question}],
                "candidate_selection": {"count": count, "supporting_quote": f"recommend {quantity} candidates"},
            })
    proposal = ProviderNeutralScientificRequirementInterpreter(Provider()).propose(question)
    assert proposal is not None
    requirements = validate_requirement_proposal(proposal, available_input_types=("protein_structure", "ligand_structure"))
    assert requirements.candidate_selection.count == count
    assert requirements.capability_profile == "protein_ligand_discovery"

def test_invented_shortlist_quantity_is_rejected():
    with pytest.raises(ValueError):
        CandidateSelectionConstraint(count=3, supporting_quote="recommend two candidates")
