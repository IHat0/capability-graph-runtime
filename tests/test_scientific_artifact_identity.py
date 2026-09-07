"""Equal scientific payloads retain distinct calculation provenance."""
from cgr.pulsate_api.phase8_scientific_handlers import _NativeRunner
from cgr.pulsate_api.scientific_production import DurableScientificPayloadStore


def test_equal_payloads_from_distinct_calculations_can_share_durable_store(tmp_path):
    store = DurableScientificPayloadStore(tmp_path / "artifacts")
    store.start()
    try:
        runner = _NativeRunner(store)
        arguments = dict(
            artifact_type="molecular_structure_analysis",
            payload=b'{"atom_count":3}',
            producer="molecular.structure_analyze",
        )
        first = runner.write_json(**arguments, execution_identifier="calculation-first")
        repeated = runner.write_json(**arguments, execution_identifier="calculation-first")
        second = runner.write_json(**arguments, execution_identifier="calculation-second")
        assert repeated == first
        assert second.content_sha256 == first.content_sha256
        assert second.artifact_identifier != first.artifact_identifier
        assert first.provenance.execution_identifier == "calculation-first"
        assert second.provenance.execution_identifier == "calculation-second"
        assert store.read(first) == store.read(second) == arguments["payload"]
        distinct_metadata = runner.write_json(
            **arguments, execution_identifier="calculation-first",
            metadata={"source_kind": "independent-check"},
        )
        assert distinct_metadata.artifact_identifier != first.artifact_identifier
        assert store.read(distinct_metadata) == arguments["payload"]
    finally:
        store.close()
