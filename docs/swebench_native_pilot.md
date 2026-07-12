# Native SWE-agent Pilot

`cgr-swebench-native-pilot` is a separate benchmark path that leaves the existing
`cgr-swebench-pilot` unchanged. Official SWE-agent is the top-level repository
agent: it deploys the frozen GitHub repository into SWE-ReX, conducts the model
conversation, submits the patch, and writes the authoritative `.traj`, `.patch`,
and `.pred` artifacts directly into durable benchmark storage.

The wrapper supplies only the frozen instance record, pinned configuration,
model endpoint, artifact retention, prediction integrity checks, and official
SWE-bench evaluation. It never creates a host repository checkout and never
extracts or reapplies the submitted patch.

Baseline uses:

```bash
export CGR_DRAFT_BASE_URL='http://127.0.0.1:8000/v1'
export CGR_DRAFT_API_KEY='cgr-aws-key'
export CGR_DRAFT_MODEL='Qwen/Qwen2.5-Coder-7B-Instruct'
export CGR_DRAFT_MAX_MODEL_LEN=16384
```

CGR mode uses:

```bash
export CGR_RUNTIME_BASE_URL='http://127.0.0.1:<configured-port>/v1'
export CGR_RUNTIME_API_KEY='<configured-key>'
export CGR_RUNTIME_MODEL='<configured-model-identifier>'
```

Generation and evaluation are explicit phases:

```bash
cgr-swebench-native-pilot --mode baseline \
  --instance-id astropy__astropy-7671 --generate-only

cgr-swebench-native-pilot --mode baseline \
  --instance-id astropy__astropy-7671 --evaluate-only

cgr-swebench-native-pilot --mode baseline \
  --instance-id astropy__astropy-7671 --generate-and-evaluate
```

`--compare` runs baseline and CGR with the same official SWE-agent configuration
and refuses to start if both endpoint/model identities are equal. A baseline
infrastructure failure stops the comparison before CGR mode. Normal official
`.pred` output with `model_patch: null` is retained as a completed unresolved
prediction; process crashes and missing predictions are infrastructure failures.

Each attempt is retained under
`benchmark-results/swebench-native-pilot-v1/<mode>/<instance>/attempt-NNN`.
Evaluation validates the frozen identity and generation-time prediction SHA-256,
then creates a same-filesystem `.json` hard link to the untouched `.pred` because
the official SWE-bench loader accepts only `.json` and `.jsonl` extensions. The
link and `.pred` are verified as the same file before the official harness runs;
no prediction bytes are copied, serialized, or reinterpreted.
