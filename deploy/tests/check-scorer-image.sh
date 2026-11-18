#!/usr/bin/env bash
# Checks a built scorer image: the command, the torch build it was asked for, offline settings,
# the non-root user, and a one-line refusal when the weights are missing.
# Usage: deploy/tests/check-scorer-image.sh IMAGE TORCH_INDEX   (TORCH_INDEX: cpu, cu126, ...)
set -euo pipefail
image=${1:?usage: check-scorer-image.sh IMAGE TORCH_INDEX}
index=${2:?usage: check-scorer-image.sh IMAGE TORCH_INDEX}
fail() { echo "FAIL: $image: $*" >&2; exit 1; }
run() { docker run --rm --network none --entrypoint "" "$image" "$@"; }

docker image inspect "$image" >/dev/null 2>&1 || fail "no such image (build it: make image-scorer)"
[ "$(run id -u):$(run id -g)" = "10001:10001" ] || fail "must run as uid 10001, gid 10001"
run coglossa-scorer --help >/dev/null || fail "coglossa-scorer --help"
torch=$(run python -c 'import torch; print(torch.__version__)')
[[ $torch == *"+$index" ]] || fail "torch is $torch, expected a +$index build"
run python -c 'import transformers, sentence_transformers, sentencepiece, safetensors' \
  || fail "a scorer dependency is missing"
[ "$(run sh -c 'echo "$HF_HUB_OFFLINE:$TRANSFORMERS_OFFLINE"')" = "1:1" ] \
  || fail "HF_HUB_OFFLINE and TRANSFORMERS_OFFLINE must be 1"
if out=$(run coglossa-scorer --embed-model /models/labse 2>&1); then
  fail "the scorer started without weights"
fi
grep -q "^error: cannot load model" <<<"$out" || fail "missing weights must fail in one line, got: $out"
echo "ok: $image (torch $torch)"
