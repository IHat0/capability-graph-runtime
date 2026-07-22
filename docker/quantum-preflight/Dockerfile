FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    PYSCF_TMPDIR=/tmp \
    XDG_CACHE_HOME=/tmp/cache \
    PYTHONPATH=/app/src

RUN groupadd --system --gid 10001 preflight \
    && useradd --system --uid 10001 --gid preflight --home-dir /nonexistent preflight \
    && mkdir -p /app /output \
    && chown preflight:preflight /output

COPY requirements/quantum-preflight.lock /app/requirements/quantum-preflight.lock
RUN python -m pip install --no-cache-dir --require-hashes \
      -r /app/requirements/quantum-preflight.lock

COPY src /app/src
COPY tests /app/tests
COPY benchmark-manifests /app/benchmark-manifests
COPY pyproject.toml /app/pyproject.toml

RUN unexpected_metadata="$(find /app/src \
      \( -type d \( -name '*.egg-info' -o -name '*.dist-info' \) \
      -o -type f -name '*.pth' \) -print)" \
    && if [ -n "$unexpected_metadata" ]; then \
         printf 'Unexpected Python packaging metadata under /app/src:\n%s\n' \
           "$unexpected_metadata" >&2; \
         exit 1; \
       fi \
    && python -m pip check

USER 10001:10001
WORKDIR /app
ENTRYPOINT ["python", "-m", "cgr.quantum_preflight.cli"]
