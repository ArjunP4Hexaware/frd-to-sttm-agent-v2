#!/usr/bin/env bash
# Build the frontend, push, and pull it into the Databricks Git folder.
#
#     scripts/deploy_to_workspace.sh
#
# WHY THIS EXISTS
# ---------------
# The workspace folder is a Databricks GIT FOLDER (2026-08-27, Arjun's call):
# it reflects pushes to GitHub, not `databricks sync`. Git folders have NO
# BUILD STEP — they check out exactly what is committed — and
# review_app_react/backend/app.py serves `frontend/dist`. So the built
# frontend has to be IN the commit, and the one quiet way to break the
# deployed app is to change a .tsx and push without rebuilding: the workspace
# then serves a stale UI with no error anywhere.
#
# This script removes that failure mode by making build-commit-push-pull one
# command. Run it instead of `git push` whenever the UI changed.
#
# ON THE HASH CHURN, so nobody "fixes" it: Vite renames its bundles on every
# build even when the output is byte-identical (verified 2026-08-27 — the
# sha256 of the JS matched across two builds while the filename changed). So
# this script commits the build ONLY when the bundle CONTENT actually differs,
# and otherwise restores the committed one. Without that, every deploy would
# add a meaningless commit.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ID="${STTM_WORKSPACE_REPO_ID:-1757772353604827}"
BRANCH="${STTM_WORKSPACE_BRANCH:-staging}"

echo "==> building the frontend"
( cd review_app_react/frontend && npm run build >/dev/null )

# Content-compare rather than trust the filenames (see the note above).
content_changed() {
  local before after
  before=$(git ls-tree -r HEAD --name-only review_app_react/frontend/dist \
           | grep -E '\.(js|css)$' | sort \
           | while read -r f; do git show "HEAD:$f" | shasum -a256 | cut -d' ' -f1; done \
           | sort | shasum -a256)
  after=$(find review_app_react/frontend/dist -name '*.js' -o -name '*.css' \
          | sort | while read -r f; do shasum -a256 "$f" | cut -d' ' -f1; done \
          | sort | shasum -a256)
  [ "$before" != "$after" ]
}

if content_changed; then
  echo "==> bundle content changed — committing the build"
  git add review_app_react/frontend/dist
  git commit -q -m "Rebuild the frontend for the Databricks Git folder"
else
  echo "==> bundle content unchanged (filename hashes only) — discarding churn"
  git checkout -- review_app_react/frontend/dist 2>/dev/null || true
  git clean -fdq review_app_react/frontend/dist
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "!! working tree has other uncommitted changes:"
  git status --short | sed 's/^/     /'
  echo "   commit or stash them, then re-run. Nothing was pushed."
  exit 1
fi

echo "==> pushing $BRANCH"
git push -q origin "$BRANCH"

echo "==> pulling the Databricks Git folder to $BRANCH"
databricks repos update "$REPO_ID" --branch "$BRANCH" >/dev/null

LOCAL=$(git rev-parse --short=12 HEAD)
REMOTE=$(databricks repos get "$REPO_ID" -o json | python3 -c "import json,sys; print((json.load(sys.stdin).get('head_commit_id') or '')[:12])")
echo
echo "   local  $LOCAL"
echo "   folder $REMOTE"
[ "$LOCAL" = "$REMOTE" ] && echo "   in step — the app can be started" \
                         || { echo "   MISMATCH — the folder did not advance"; exit 1; }
