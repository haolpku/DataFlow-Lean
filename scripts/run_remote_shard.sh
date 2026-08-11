#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 4 ]]; then
  echo "usage: $0 SHARD_JSONL OUTPUT_DIR PROVIDER MODEL [BASE_URL]" >&2
  exit 2
fi

shared=/vepfs-mlp2/c20250602/500050/lh/lianghao
export PATH="$shared/toolchains/lean-4.28.0-linux/bin:$PATH"
export HTTP_PROXY=http://127.0.0.1:8081
export HTTPS_PROXY=http://127.0.0.1:8081

args=(
  --input "$1"
  --fateh-root "$shared/FATE-H"
  --output-root "$2"
  --provider "$3"
  --model "$4"
  --planning-rounds 21
  --executor-attempts 10
)
if [[ "$3" == "openai" ]]; then
  if [[ $# -lt 5 ]]; then
    echo "BASE_URL is required for the openai provider" >&2
    exit 2
  fi
  args+=(--base-url "$5" --api-key-env M2F_API_KEY)
fi

exec "$shared/dataflow-lean-env/bin/dataflow-lean-m2f-fateh" "${args[@]}"
