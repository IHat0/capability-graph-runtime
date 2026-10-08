"""Replay frozen ADME validation and predeclared research-use gates.

Hashes alone do not establish numerical validity. This boundary binds a submitted
prediction to its actual model, manifest, held-out rows and numerical gate. It is
software/numerical verification, not independent biological qualification.
"""
from __future__ import annotations

import functools
import json
import math
import statistics

from .virtual_organism import canonical, digest


def _source(sources, sha):
    payload = sources.get(sha)
    if payload is None or digest(payload) != sha:
        raise ValueError('Prediction validation requires its exact preserved source bytes.')
    return payload


@functools.lru_cache(maxsize=32)
def _replay(model_bytes, manifest_bytes, report_bytes):
    from rdkit import Chem, rdBase
    from rdkit.Chem.Scaffolds import MurckoScaffold
    from .adme_prediction import molecular_features, forest_value, applicability
    model, manifest, report = map(json.loads, (model_bytes, manifest_bytes, report_bytes))
    model_sha, manifest_sha = digest(model_bytes), digest(manifest_bytes)
    matches = [m for m in manifest.get('models', []) if m['sha256'] == model_sha]
    formats = {'pulsate.adme-rf/v1': 'pulsate.adme-model-manifest/v1',
        'pulsate.target-activity-rf/v1': 'pulsate.target-activity-manifest/v1'}
    if (model.get('schema_version') not in formats or manifest.get('schema_version') != formats.get(model.get('schema_version')) or len(matches) != 1
            or report.get('manifest_sha256') != manifest_sha
            or model['training']['rdkit_version'] != rdBase.rdkitVersion):
        raise ValueError('Prediction model, feature runtime, manifest and validation report disagree.')
    entry = matches[0]
    keys = ('endpoint', 'species') if model['schema_version']=='pulsate.adme-rf/v1' else ('kind', 'species', 'target_accession')
    if any(entry[k] != model[k] for k in keys):
        raise ValueError('Predictive endpoint/species mismatch in the frozen manifest.')
    evidence = report['endpoints'][entry['path']]
    rows = evidence['rows']
    if not 1 <= len(rows) <= 20000 or evidence['unit'] != model['unit']:
        raise ValueError('Held-out rows/unit are invalid or unbounded.')
    seen, split, verified = set(), [], []
    width = model['calibration']['absolute_error_quantile']
    if not math.isfinite(width) or width <= 0:
        raise ValueError('Invalid frozen uncertainty calibration.')
    for row in rows:
        graph, properties, features, fp = molecular_features(row['smiles'])
        graph_sha = graph['graph_sha256']
        if graph_sha in seen or graph_sha in model['domain']['training_graph_hashes']:
            raise ValueError('Held-out validation contains duplicate or training-overlap graphs.')
        seen.add(graph_sha)
        scaffold = MurckoScaffold.MurckoScaffoldSmiles(mol=Chem.MolFromSmiles(graph['smiles']), includeChirality=False) or graph['smiles']
        bucket = int(digest((str(model['policy']['seed']) + ':' + scaffold).encode())[:8], 16) % 100
        if bucket < 85 or graph['inchikey'] != row['inchikey']:
            raise ValueError('Validation graph is not in the declared scaffold-held-out partition.')
        observed = row['experimental']
        if type(observed) not in (int, float) or not math.isfinite(observed):
            raise ValueError('Invalid held-out experimental label.')
        predicted = forest_value(model, features, model_schema=model['schema_version'])
        error = predicted - observed
        domain = applicability(model, properties, fp, graph)
        if (not math.isclose(row['prediction'], predicted, rel_tol=0, abs_tol=1e-12)
                or not math.isclose(row['error'], error, rel_tol=0, abs_tol=1e-12)
                or row['interval_covered'] is not (abs(error) <= width)
                or row['domain'] != domain['status']):
            raise ValueError('Held-out model inference, applicability or interval failed independent replay.')
        split.append({'graph': graph_sha, 'label': observed, 'scaffold': scaffold})
        verified.append((domain['status'], error, abs(error) <= width, observed, predicted))
    if (digest(canonical(split)) != model['training']['split_hashes']['test']
            or len(rows) != model['training']['split_counts']['test']):
        raise ValueError('Held-out validation labels/partition differ from the frozen training receipt.')
    for domain, declared in [('all', evidence['overall']), *evidence['by_domain'].items()]:
        subset = verified if domain == 'all' else [v for v in verified if v[0] == domain]
        computed = {'n': len(subset)}
        if subset:
            errors = [r[1] for r in subset]
            computed.update(mae=statistics.mean(abs(e) for e in errors),
                rmse=math.sqrt(statistics.mean(e * e for e in errors)),
                interval_coverage=statistics.mean(r[2] for r in subset))
            if 'median_multiplicative_error' in declared:
                computed['median_multiplicative_error'] = 10 ** statistics.median(abs(e) for e in errors)
            if 'spearman' in declared:
                def ranks(values):
                    result=[0.]*len(values)
                    ordered=sorted(range(len(values)),key=values.__getitem__)
                    index=0
                    while index<len(ordered):
                        end=index+1
                        while end<len(ordered) and values[ordered[end]]==values[ordered[index]]: end+=1
                        for position in ordered[index:end]: result[position]=(index+end+1)/2
                        index=end
                    return result
                try:
                    computed['spearman'] = statistics.correlation(ranks([r[3] for r in subset]),ranks([r[4] for r in subset])) if len(subset)>2 else None
                except statistics.StatisticsError:
                    computed['spearman'] = None
        if any(k not in declared or (declared[k] is not None if v is None else
                declared[k] is None or not math.isclose(declared[k], v, rel_tol=0, abs_tol=1e-12)) for k, v in computed.items()):
            raise ValueError('Held-out aggregate metrics disagree with their independently replayed rows.')
    return {'report_sha256': digest(report_bytes), 'manifest_sha256': manifest_sha,
        'overall': evidence['overall'], 'by_domain': evidence['by_domain'],
        'scope': evidence['scope'], 'unit': evidence['unit']}


