# Pulsate single-node production deployment v1

## Supported model

The only supported v1 model is one immutable Pulsate API container on one
node. It uses three externally managed mounts: a read-only trusted
configuration root, a writable application-data root, and a writable recovery
root. TLS and the public network boundary belong to an external reverse proxy.
This model does not provide high availability, multi-region operation,
automatic failover, zero-downtime deployment, online migration, or online
consistent backup.

## Image and process

Build `Dockerfile` with an immutable source revision and deploy the resulting
digest. It uses the CI Python 3.12 line, hash-locked runtime dependencies, the
committed source, UID/GID 10001, a deterministic work directory, and an
exec-form Python entry point. The root filesystem supports read-only operation;
only the data/recovery mounts and bounded `/tmp` tmpfs are writable.

`cgr-pulsate-serve` runs the existing offline production configuration check
before Uvicorn may bind, then validates application data storage. Application
lifespan initializes and closes JWT/JWKS, grants, audit, catalogue, and
repository services. One worker is supported because the current run
coordinator is single-process; another count fails closed.

## Compose procedure

Create an operator-owned environment file from
`deployment/production.env.example`, without committing secrets. Set an
immutable image reference and absolute configuration, data, and recovery mount
paths. Pre-create the writable roots for UID/GID 10001 with restrictive host
permissions; the container does not change external ownership. Then run:

```text
docker compose -f compose.production.yml config
docker compose -f compose.production.yml up -d
```

The manifest drops all capabilities, enables `no-new-privileges`, bounds
processes/memory/CPU, has no Docker socket, and gives shutdown 40 seconds. Its
health command verifies `/live` and `/ready`; the reverse proxy must also use
`/ready` before traffic. Readiness covers security, catalogue, and repositories.
The manifest's 2 CPU, 2 GiB, and 256-process values are starting operational
limits, not measured capacity claims.

Run `cgr-pulsate-config-check` before promotion. Start, require `/live`, then
require `/ready`. Stop with the normal container signal so lifespan cleanup can
finish. A forced kill is an operational failure and requires state validation.
Never place credentials, keys, grants, audit state, molecular data, real
catalogues, or JWKS in an image or Compose file.
