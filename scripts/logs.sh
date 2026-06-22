#!/usr/bin/env bash
# Show what's running and stream all idigest service logs in one place.
#
#   ./scripts/logs.sh            # recent consolidated logs
#   ./scripts/logs.sh -f         # follow live
#   ./scripts/logs.sh -n 200     # last 200 lines
#
# Any extra args are passed straight to journalctl.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

UNITS=(idigest-llm idigest-tts idigest-web idigest-ingest idigest-email idigest-digest)

echo "=== services (long-running) ==="
systemctl --user list-units 'idigest-*.service' --all --no-pager \
  | awk 'NR==1 || /idigest-/{print}'
echo
echo "=== timers (scheduled) ==="
systemctl --user list-timers 'idigest-*' --no-pager | grep -E "NEXT|idigest-" || true
echo
echo "=== consolidated logs (Ctrl-C to stop) ==="
args=(); for u in "${UNITS[@]}"; do args+=(-u "$u"); done
exec journalctl --user "${args[@]}" -o short-iso "${@:--n 60}"
