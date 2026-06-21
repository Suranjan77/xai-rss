#!/usr/bin/env bash
# Launch a single llama-server for the configured active model (one model at a
# time). Reads model + MTP settings from config via a tiny python helper.
#
# Usage: scripts/serve_llm.sh [model_key]   # default: config active_model
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Emit shell assignments (SERVER_BIN, HOST, PORT, ...) from config.
eval "$(python3 - "$ROOT" "${1:-}" <<'PY'
import sys, shlex
sys.path.insert(0, f"{sys.argv[1]}/src")
from idigest.config import load_config
cfg = load_config()
llm = cfg["llm"]
key = sys.argv[2] or llm["active_model"]
m = llm["models"][key]
def out(k, v): print(f'{k}={shlex.quote(str(v))}')
out("SERVER_BIN", llm["server_bin"])
out("HOST", llm["host"]); out("PORT", llm["port"])
out("CTX", llm["ctx_size"]); out("NGL", llm["n_gpu_layers"])
out("MODEL", m["path"])
out("SPEC_TYPE", m.get("spec_type", "none"))
out("SPEC_FALLBACK", m.get("spec_fallback", "none"))
out("SPEC_NMAX", m.get("spec_draft_n_max", 3))
out("MTP_PATH", m.get("mtp_path", ""))
out("MMPROJ", m.get("mmproj_path", ""))
PY
)"

ARGS=(-m "$MODEL" -c "$CTX" -ngl "$NGL" --host "$HOST" --port "$PORT" --jinja)

SPEC="$SPEC_TYPE"
if [[ "$SPEC" == "draft-mtp" ]]; then
  if [[ -n "$MTP_PATH" && -f "$MTP_PATH" ]]; then
    ARGS+=(--spec-type draft-mtp --model-draft "$MTP_PATH"
           --spec-draft-ngl "$NGL" --spec-draft-n-max "$SPEC_NMAX")
    echo ">> MTP enabled: $MTP_PATH"
  else
    echo ">> MTP draft file missing ($MTP_PATH); falling back to $SPEC_FALLBACK"
    SPEC="$SPEC_FALLBACK"
  fi
fi
if [[ "$SPEC" == ngram-* ]]; then
  ARGS+=(--spec-type "$SPEC" --spec-draft-n-max "$SPEC_NMAX")
  echo ">> Speculation: $SPEC (model-free)"
fi

[[ -n "$MMPROJ" && -f "$MMPROJ" ]] && ARGS+=(--mmproj "$MMPROJ")

echo ">> $SERVER_BIN ${ARGS[*]}"
exec "$SERVER_BIN" "${ARGS[@]}"
