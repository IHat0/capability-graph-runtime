FROM python:3.12.11-slim-bookworm AS runtime-dependencies

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/pulsate-venv

RUN python -m venv "${VIRTUAL_ENV}"
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

COPY requirements/pulsate-http-integration.lock /tmp/requirements/http.lock
COPY requirements/pulsate-production-security.lock /tmp/requirements/security.lock
COPY requirements/pulsate-production-quantum.lock /tmp/requirements/quantum.lock
RUN python -m pip install --require-hashes \
      -r /tmp/requirements/quantum.lock \
      -r /tmp/requirements/http.lock \
      -r /tmp/requirements/security.lock

COPY pyproject.toml /tmp/application/pyproject.toml
COPY src /tmp/application/src
RUN python -m pip install --no-deps --no-build-isolation /tmp/application \
    && python -m pip check


FROM python:3.12.11-slim-bookworm AS application

ARG SOURCE_REVISION=unknown
ARG APPLICATION_VERSION=0.1.0
LABEL org.opencontainers.image.title="CGR Pulsate API" \
      org.opencontainers.image.version="${APPLICATION_VERSION}" \
      org.opencontainers.image.revision="${SOURCE_REVISION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0 \
    PATH="/opt/pulsate-venv/bin:${PATH}"

RUN groupadd --system --gid 10001 pulsate \
    && useradd --system --uid 10001 --gid pulsate --home-dir /nonexistent pulsate \
    && mkdir -p /opt/pulsate /var/lib/pulsate /var/lib/pulsate-recovery /etc/pulsate \
    && chown -R 10001:10001 /var/lib/pulsate /var/lib/pulsate-recovery

COPY --from=runtime-dependencies /opt/pulsate-venv /opt/pulsate-venv

WORKDIR /opt/pulsate
USER 10001:10001
EXPOSE 8000

ENTRYPOINT ["python", "-m", "cgr.pulsate_api.deployment"]
