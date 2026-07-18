# Quantum SWE-agent/OpenAI-compatible repair provider v1

## Status and scope

This is a production model-provider component validated locally through contract,
security, recovery, and integration tests. Its live scientific acceptance boundary is
the reviewed twelve-case LiH suite. A successful local test run does not claim that
Qwen repaired candidates: live Qwen/vLLM and Docker acceptance is performed on EC2.
Rung 8B will extend the measurement to repeated baseline-versus-CGR runs over the
full thirty-case suite.

The narrow acceptance set does not create a prototype architecture. Endpoint, agent,
prompt, request, result, trajectory, budget, telemetry, recovery, and replay
contracts are versioned and model-independent. The case list lives only in the
acceptance manifest; the provider contains no case-specific repair map.

## Trust boundary

The model and pristine SWE-agent are untrusted patch proposers. They receive public
experiment material, allowlisted candidate source, and either a generic baseline
request or a sanitized CGR directive. They cannot issue an authorization receipt.
Every proposed patch passes the existing `RepairPatch` policy, runs as a fresh
candidate attempt in the existing hostile sandbox, and is judged by the existing
trusted adjudicator. Thus model success, a clean agent exit, and a non-empty patch
are never authorization evidence.

SWE-agent remains at commit
`0f3acafacabc0def8cc76b4e48acb4b6cf302cb9`. The provider checks its commit and a
clean tree before and after each invocation. It invokes SWE-agent through its
official command interface and consumes official prediction/trajectory artifacts.
It does not patch the checkout, load `sitecustomize`, monkey-patch internals, or
intercept actions. CGR supervises the whole process, its lease, wall time, outputs,
and terminal artifacts.

## Endpoint and credentials

The default endpoint is `http://127.0.0.1:8000/v1` and the default model is
`Qwen/Qwen2.5-Coder-7B-Instruct`. Configuration can come from the checked provider
JSON or the `CGR_REPAIR_MODEL_*` environment variables. URL validation rejects
credentials, query strings, non-loopback names/addresses, and redirects. A health
probe reads `/models`, requires an exact model identity, and records the advertised
context length. Deterministic sampling uses temperature zero, top-p one, and an
explicit seed.

Only the orchestration process receives the API key, by environment-variable
reference. The key value is absent from command lines and every persisted contract.
The child environment is allowlisted and excludes cloud, IBM, Git, SSH, and host
credentials. Redaction removes explicit secrets, authorization headers, assignments,
and host-home paths before artifacts become portable.

## Two sandboxes

The SWE-agent tool sandbox is separate from candidate execution. Its container has a
bounded candidate workspace, public problem statement, no trusted material, no host
home, no Docker socket, dropped capabilities, `no-new-privileges`, resource limits,
and Docker network `none`. The host SWE-agent process alone can call the loopback
model endpoint.

Candidate execution continues to use the existing candidate sandbox: non-root,
network disabled, read-only root, dropped capabilities, bounded resources, and only
explicit source/input/output mounts. It receives neither provider artifacts nor the
model endpoint or credentials. The existing candidate and adjudication code remains
authoritative.

## Prompt and patch flow

`ModelRepairPrompt` is deterministically serialized and self-hashed. Source selection
uses complete allowlisted UTF-8 files or fails explicitly; constraints and identities
are never silently truncated. Live endpoint context capacity must accommodate the
estimated prompt plus its output reservation. CGR mode includes sanitized findings,
invariants, and prior public failure categories. Baseline mode contains only the
public task, candidate source, generic public invariants, and equal budgets; it does
not contain CGR labels, finding codes, or diagnosis-derived invariants.

Prompt inspection rejects secret values, trusted numerical assignments, trusted
hash assignments, and trusted evidence paths. Trusted outputs, Hamiltonians,
receipts, valid-control source, and deterministic-provider patches are not mounted or
serialized.

Patch extraction accepts only an official SWE-agent prediction artifact containing a
valid unified diff. Extraction occurs against an isolated copy of the exact source
manifest and runs `git apply --check` before application. Empty, missing, malformed,
truncated, binary, traversing, absolute, stale, and no-op patches fail closed. Changed
files are converted deterministically to whole-file structured edits, then the
existing patch-policy engine enforces allowed paths, quotas, prohibited content,
candidate identity, prior patch identities, and prior source states.

## Durable invocation and recovery

Before launch the provider atomically persists its self-hashed request, identities,
budget reservation, sequence, state, heartbeat, and lease. State transitions are:

`created -> request_persisted -> launching -> running -> response_persisted ->
patch_extracted -> completed`

Failures end in `interrupted`, `retryable_failure`, or `terminal_failure`. Recovery
verifies durable evidence, refuses a duplicate while a lease is active, never treats
a partial response or patch as complete, preserves the interrupted directory, and
uses a new invocation identifier for a charged retry. A completed invocation is
idempotently replayed and cannot be duplicated. Crash-injection tests cover every
state boundary. The provider automatically continues within its configured retry
budget when the controller process survives; after a controller restart, the same
durable attempt call verifies and advances from the recorded state rather than
reusing partial output.

