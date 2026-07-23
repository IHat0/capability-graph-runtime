# Network-enabled IBM Runtime layer over the pinned Pulsate HTTP/scientific image.
ARG BASE_IMAGE=cgr-pulsate-http-integration:1.0.0
FROM ${BASE_IMAGE}

ARG BASE_IMAGE_NAME=cgr-pulsate-http-integration:1.0.0
ARG BASE_IMAGE_ID=unknown
ARG SOURCE_CHECKPOINT=unknown
LABEL org.opencontainers.image.title="CGR Pulsate IBM Runtime integration" \
      org.opencontainers.image.description="Isolated IBM Runtime extension over trusted local Pulsate preflight" \
      org.opencontainers.image.base.name="${BASE_IMAGE_NAME}" \
      io.pulsate.base.image.id="${BASE_IMAGE_ID}" \
      org.opencontainers.image.revision="${SOURCE_CHECKPOINT}"

USER root
COPY requirements/pulsate-ibm-runtime.lock /app/requirements/pulsate-ibm-runtime.lock
RUN python -m pip install --no-cache-dir --no-deps --require-hashes \
      -r /app/requirements/pulsate-ibm-runtime.lock \
    && python -m pip check \
    && python -c 'from importlib.metadata import version; assert version("qiskit") == "2.3.1"; assert version("qiskit-ibm-runtime") == "0.48.0"'

USER 10001:10001
WORKDIR /app
ENTRYPOINT ["python", "-m"]
