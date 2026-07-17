FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    PYSCF_TMPDIR=/tmp \
    XDG_CACHE_HOME=/tmp/cache

RUN groupadd --system --gid 10002 candidate \
    && useradd --system --uid 10002 --gid candidate --home-dir /nonexistent candidate \
    && mkdir -p /candidate /input /output \
    && chown candidate:candidate /output

COPY requirements/quantum-preflight.lock /opt/candidate/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes \
      -r /opt/candidate/requirements.lock

USER 10002:10002
WORKDIR /candidate
ENTRYPOINT ["python"]
