#!/usr/bin/env bash
# 一次性：把 cover/env 里的 R2 凭据写进 GitHub 仓库 Secrets（不打印值）
set -euo pipefail
REPO=${REPO:-493764654/cover-cloud}
set -a
. /d/DSHgongzuoqualiyun/cover/env
set +a
for k in R2_ENDPOINT R2_BUCKET R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_PUBLIC_BASE; do
  v="${!k}"
  if [ -z "$v" ]; then echo "SKIP $k (empty)"; continue; fi
  printf '%s' "$v" | gh secret set "$k" -R "$REPO"
  echo "SET $k (${#v} chars)"
done
gh secret list -R "$REPO"