def verify_adme_validation(model_sha, provenance, validation, sources):
    report_bytes = _source(sources, validation.get('report_sha256'))
    manifest_bytes = _source(sources, validation.get('manifest_sha256'))
    model_bytes = _source(sources, model_sha)
    gate = json.loads(_source(sources, validation.get('gate_sha256')))
    model = json.loads(model_bytes)
    if model.get('schema_version') != 'pulsate.adme-rf/v1':
        raise ValueError('A binding-activity model cannot be relabelled as an ADME predictor.')
    if (gate.get('schema') != 'pulsate.sponsor-prediction-gate/v1'
            or gate.get('endpoint') != model['endpoint'] or gate.get('species') != model['species']
            or gate.get('unit') != model['unit'] or gate.get('domain') != 'in_domain'
            or not all(gate.get(k) for k in ('reviewer', 'version', 'rationale', 'limitations'))):
        raise ValueError('Prediction requires an endpoint-specific, frozen research-use gate.')
    checked = _replay(model_bytes, manifest_bytes, report_bytes)
    if canonical(checked) != canonical(provenance.get('validation')):
        raise ValueError('Prediction validation metadata differs from the actual held-out evidence.')
    metrics = checked['by_domain']['in_domain']
    limits = gate.get('limits', {})
    required = {'minimum_n', 'minimum_interval_coverage', 'maximum_mae', 'maximum_rmse'}
    if (set(limits) != required or any(type(v) not in (int, float) or not math.isfinite(v) for v in limits.values())
            or type(limits['minimum_n']) is not int or limits['minimum_n'] < 1
            or not 0 < limits['minimum_interval_coverage'] <= 1
            or limits['maximum_mae'] <= 0 or limits['maximum_rmse'] <= 0):
        raise ValueError('Predeclared numerical gate thresholds are incomplete or invalid.')
    if (metrics['n'] < limits['minimum_n'] or metrics['interval_coverage'] < limits['minimum_interval_coverage']
            or metrics['mae'] > limits['maximum_mae'] or metrics['rmse'] > limits['maximum_rmse']):
        raise ValueError('Prediction model failed its predeclared research-use validation gate.')
    if canonical(metrics) != canonical(validation.get('metrics')):
        raise ValueError('Submitted gate metrics disagree with actual held-out metrics.')
    return checked
