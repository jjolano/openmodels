FROM python:3.14-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d

# git is the indexer's entire detection mechanism; git-lfs is deliberately NOT installed —
# we read 133-byte LFS pointers and fetch blobs over the batch API ourselves, so a clone
# never pulls gigabytes it doesn't need.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY index/ index/
COPY web/ web/
COPY ci/ ci/
COPY openmodels/ openmodels/
COPY AGENTS.md ./
COPY LICENSE THIRD_PARTY_NOTICES.md ./

RUN useradd --create-home --uid 10001 openmodels \
    && mkdir -p /data \
    && chown openmodels:openmodels /data \
    && chmod +x ci/selfhost.sh

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OPENMODELS_DATA=/data

# GIT_LFS_SKIP_SMUDGE keeps pointers as pointers even if git-lfs appears in the image later.
ENV GIT_LFS_SKIP_SMUDGE=1

EXPOSE 8000
USER openmodels
CMD ["/app/ci/selfhost.sh"]
