#!/usr/bin/env bash
# Wait until GitHub Pages is actually SERVING this commit's code.
#
# Two traps, both hit on 16/08/2026:
#   1. `gh api repos/OWNER/REPO/pages --jq .status` reports the SITE's status, which
#      reads "built" from the PREVIOUS deployment while the new one has not started.
#      Polling it succeeds immediately and you then verify against the old build.
#      /pages/builds/latest is no better - it is the LEGACY build record and on this
#      repo it still named the previous commit long after the new content was live.
#      Neither API is trustworthy, so neither is used.
#   2. A content check must be able to come out NEGATIVE. Grepping for something that
#      was already present proves nothing. Pass a marker that exists ONLY in the new
#      code, and let this fail loudly if it never turns up.
#
# Usage: wait_pages.sh <url> <marker-string>
set -u
URL="$1"; MARKER="$2"

echo "waiting for $(git rev-parse --short HEAD) to be served at $URL"
for i in $(seq 1 40); do
  if curl -sL --max-time 30 "$URL?cb=$i$RANDOM" | grep -q -- "$MARKER"; then
    echo "SERVING the new build (found: $MARKER)"
    exit 0
  fi
  echo "  still the old build"
  sleep 12
done
echo "NOT SERVING after the wait - marker '$MARKER' never appeared"
exit 1
