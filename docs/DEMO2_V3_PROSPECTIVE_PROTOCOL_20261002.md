# Demo 2 v3 prospective evidence protocol

Status: user-approved policy applied to isolated validation. The 2026-10-03
research-grade readiness amendment below supersedes the former universal
qualification freeze requirement. The final held-out investigation has not run
at this engine-freeze review.

## Authority and cutoff

Authority: the user's challenge protocol supplied on 2026-10-02. No candidate
outcome search was used to select or implement this boundary.

The exact configured cutoff is **2006-12-01T23:59:00Z**. The date is also recorded
as 2006-12-01, but the timestamp takes precedence. Dates without an exact time
are tested at the latest possible time on that day; year-only dates are tested
at the latest possible time in that year. A publication dated only on the
cutoff day cannot establish availability before the minute-precision boundary.
The system does not invent a publication hour.

This is a prospective-style evidence challenge, not a claim that modern model
weights, databases or general scientific knowledge are literally frozen in 2006.

## Permitted and quarantined information

Candidate-specific original measurements may qualify only when their historical
availability, exact identity and endpoint are established. Relevant classes are
physicochemical properties, ionization/solubility, plasma binding, metabolism or
clearance, preclinical ADME, pharmacokinetic measurements and development dosing.
Existing native PBPK contracts still determine which qualified measurements can
supply an input; historical eligibility never overrides endpoint compatibility.

Candidate-specific clinical safety/efficacy conclusions, physiological outcome
observations and retrospective mechanisms or failure explanations are excluded
regardless of publication date. Compound-specific observations cannot be admitted
under a general-physiology, annotation, tissue-expression or pathway-model label.

General physiology, target annotations, pathway/model knowledge and activity data
for unrelated compounds may be modern. Their modern/unknown historical status
must be disclosed. Similar-compound activity is hypothesis evidence, not the
candidate's measured potency. Self and parent-connectivity matches are excluded
before the bioactivity retrieval step. Docking energies are never converted to
potencies.

Clean, projected molecular identity/graph and protein/intended-target records are
foundational inputs. They do not import clinical narratives, and their use is not
asserted to prove historical availability. This exception does not admit later
candidate mechanisms, activity conclusions or ADME measurements.

## Original measurement trace

A modern wrapper's old citation date is insufficient. Each eligible candidate
measurement requires a reviewed `original_primary_record` trace containing:

- original URL, record identifier and preserved source hash;
- reviewer and the basis for classifying it as original primary data;
- original publication date, precision and exact preserved date value;
- release date/time and exact release evidence when supplied;
- an exact pointer to the original measurement and its units;
- an exact pointer to the original date metadata;
- the datum's identity, endpoint/species context, value, units and evidence type.

The wrapper value, original value, original unit and original date are replayed
against preserved bytes. Eligibility uses the original record's availability,
not the modern wrapper's publication date. Only the specified numerical datum is
admitted; surrounding modern interpretation is not trusted wholesale.

The extractor now supports exact JSON, text/HTML spans, bounded unambiguous
HTML/CSV/TSV table cells and reviewed PDF spans. PDF admission requires agreement
of two independent extraction libraries and a preserved, source-bound rendered
page review with exact literals. Merged/nested or ambiguous tables, uncertain
extraction and unverified OCR remain quarantined. Scripts/styles are not evidence.
A reviewer labeling converted JSON as primary does not make it primary; source
designation, endpoint interpretation and dating still require scientific curation.
Hash replay establishes evidence integrity, not experimental truth or independent
scientific certification.

An unrelated merged table no longer invalidates an explicitly indexed separate
unmerged table. The selected table must still be unmerged; prose cannot bypass
merged-table refusal. Full-cell and unique full-numerical-token replay may expose
a reviewed scalar only with its original header/statistic/context retained by
the evidence contract. Neither a mean nor a deviation is chosen automatically.
Partial exponents, substrings of larger numbers and layout-joined digits refuse.

Where wrapper bytes differ from original bytes, explicit wrapper provenance is
mandatory: its kind, exact hash, record, URL/discovery relationship where relevant,
reviewer, purpose and `not_primary` declaration. A local extraction receipt is a
wrapper, not an original experimental publication. Both byte sets are retained.
The original identity/species/endpoint/context anchors, value, unit and exact
date evidence must independently replay. Total-body, blood, intrinsic or tissue
clearance is not silently treated as hepatic plasma clearance.

No held-out candidate-specific source was retrieved to implement this policy. Any future
dossier curation must use pre-cutoff numerical sources, avoid outcome narratives,
retain all permissible conflicts, and never choose inputs to obtain a desired
assessment.

## Current validation boundary

Synthetic tests validate the policy and tamper rejection; they are not measured
ADME evidence. Unrelated live browser investigations validate real Qwen,
acquisition, docking, artifact integrity, UI and session reopening. The two
established SBML validation models demonstrate actual numerical perturbations,
not qualified Human drug-response or organ-risk predictions.

