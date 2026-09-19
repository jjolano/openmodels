#!/bin/sh
# Self-hosting loop: index commaai/openpilot directly and serve a local verified blob mirror.
# Detection is pure git (anonymous ls-remote + blobless fetch) and LFS blobs need no auth, so
# GITHUB_TOKEN is only ever required to *publish* -- which is the public instance's job.
set -e

UPSTREAM=${UPSTREAM:-/data/openpilot}
INTERVAL=${INTERVAL:-3600}
LOCAL_MIRROR_LIMIT=${LOCAL_MIRROR_LIMIT:-40}

if [ ! -d "$UPSTREAM/.git" ]; then
  git clone --filter=blob:none --no-checkout https://github.com/commaai/openpilot "$UPSTREAM"
fi

while true; do
  git -C "$UPSTREAM" fetch --prune --filter=blob:none origin '+refs/pull/*/head:refs/remotes/pr/*'
  git -C "$UPSTREAM" fetch --prune --filter=blob:none origin '+refs/heads/*:refs/remotes/origin/*'
  python -m index.indexer \
    --repo "$UPSTREAM" --head origin/master \
    --out /data/index.json --previous /data/index.json \
    --blob-cache /data/public/blobs --mirror-local \
    --download-limit "$LOCAL_MIRROR_LIMIT"
  python web/render.py --index /data/index.json --out /data/public
  sleep "$INTERVAL"
done
