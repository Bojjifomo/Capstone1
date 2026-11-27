#!/usr/bin/env bash
# Require the `test` status check (strict) on main of a repo.
# Usage: ci/protect_main.sh <owner/repo>
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "usage: $0 <owner/repo>" >&2
  exit 1
fi

gh api -X PUT "repos/$1/branches/main/protection" --input - <<'JSON'
{
  "required_status_checks": { "strict": true, "contexts": ["test"] },
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null
}
JSON
