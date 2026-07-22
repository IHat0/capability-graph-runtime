# Pulsate application/integration layer. The trusted scientific runtime remains
# exclusively defined by cgr-quantum-preflight and quantum-preflight.lock.
ARG BASE_IMAGE=cgr-quantum-preflight:1.0.0
FROM ${BASE_IMAGE}

ARG BASE_IMAGE_NAME=cgr-quantum-preflight:1.0.0
ARG BASE_IMAGE_ID=unknown
ARG SOURCE_CHECKPOINT=unknown
LABEL org.opencontainers.image.title="CGR Pulsate HTTP integration" \
      org.opencontainers.image.description="HTTP integration layer over the trusted quantum-preflight scientific image" \
      org.opencontainers.image.base.name="${BASE_IMAGE_NAME}" \
      io.pulsate.base.image.id="${BASE_IMAGE_ID}" \
      org.opencontainers.image.revision="${SOURCE_CHECKPOINT}"

USER root
COPY requirements/pulsate-http-integration.lock /app/requirements/pulsate-http-integration.lock
RUN python -m pip install --no-cache-dir --require-hashes \
      -r /app/requirements/pulsate-http-integration.lock \
    && python -m pip check

# Preserve the base image's /app source, tests, manifests, deterministic
# environment variables, and unprivileged scientific execution boundary.
USER 10001:10001
WORKDIR /app
ENTRYPOINT ["python", "-m"]