Actual unrelated Human native executions now use two retained original-primary
binding measurements plus separately disclosed numerical controls. Other ADME
inputs in those controls are not promoted to measured or biologically qualified
values. All six existing native bridge capabilities execute and independently
replay. Conflict sensitivity keeps every highest-precedence alternative, with
no nominal selection. Exhaustive discrete coverage is distinguished from bounded
sampling; population identity, native compartment path and integration window
must agree before outputs are pooled. Conditional exposure curves retain their
case/subject/population hashes through functional-transfer evaluation. Neither
those curves nor their ranges establish a physiological effect or clinical risk.

The broader target-hypothesis path is integrated with existing docking and blocking
verification. It prefers an exact active-neighbour co-crystal; a separate
assay-qualified reference may instead establish a pocket on the same exact
target/species. That reference's activity is never candidate activity. Entire
deposits containing candidate/parent connectivity are excluded before activity
or coordinate retrieval. Query/structure budgets and every refusal are retained.
An unrelated real browser session now computed two newly nominated alternatives,
refused a third and verified 182 exported hashes plus reopening. This is positive
structural proof, not full Human drug-response validation or calibrated selectivity.

## Research-grade Human qualification authority

The user explicitly authorized continued internal research validation without
an external independent reviewer. External review is a later qualification step,
not a prerequisite for computational engineering. This does **not** authorize
clinical validity, regulatory qualification, Human safety prediction or describing
engineering checks as independent scientific certification.

The research route requires a versioned, hash-bound audit package describing the
exact model/equations, parameter provenance, assay-to-model translation, units,
domain, uncertainty, benchmarks, discrepancies, sensitivity, reproducibility,
negative cases, progression/refusal rules and limitations. Eight critical gates
must pass at their declared research scope:

1. Model/source provenance.
2. Input/unit verification.
3. Observed biological benchmark agreement within a predeclared, justified budget.
4. Supported applicability domain.
5. Compatible exposure/activity concentration bases.
6. Meaningful bounded uncertainty.
7. No clinical inference beyond the model.
8. Critical-failure refusal.

Solver agreement is not an observed biological benchmark. Benchmark observations
require preserved original-primary context/time/value/unit anchors and fresh model
replay; repeated points cannot masquerade as independent observations. Native
parameter meaning and molecular species require an explicit calibrated transfer,
not accession/name matching. Binding Ki/Kd or docking scores are not arbitrarily
converted into a fractional change in a kinetic rate.

At native execution a candidate fractional-activity transfer additionally
requires a physically defined parameter unit, positive baseline and replayable
equation path to a reported output. Source-bound AST connectivity is a necessary
interface guard, not evidence that an effect occurs, that a mapping is calibrated
or that a Human response is biologically validated. Original mass-action binding
models require their own native species/kinetic semantics; they are not made
compatible by relabeling them as fractional-activity models. Published prediction
figures, secondary non-curated encodings and numerical solver agreement do not
qualify the observed-benchmark gate.

Original observations may use different compatible physical time/value units
from native outputs. Their original numerical values and both unit anchors must
replay before finite dimension-checked conversion. A partial conversion, arbitrary
factor or mass/molar dimension change refuses. This does not establish a free/
total concentration mapping or turn aggregate statistics into individual curves.
Modern unrelated benchmark observations remain separate from historical
candidate-specific input admission.

Candidate transfer additionally requires native SBML component and full unit
consistency, with physical reaction/assignment scales replayed. Undeclared rate
dependencies, missing dimensions and hour/second scale mismatches refuse; no
source equations or units are silently repaired. Numerical controls remain
explicit controls, not Human drug-response qualification.

Research progression criteria remain explicit, versioned and scoped to reviewed
nonclinical endpoints. They require original-source justification, exact model
revision/units/domain and all qualified uncertainty cases. Any critical missing
gate or required execution refusal produces **INSUFFICIENT EVIDENCE**, even if a
partial calculation matches a concern/rejection criterion; the partial signal is
retained in the audit. No default therapeutic dose, universal biological threshold
or automatic advancement is introduced.

## Research-grade readiness amendment — 2026-10-03

The user explicitly replaced the universal-Human qualification freeze condition
with a bounded research demonstration. A universal ADME library, a qualified QSP
model for every hypothesis, universal progression thresholds and an external
reviewer are not prerequisites. None of the execution/admission guards above is
weakened by that scope change.

Candidate-specific permissible original ADME/PK may populate a separately frozen
dossier after the general engine is committed. Missing sufficient PBPK inputs
produce an explicit refusal, not a guessed regimen or clearance. Supported
target/structure computations continue independently. Exposure-to-mechanism
progresses only with compatible activity and an applicable reviewed transfer.
No applicable predeclared assessment criterion means INSUFFICIENT EVIDENCE.

The general engine must be reviewed, regression-tested, hash-frozen, selectively
committed and pushed before the separately authorized single final execution.
One completed valid result cannot trigger tuning or a second scientific attempt.
Older frozen results remain unchanged and are checked by hash only. No historical
reveal, production modification or IBM submission is authorized.

This engine is research-grade and computationally auditable. It is not
independently clinically qualified, regulator-qualified, or validated as a
universal predictor of human safety. See
`DEMO2_V3_RESEARCH_GRADE_FREEZE_20261003.md` for the bounded engine freeze.
