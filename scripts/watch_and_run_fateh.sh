#!/usr/bin/env bash
set -u

if [ "$#" -ne 3 ]; then
  echo "usage: $0 IDS OUTPUT_ROOT LOG_FILE" >&2
  exit 2
fi

ids=$1
output_root=$2
log_file=$3
repo_root=/vepfs-mlp2/c20250602/500050/lh/lianghao/DataFlow-Lean-dev
python_bin=/vepfs-mlp2/c20250602/500050/lh/lianghao/dataflow-lean-env/bin/python
fateh_root=/vepfs-mlp2/c20250602/500050/lh/lianghao/FATE-H
endpoint=http://127.0.0.1:4200/v1
model=gpt-5.4

mkdir -p "$(dirname "$log_file")" "$output_root"
echo "$(date -Is) waiting for $model channel; ids=$ids" >>"$log_file"
while true; do
  response=$(curl -sS --max-time 45 "$endpoint/chat/completions" \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply only OK\"}],\"max_tokens\":8}" 2>&1 || true)
  if printf '%s' "$response" | grep -q '"choices"'; then
    echo "$(date -Is) channel ready; starting runner" >>"$log_file"
    break
  fi
  echo "$(date -Is) channel unavailable" >>"$log_file"
  sleep 300
done

cd "$repo_root" || exit 1
export OPENAI_API_KEY=unused
exec "$python_bin" scripts/run_fateh_subset.py \
  --fateh-root "$fateh_root" \
  --output-root "$output_root" \
  --ids "$ids" --workers 2 --model "$model" \
  --base-url "$endpoint" --planning-rounds 1 --executor-attempts 5 >>"$log_file" 2>&1
