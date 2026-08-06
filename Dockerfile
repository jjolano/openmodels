FROM python:3.12-slim

# git is the indexer's entire detection mechanism; git-lfs is deliberately NOT installed —
# we read 133-byte LFS pointers and fetch blobs over the batch API ourselves, so a clone
# never pulls gigabytes it doesn't need.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir fastapi==0.141.1 "uvicorn[standard]==0.38.0"

COPY index/ index/
COPY api/ api/
COPY web/ web/
COPY clients/ clients/
COPY AGENTS.md ./

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OPENMODELS_DATA=/data

# GIT_LFS_SKIP_SMUDGE keeps pointers as pointers even if git-lfs appears in the image later.
ENV GIT_LFS_SKIP_SMUDGE=1

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
