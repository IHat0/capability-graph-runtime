# Unified research session v1

Pulsate now exposes one scientist entry point over the existing Phase 8
product handlers and the canonical Phase 3/4 contracts.

## Canonical path

```text
scientist question or reply
        |
        v
ResearchSession (durable conversation and lifecycle)
        |
        +--> ScientificObjective / CandidateResearchPlan
        |    canonical Phase 3 objective and plan, including explicit
        |    artifact-flow and verification requirements
        |
        +--> WorkflowGraphDefinition
             compiled by the canonical Phase 4 compiler, persisted once,
             and executed by the Phase 4 orchestrator through Phase 8 adapters
```

`StructuredScientificObjective` and `ScientificWorkflowPlan` remain internal
compatibility views for the established Phase 8 handler interfaces. They no
longer own a second execution graph. The `WorkflowGraphDefinition` stored in
the `ResearchCompilation` and `ScientificExecutionRecord` is the same graph
`ScientificObjectiveRuntime` persists and runs. A Phase 8 handler is selected
from the canonical node's assignment metadata; handler outcomes flow back into
the canonical orchestrator as artifacts, evidence, verification, cost, and
retry state.

## Scientist interaction

Start a session:

```bash
python -m cgr.pulsate_api.scientific_cli "YOUR QUESTION"
```

If the request is incomplete, the command succeeds with
`status=awaiting_clarification`, a `session_identifier`, and one or more
`next_questions`. It does not return a terminal checklist failure.

Reply to the same session:

```bash
python -m cgr.pulsate_api.scientific_cli \
  --session-id research-session-... \
  "YOUR ANSWER"
```

If the configured language model proposed one registered goal for unfamiliar
wording, Pulsate returns that proposal for review. Confirm it explicitly on the
reply that accepts the classification:

```bash
python -m cgr.pulsate_api.scientific_cli \
  --session-id research-session-... \
  --accept-intent-proposal \
  "Yes, use that research goal."
```

When a natural-language reply contains any explicit scientific controls, the
provider-neutral evidence interpreter returns a reviewable complete or partial
proposal. Every proposed value must be backed by a literal quote from a
specific scientist turn and pass a field-specific deterministic validator.
Confirmed partial evidence is accumulated across turns, and the next question
names only fields that are still missing. Confirm each proposal on the same
durable session:

```bash
python -m cgr.pulsate_api.scientific_cli \
  --session-id research-session-... \
  --accept-evidence-proposal \
  "I confirm the quoted values."
```

The proposal cannot affect the canonical objective, plan, graph, or execution
until that confirmation is recorded. Confirmed evidence is persisted in the
session and reused by every later revision.

Exact files or source identifiers may be supplied on either the initial turn
or a later reply using the existing CLI input/acquisition options. Later
evidence is merged with the session's earlier evidence.

A literal named input in ordinary language may also be proposed by the model.
The quoted name is used only when a deterministic public lookup returns one
unique identifier; zero or multiple identifiers fail closed. The acquired
input and its provenance remain behind the same scientist-confirmation gate.

CLI output is concise by default and omits the full canonical graph. Use
`--full-json` only when the complete diagnostic session payload is required.

Acquired or generated inputs also have a durable approval boundary. The CLI
flag `--approve-acquired-inputs` records that approval in the session request;
without it, the session remains `awaiting_approval` and cannot be executed by a
later command until approval is explicitly recorded.

The authenticated HTTP equivalents are:

- `GET /api/v1/research/capability`
- `POST /api/v1/research/sessions`
- `GET /api/v1/research/sessions/{session_identifier}`
- `POST /api/v1/research/sessions/{session_identifier}/reply`
- `POST /api/v1/research/sessions/{session_identifier}/execute`
- `GET /api/v1/research/sessions/{session_identifier}/scene?artifact_identifier=...`

The web workspace uses these same endpoints. Its primary view is no longer a
structured prerequisite form: it begins with one natural-language question,
renders the durable conversation, permits exact structure files on the first
turn or a reply, exposes the planned canonical graph state, runs the research,
and renders the stable scientist-facing result.

The scene endpoint projects coordinates and explicit bonds from an exact
tenant-owned artifact already attached to that session. It supports PDB,
PDBQT, MDL V2000 MOL/SDF, and MOL2 evidence. It does not generate coordinates,
infer missing elements, or synthesize bonds. Unsupported or incomplete scene
evidence fails closed while the research session itself remains resumable.

## No-invention contract

- Missing scientific controls remain null. Confirmed partial controls persist
  without becoming executable until the complete typed contract is satisfied.
- A semantic input reference cannot become execution-ready until its exact
  persisted artifact is attached.
- The language model may phrase a deterministic unresolved requirement as a
  question. It may propose one registered goal or a partial/complete evidence bundle
  only when each proposal contains exact quotations from scientist turns. No
  proposal affects planning until the scientist explicitly confirms it.
- Exact PDB, PubChem, SMILES, and InChI identifiers in a question or reply may
  be acquired through the same evidence-proposal boundary. A unique declared
  structural connection may be resolved by the audited deterministic resolver.
  Derived protein/partner inputs and reaction controls remain reviewable before
  use.
- If a reply does not resolve a requirement, the next prompt explains what
  could not be validated and gives exact acceptable evidence forms. The same
  unchanged checklist is never emitted silently.
- Any malformed or identity-changing model response falls back to the
  deterministic question writer.
- The current OpenAI-compatible provider boundary remains model-neutral. Qwen
  is a configuration choice, not a hard-coded architecture dependency.
- Browser authentication remains a host responsibility. The frontend calls
  `window.pulsateAccessTokenProvider` when installed by the authenticated shell;
  credentials are not stored in React state, URLs, local storage, or session
  storage.

## Migration boundary

The old `/api/v1/scientific/objectives/compile` and
`/api/v1/scientific/executions/...` routes remain available for compatibility.
The scientist CLI and web workspace use research sessions by default. The
canonical Phase 3/4 graph owns orchestration; Phase 8 compatibility objects are
adapter inputs only and do not define an independent workflow.

## Major A completion boundary

Major A is complete at this contract boundary:

- one durable scientist session owns question, replies, exact inputs,
  interpretation proposal, canonical compilation, execution, and result;
- unresolved requirements become follow-up questions rather than terminal
  prerequisite failures;
- language-model interpretation and question wording are provider-neutral and
  non-authoritative;
- the persisted canonical graph is the executed graph;
- the existing Phase 8 vertical slices are reached as canonical capability
  adapters instead of a second top-level system;
- scientist-facing results and exact-evidence molecular scenes are accessible
  from the CLI/API/web workspace;
- all scientific values remain scientist-provided or deterministically derived;
- natural-language follow-ups become quoted, validated, persisted evidence;
- the existing automatic structural resolver is connected to the unified
  session rather than operating as an isolated subsystem.
