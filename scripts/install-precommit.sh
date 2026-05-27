#!/usr/bin/env bash
# Copy this repo's .gitleaks.toml + .pre-commit-config.yaml to every
# other costaff-* sibling repo, then `pre-commit install` in each.
#
# Idempotent. Re-run after editing .gitleaks.toml here to propagate.
#
# Usage:
#     bash tools/install-precommit.sh           # all sibling repos
#     bash tools/install-precommit.sh costaff-channel-telegram   # one repo

set -euo pipefail

THIS=$(cd "$(dirname "$0")/.." && pwd)
ROOT=$(cd "$THIS/.." && pwd)

# Each entry: "<sibling-dir-relative-to-ROOT>"
ALL_REPOS=(
  costaff-channel/costaff-channel-webchat-oss
  costaff-channel/costaff-channel-telegram
  costaff-channel/costaff-channel-webchat-enterprise
  costaff-channel/costaff-channel-discord
  costaff-channel/costaff-channel-line
  costaff-channel/costaff-channel-slack
  costaff-channel/costaff-channel-chatbot
  costaff-agent/costaff-agent-business-analysis
  costaff-agent/costaff-agent-coding
  costaff-agent/costaff-agent-database
  costaff-agent/costaff-agent-twinkle-hub
  costaff-agent/costaff-agent-wrenai-oss
  costaff-agent/costaff-agent-template
  costaff-agent/costaff-agent-builder
  costaff-agent/costaff-agent-notion
  costaff-agent/costaff-agent-gmail
  costaff-agent/costaff-agent-google-calendar
  costaff-agent/costaff-agent-google-drive
  costaff-agent/costaff-agent-rss-feed
  costaff-agent/costaff-agent-web-search
  costaff-agent/costaff-agent-apmic-privai
  costaff-agent/costaff-agent-human-resource
  costaff-agent/costaff-agent-medical
  costaff-agent/costaff-agent-nutrition
  costaff-agent/costaff-agent-terraform
  costaff-agent/costaff-agent-kubernetes
)

filter="${1:-}"
ok=0
skipped=0
missing=0

for rel in "${ALL_REPOS[@]}"; do
  name=$(basename "$rel")
  if [ -n "$filter" ] && [ "$filter" != "$name" ]; then continue; fi
  d="$ROOT/$rel"
  if [ ! -d "$d" ]; then
    echo "  · $name: (not cloned locally — skip)"
    missing=$((missing+1))
    continue
  fi
  if [ ! -d "$d/.git" ]; then
    echo "  · $name: (not a git repo — skip)"
    skipped=$((skipped+1))
    continue
  fi

  cp "$THIS/.gitleaks.toml" "$d/.gitleaks.toml"
  cp "$THIS/.pre-commit-config.yaml" "$d/.pre-commit-config.yaml"

  # Clear any stale local hooksPath (we hit this from a previous workspace
  # layout) then install.
  (
    cd "$d"
    git config --local --unset core.hooksPath 2>/dev/null || true
    pre-commit install >/dev/null 2>&1
  )
  echo "  ✓ $name"
  ok=$((ok+1))
done

echo ""
echo "Done. ok=$ok  skipped=$skipped  missing=$missing"
echo ""
echo "Remind contributors:"
echo "  pip install pre-commit && pre-commit install"
echo "  (inside their freshly cloned repo)"
