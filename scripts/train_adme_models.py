"""Reproducible fixed-policy endpoint training; immutable models before test evaluation.

Research dependencies are separate from inference. Source files are supplied as
hash-pinned local configuration; this script does not fetch arbitrary datasets.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from rdkit import Chem, DataStructs, rdBase
from rdkit.Chem.Scaffolds import MurckoScaffold
from sklearn import __version__ as sklearn_version
from sklearn.ensemble import RandomForestRegressor
from scipy.stats import spearmanr

from cgr.pulsate_api.adme_prediction import ENDPOINTS, FEATURES, VERSION, applicability, forest_value, molecular_features
from cgr.pulsate_api.virtual_organism import canonical, digest

POLICY = {"seed": 20261002, "n_estimators": 64, "max_depth": 12, "min_samples_leaf": 3, "max_features": .7,
    "split": "graph-deduplicated scaffold hash buckets: train 0..69, calibration 70..84, test 85..99",
    "coverage": .90, "domain_similarity_percentile": .10, "duplicate_graph_label": "median",
    "censoring": "PPBR >=100 or <0 excluded; CLint <=3 or >=150 excluded as assay boundary labels"}


def rows_for(source):
    payload = Path(source["path"]).read_bytes()
    if digest(payload) != source["sha256"]:
        raise ValueError("Training source hash mismatch.")
    grouped, rejected = defaultdict(list), Counter()
    for row in csv.DictReader(payload.decode("utf-8-sig").splitlines(), delimiter="\t"):
        try:
            if source.get("species_column") and row[source["species_column"]] != source["species_value"]:
                rejected["other_species"] += 1
                continue
            y = float(row["Y"])
            if not math.isfinite(y): raise ValueError("nonfinite_label")
            if source["endpoint"] == "fraction_unbound":
                if not 0 <= y < 100: raise ValueError("censored_or_invalid_binding_label")
                y = math.log10(1 - y / 100)
            if source["endpoint"] == "hepatocyte_intrinsic_clearance":
                if not 3 < y < 150: raise ValueError("censored_assay_boundary")
                y = math.log10(y)
            smiles = row.get("Drug") or row.get("X")
            identity, properties, values, fp = molecular_features(smiles)
            graph = identity["smiles"]
            grouped[graph].append((y, identity, properties, values, fp, row.get("Drug_ID") or row.get("ID")))
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            rejected[str(error)] += 1
    rows = []
    for graph, records in sorted(grouped.items()):
        y, identity, properties, values, fp, identifier = records[0]
        scaffold = MurckoScaffold.MurckoScaffoldSmiles(mol=Chem.MolFromSmiles(graph), includeChirality=False) or graph
        bucket = int(digest((str(POLICY["seed"]) + ":" + scaffold).encode())[:8], 16) % 100
        split = "train" if bucket < 70 else "calibration" if bucket < 85 else "test"
        rows.append({"identity": identity, "properties": properties, "x": values, "fp": fp, "y": statistics.median(r[0] for r in records),
            "identifier": identifier, "replicates": len(records), "split": split, "scaffold": scaffold})
    return rows, dict(rejected)


def train(source, output):
    rows, rejected = rows_for(source)
    groups = {s: [r for r in rows if r["split"] == s] for s in ("train", "calibration", "test")}
    if any(len(items) < 30 for items in groups.values()):
        raise ValueError("Insufficient independent endpoint split.")
    train_rows, cal_rows, test_rows = groups.values()
    forest = RandomForestRegressor(n_estimators=64, max_depth=12, min_samples_leaf=3, max_features=.7, random_state=POLICY["seed"], n_jobs=2)
    forest.fit(np.array([r["x"] for r in train_rows], dtype=np.float32), [r["y"] for r in train_rows])
    cal_preds = forest.predict(np.array([r["x"] for r in cal_rows], dtype=np.float32))
    residuals = sorted(abs(r["y"] - p) for r,p in zip(cal_rows, cal_preds, strict=True))
    order = min(len(residuals), math.ceil((len(residuals) + 1) * .90))
    width = float(residuals[order - 1])
    nearest = [max(DataStructs.BulkTanimotoSimilarity(r["fp"], [t["fp"] for t in train_rows])) for r in cal_rows]
    model = {"schema_version": VERSION, "features": FEATURES, "endpoint": source["endpoint"], "species": source.get("species"),
        "unit": ENDPOINTS[source["endpoint"]][0], "policy": POLICY,
        "training": {"source_url": source["url"], "dataset_sha256": source["sha256"], "dataset_name": source["dataset_name"],
            "license": source["license"], "rdkit_version": rdBase.rdkitVersion, "sklearn_version": sklearn_version,
            "split_counts": {s: len(g) for s,g in groups.items()}, "rejected": rejected,
            "duplicate_graph_rows": sum(r["replicates"] - 1 for r in rows),
            "split_hashes": {s: digest(canonical([{ "graph": r["identity"]["graph_sha256"], "label": r["y"], "scaffold": r["scaffold"]} for r in g])) for s,g in groups.items()}},
        "calibration": {"coverage_target": .9, "n": len(cal_rows), "order_statistic": order, "absolute_error_quantile": width,
            "method": "Split conformal absolute residual; log endpoint units. Marginal coverage under exchangeability only."},
        "domain": {"training_fingerprints": [r["fp"].ToBitString() for r in train_rows],
            "training_graph_hashes": [r["identity"]["graph_sha256"] for r in train_rows],
            "descriptor_ranges": {name: [min(r["properties"][name] for r in train_rows), max(r["properties"][name] for r in train_rows)] for name in FEATURES["descriptors"]},
            "similarity_threshold": float(np.quantile(nearest, .1))},
        "trees": [{"left": t.tree_.children_left.tolist(), "right": t.tree_.children_right.tolist(), "feature": t.tree_.feature.tolist(),
            "threshold": t.tree_.threshold.tolist(), "value": t.tree_.value[:,0,0].tolist()} for t in forest.estimators_]}
    name = source["endpoint"] + "-" + (source.get("species") or "chemistry") + ".json"
    payload = canonical(model)
    with (output / name).open("xb") as stream: stream.write(payload)
    model_sha = digest(payload)
    # Numerical parity check uses CALIBRATION ONLY; test isn't examined yet.
    assert np.allclose(cal_preds, [forest_value(model, r["x"]) for r in cal_rows], rtol=0, atol=1e-12)
    return {"endpoint": source["endpoint"], "species": source.get("species"), "path": name, "sha256": model_sha}, model, test_rows


def evaluate(model, rows):
    results = []
    for row in rows:
        pred = forest_value(model, row["x"])
        error = pred - row["y"]
        domain = applicability(model, row["properties"], row["fp"], row["identity"])
        results.append({"smiles": row["identity"]["smiles"], "inchikey": row["identity"]["inchikey"], "identifier": row["identifier"],
            "experimental": row["y"], "prediction": pred, "error": error, "interval_covered": abs(error) <= model["calibration"]["absolute_error_quantile"],
            "domain": domain["status"], "nearest_training_tanimoto": domain["nearest_training_tanimoto"], "properties": row["properties"]})
    def metrics(items):
        if not items: return {"n": 0}
        errors = [r["error"] for r in items]
        correlation = float(spearmanr([r["experimental"] for r in items], [r["prediction"] for r in items]).statistic) if len(items) > 2 else None
        return {"n": len(items), "mae": statistics.mean(abs(e) for e in errors), "rmse": math.sqrt(statistics.mean(e*e for e in errors)),
            "median_multiplicative_error": 10 ** statistics.median(abs(e) for e in errors),
            "spearman": correlation if correlation is not None and math.isfinite(correlation) else None,
            "interval_coverage": statistics.mean(r["interval_covered"] for r in items)}
    return {"overall": metrics(results), "by_domain": {s: metrics([r for r in results if r["domain"] == s]) for s in ("in_domain", "weakly_supported", "out_of_domain")},
        "unit": model["unit"], "rows": results, "scope": "Internal scaffold-held-out experimental labels; not an external prospective study"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    sources = json.loads(args.sources.read_bytes())
    trained = [train(source, args.output) for source in sources]
    manifest = {"schema_version": "pulsate.adme-model-manifest/v1", "models": [entry for entry,_,_ in trained], "training_policy": POLICY}
    (args.output / "manifest.json").write_bytes(canonical(manifest))
    # All models and calibration artifacts are immutable and hashed BEFORE held-out evaluation.
    freeze_sha = digest(canonical(manifest))
    print("models_frozen_manifest_sha256:", freeze_sha, flush=True)
    report = {"manifest_sha256": freeze_sha, "policy": POLICY, "endpoints": {entry["path"]: evaluate(model, rows) for entry,model,rows in trained}}
    (args.output / "held-out-validation.json").write_bytes(canonical(report))
    for entry,_,_ in trained:
        print(entry["path"], json.dumps(report["endpoints"][entry["path"]]["overall"]), flush=True)


if __name__ == "__main__": main()
