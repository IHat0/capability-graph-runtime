ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG BUILD_INPUT_SHA256
ARG SWEAGENT_COMMIT
ARG SWEREX_VERSION

LABEL org.cgr.tool-sandbox.schema="cgr.quantum-sweagent-tool-image/1.0.0" \
      org.cgr.tool-sandbox.build-input-sha256="${BUILD_INPUT_SHA256}" \
      org.cgr.tool-sandbox.sweagent-commit="${SWEAGENT_COMMIT}" \
      org.cgr.tool-sandbox.swerex-version="${SWEREX_VERSION}" \
      org.cgr.tool-sandbox.offline-bootstrap="true" \
      org.cgr.tool-sandbox.network-policy="none"

COPY requirements.lock /opt/cgr-tool-image/requirements.lock
RUN python3 -m pip install --no-cache-dir --disable-pip-version-check \
      -r /opt/cgr-tool-image/requirements.lock \
    && command -v bash \
    && command -v git \
    && command -v pipx \
    && command -v swerex-remote \
    && swerex-remote --version \
    && rm -rf /root/.cache /tmp/*

ENV PATH="/root/.local/bin:${PATH}" \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_INPUT=1

WORKDIR /
