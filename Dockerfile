FROM python:3.12-slim@sha256:dd29372629eeba2dd003fd9e9d35a5b8236c44727875a0364254b5127af88e65

# git is the indexer's entire detection mechanism; git-lfs is deliberately NOT installed —
# we read 133-byte LFS pointers and fetch blobs over the batch API ourselves, so a clone
# never pulls gigabytes it doesn't need.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir fastapi==0.141.1 "uvicorn[standard]==0.52.3"

COPY index/ index/
COPY api/ api/
COPY web/ web/
COPY clients/ clients/
COPY runtime/ runtime/
COPY AGENTS.md ./
COPY LICENSE THIRD_PARTY_NOTICES.md ./

RUN useradd --create-home --uid 10001 openmodels \
    && mkdir -p /data \
    && chown openmodels:openmodels /data

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OPENMODELS_DATA=/data

# GIT_LFS_SKIP_SMUDGE keeps pointers as pointers even if git-lfs appears in the image later.
ENV GIT_LFS_SKIP_SMUDGE=1

EXPOSE 8000
USER openmodels
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