Each invocation enforces model-call, input/output/total-token, tool-command,
tool-output, file-read/file-change, patch-size/line, wall-time, retry, and overall
repair-time limits. Exhaustion creates a terminal result; there is no deterministic
repair fallback.

## Evidence, telemetry, and replay

Endpoint, agent, prompt, request, result, and trajectory contracts are immutable,
self-hashed JSON. Ordered JSONL telemetry records non-sensitive lifecycle events,
identities, usage, tool counts, timings, patch identity, and status. Raw official
artifacts remain in private invocation storage. Only UTF-8 artifacts that pass
redaction are copied into the portable trajectory manifest, where every artifact and
the ordered manifest are hashed.

Repair replay additionally verifies provider state order, cross-contract hashes,
the completed result, trajectory, telemetry sequence, and equality between the
provider patch and the controller attempt patch. It performs no model or candidate
execution.

## Comparative acceptance

The reviewed manifest runs twelve cases in baseline and CGR modes with the same
model, sampling, pristine agent, source, tool and candidate sandboxes, patch policy,
maximum proposals, token reservation, and wall-time reservation. It reports actual
and unused consumption. The valid control must authorize at attempt zero without a
provider call. Six named cases run twice with the same seed; repeatability requires
consistent safety and authorization decisions, while allowing trajectory text,
token counts, and wall time to vary.

Safety gates are independent of effectiveness. Any false authorization, patch-policy
bypass, trusted exposure, candidate network/model access, missing/skipped case, or
replay failure fails acceptance. CGR must authorize at least eight broken cases,
beat baseline by at least two, and authorize the composite case. Provider/model
failure is recorded but remains safe when the candidate stays unauthorized.

## Operations and remaining limitations

Use `cgr-quantum-candidate-repair provider-check` before live execution, the
acceptance run script to create machine-readable evidence, and the verification
script for summary and replay checks. The health check also verifies the pinned
QuixBugs commit, tool image, executable, writable evidence root, sandbox arguments,
and forbidden credential forwarding.

Local Windows validation does not exercise the Linux Docker isolation path or a live
vLLM endpoint. Hosted non-loopback endpoints and alternate SWE-agent commits require
future explicit policies/compatibility declarations; v1 deliberately rejects them.
Model quality on the twelve cases remains an EC2 acceptance result, not a property
inferred from mocks. The component is therefore production-oriented and fail-closed,
but the complete platform is not described as production-ready. Rung 8B will execute
and tune repeated full thirty-case comparisons without changing the trust boundary.

## Offline-ready tool image and bootstrap correction

The first EC2 provider acceptance exposed a systemic bootstrap failure before any
model call. Pinned SWE-agent enters `SWEEnv._init_deployment()`, calls
`DockerDeployment.start()` from SWE-ReX 1.4.0, and obtains its container command from
`DockerDeployment._get_swerex_start_cmd()`. When `swerex-remote` is absent, that
official command falls back to `python3 -m pip install pipx`, `pipx ensurepath`, and
`pipx run swe-rex`. With the original Docker network mode `none`, PyPI was correctly
unreachable and the container terminated. The previously selected
`tools/edit_anthropic/install.sh` would subsequently have run two additional pip
installs, so merely adding `pipx` would not have made the lifecycle offline-ready.

The supported correction follows upstream's documented custom-image approach. The
dedicated image in `docker/quantum-sweagent-tool` installs the exact SWE-ReX 1.4.0
runtime and its `swerex-remote` entry point at image build time. Build-time dependency
retrieval is a separately controlled boundary. Runtime creation uses the exact image
ID and pull policy `never`; no runtime dependency download is permitted. The provider
uses only official tool bundles whose startup scripts do not
install packages (`registry`, `search`, `windowed`, and `review_on_submit_m`) plus
SWE-agent's supported Bash tool for edits. No upstream file or action is modified.

The build requires a caller-supplied base image by immutable repository digest. It
hashes that identity together with the Dockerfile, requirements lock, and build
contract, labels the result, records provenance, and produces a provider configuration
containing the resulting exact `sha256:` image ID. Runtime validation compares the
local image ID and all labels with the versioned `ToolSandboxImageDescriptor`; a tag
is only a build-time convenience and is never authoritative.

The next EC2 preflight proved that the preinstalled runtime starts, but also exposed
that literal `--network=none` cannot carry SWE-ReX's required host-to-container TCP
control channel. Pinned `DockerDeployment.start()` always publishes its selected host
port to container port 8000. Its supported `docker_args` field can select a custom
network, but the pinned configuration has no host-bind-address field and emits
`-p <port>:8000` itself.

The narrow lifecycle adapter therefore creates a unique user-defined bridge with
`docker network create --internal` and the bridge driver option
`com.docker.network.bridge.host_binding_ipv4=127.0.0.1`. It passes the owned network
name and an ownership label through official `DockerDeploymentConfig.docker_args`.
An internal Docker network intentionally provides no usable published host port, so
CGR supplies the selected loopback endpoint with an invocation-owned TCP proxy. The
provider does not subclass, patch, or rewrite SWE-ReX and does not intercept agent
actions. Docker inspection must show `Internal=true`, the expected ownership nonce,
exactly one container network, and no published container control binding. The default
bridge, host network, public bindings, extra networks, and substituted resources are
rejected.

Pinned SWE-ReX 1.4.0 starts its Docker process and immediately constructs a loopback
`RemoteRuntime`; its readiness loop can therefore issue `GET /is_alive` before CGR
has discovered the new container. Selecting a free port and closing the selection
socket left a nondeterministic interval in which that first probe received connection
refused. The v2 startup handshake instead binds an ephemeral `127.0.0.1` listener
before official deployment launch, passes that still-owned port to SWE-ReX, and keeps
the listener gated until Docker inspection verifies the ownership nonce, exact image,
network identity, sole internal network, absent public binding, and internal IPv4
destination. Early official health probes can reach the reserved listener but cannot
reach any unverified destination.

After activation, CGR polls `/is_alive` through the proxy under the original bounded
startup deadline. Only connection/readiness failures are retried; every poll rechecks
the immutable container destination. Container exit, identity substitution, missing
internal IP, public exposure, network-policy changes, proxy failure, and deadline
expiry fail immediately with layer-specific classifications. The lifecycle contract
records the reserved port, bind/listener, official activation and first-request times,
verified destination state, readiness attempts and elapsed time, deadline, terminal
state, and cleanup results. A passing artifact must prove listener readiness preceded
both official client activation and the first official control request. The proxy
remains live until the official child has completed `deployment.stop()` and `/close`;
cleanup then removes containers, the proxy, and finally the owned network.

Provider health waits for the official ready state, runs a harmless shell command,
modifies a disposable file, and proves that direct external IP, external hostname,
PyPI, Qwen, credentials, and the Docker socket remain unavailable. Its self-hashed
health artifact binds the image, versioned network policy, sanitized network identity,
ownership nonce, allocated port, probes, and separate container/network cleanup
results. Failures distinguish control-network creation or policy defects, public port
exposure, unreachable runtime control, detected egress/model access, and container or
network cleanup failures from dependency bootstrap failures.

Network state is persisted privately before SWE-agent starts. Normal completion,
timeout, interruption, and failure all remove labeled containers and then the network.
Retry recovery reads that state and removes a resource only when its exact Docker ID,
schema label, and ownership nonce agree. Foreign or substituted networks and
containers are never removed; they instead cause a fail-closed cleanup classification.

### Windowed-tool command-document configuration

Pinned SWE-agent 1.1.0 declares `scroll_up` and `scroll_down` in
`tools/windowed/config.yaml` with docstrings containing the exact `{WINDOW}` field.
`ToolConfig.model_post_init()` passes `agent.tools.env_variables` to
`generate_command_docs()`, which formats those docstrings. Upstream's official
windowed configurations and `tools/windowed/install.sh` use the default value 100;
upstream converts the runtime value to an integer but declares no narrower range.
The repair provider therefore retains `tools/windowed` and supplies the deterministic
value `WINDOW: 100` through the supported `agent.tools.env_variables` field. Retaining
the bundle preserves its bounded file navigation commands; removing it would narrow
the provider workflow without addressing the supported upstream configuration.

Before SWE-agent launch, CGR resolves each configured bundle beneath the pristine
pinned `tools` root, hashes its `config.yaml`, extracts command-doc placeholder names
without formatting their values, and checks them against the allow-listed schema.
`WINDOW` must be an actual integer from 1 through 1000; missing values are classified
as `tool_configuration_template_missing_variable`, while unknown placeholders and
malformed or unbounded values fail as
`sweagent_tool_template_configuration_failure`. The sanitized, self-hashed validation
artifact contains only bundle identities, hashes, required names, and validated
allow-listed values, with zero model requests, tokens, and trusted-evidence exposure.
Its identity is cross-linked by the overlay-derived agent configuration, agent
descriptor, provider request, smoke evidence, acceptance evidence, and replay checks.
No Docker image input changes because this is an agent configuration value.

Comparative acceptance performs this preflight before materializing any baseline or
CGR case. Failure creates a completed fail-closed preflight report with zero cases and
zero model tokens. A separate `syntax-error` smoke then uses the real provider-neutral
controller, candidate sandbox, patch policy, agent, endpoint, and image. It must show
positive model calls and tokens plus replayable official evidence. The full run
accepts the smoke only when its self-hash and provider, endpoint, agent, image, and
network-policy identities exactly match the current configuration.

Operator order is therefore: build the image, run the offline tool check, run complete
provider health, run the one-case provider smoke, and only then run comparative
acceptance. All scripts require an explicit `CGR_PYTHON` that imports CGR and Pydantic;
they cannot fall back to an unsuitable system interpreter or print verification
success after an import or replay failure.
