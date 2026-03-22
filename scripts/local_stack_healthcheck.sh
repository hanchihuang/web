#!/usr/bin/env bash
set -euo pipefail

LOG_FILE="/home/user/tool_aggregator/local_stack_healthcheck.log"
STATE_FILE="/home/user/tool_aggregator/local_stack_healthcheck_state.env"
TUSHARE_HEALTH_API_KEY="${TUSHARE_HEALTH_API_KEY:-huanghanchi}"

touch "$LOG_FILE"
touch "$STATE_FILE"

log() {
  printf '%s %s\n' "$(date '+%F %T')" "$1" >> "$LOG_FILE"
}

safe_source_state() {
  # shellcheck disable=SC1090
  source "$STATE_FILE" 2>/dev/null || true
}

get_fail_count() {
  local key="$1"
  safe_source_state
  local var_name="FAIL_${key}"
  printf '%s' "${!var_name:-0}"
}

set_fail_count() {
  local key="$1"
  local value="$2"
  /home/user/anaconda3/bin/python3.13 - "$STATE_FILE" "$key" "$value" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = f"FAIL_{sys.argv[2]}"
value = sys.argv[3]
lines = []
if path.exists():
    lines = path.read_text(encoding="utf-8").splitlines()
updated = False
for idx, line in enumerate(lines):
    if line.startswith(f"{key}="):
        lines[idx] = f"{key}={value}"
        updated = True
        break
if not updated:
    lines.append(f"{key}={value}")
path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
PY
}

reset_fail_count() {
  set_fail_count "$1" 0
}

inc_fail_count() {
  local key="$1"
  local current
  current="$(get_fail_count "$key")"
  current=$((current + 1))
  set_fail_count "$key" "$current"
  printf '%s' "$current"
}

restart_unit() {
  local unit="$1"
  log "restart ${unit}"
  systemctl --user restart "$unit"
}

strong_restart_stack() {
  log "strong_restart_stack begin"
  systemctl --user restart ai-tool-cloudflared.service || true
  systemctl --user restart ai-tool-gunicorn.service || true
  systemctl --user restart tushare-relay.service || true
  sleep 8
  log "strong_restart_stack end"
}

kill_port_listener() {
  local port="$1"
  local pids
  pids="$(lsof -t -iTCP:${port} -sTCP:LISTEN || true)"
  if [[ -n "$pids" ]]; then
    log "kill_port_listener ${port} pids=${pids//$'\n'/,}"
    printf '%s\n' "$pids" | xargs -r kill -9
    sleep 2
  fi
}

force_recover_service() {
  local service="$1"
  local port="$2"
  log "force_recover ${service} port=${port}"
  systemctl --user stop "$service" || true
  if [[ -n "$port" ]]; then
    kill_port_listener "$port"
  fi
  systemctl --user start "$service" || true
  sleep 6
}

check_url() {
  local name="$1"
  local url="$2"
  shift 2
  if curl --http1.1 -fsS --max-time 15 "$@" "$url" >/dev/null; then
    log "ok ${name}"
    reset_fail_count "$name"
    return 0
  fi
  local count
  count="$(inc_fail_count "$name")"
  log "fail ${name} count=${count}"
  return 1
}

check_unit_active() {
  local unit="$1"
  if systemctl --user is-active --quiet "$unit"; then
    log "active ${unit}"
    return 0
  fi
  log "inactive ${unit}"
  return 1
}

recover_by_threshold() {
  local name="$1"
  local count
  count="$(get_fail_count "$name")"
  case "$name" in
    django_local)
      if (( count == 1 )); then
        restart_unit "ai-tool-gunicorn.service"
      elif (( count == 2 )); then
        force_recover_service "ai-tool-gunicorn.service" "8000"
      elif (( count >= 3 )); then
        strong_restart_stack
      fi
      ;;
    tushare_relay_local)
      if (( count == 1 )); then
        restart_unit "tushare-relay.service"
      elif (( count == 2 )); then
        force_recover_service "tushare-relay.service" "8001"
      elif (( count >= 3 )); then
        strong_restart_stack
      fi
      ;;
    external_home)
      if (( count == 1 )); then
        restart_unit "ai-tool-cloudflared.service"
      elif (( count == 2 )); then
        restart_unit "ai-tool-gunicorn.service"
        restart_unit "ai-tool-cloudflared.service"
      elif (( count >= 3 )); then
        strong_restart_stack
      fi
      ;;
    external_tushare)
      if (( count == 1 )); then
        restart_unit "tushare-relay.service"
      elif (( count == 2 )); then
        restart_unit "ai-tool-gunicorn.service"
        restart_unit "tushare-relay.service"
        restart_unit "ai-tool-cloudflared.service"
      elif (( count >= 3 )); then
        force_recover_service "tushare-relay.service" "8001"
        force_recover_service "ai-tool-gunicorn.service" "8000"
        strong_restart_stack
      fi
      ;;
  esac
}

post_recovery_recheck() {
  local name="$1"
  local url="$2"
  shift 2
  sleep 6
  if curl --http1.1 -fsS --max-time 15 "$@" "$url" >/dev/null; then
    log "recovered ${name}"
    reset_fail_count "$name"
    return 0
  fi
  log "still_fail ${name} count=$(get_fail_count "$name")"
  return 1
}

check_unit_active "ai-tool-gunicorn.service" || restart_unit "ai-tool-gunicorn.service"
check_unit_active "tushare-relay.service" || restart_unit "tushare-relay.service"
check_unit_active "ai-tool-cloudflared.service" || restart_unit "ai-tool-cloudflared.service"

if ! check_url "django_local" "http://127.0.0.1:8000/"; then
  recover_by_threshold "django_local"
  post_recovery_recheck "django_local" "http://127.0.0.1:8000/" || true
fi

if ! check_url "tushare_relay_local" "http://127.0.0.1:8001/health"; then
  recover_by_threshold "tushare_relay_local"
  post_recovery_recheck "tushare_relay_local" "http://127.0.0.1:8001/health" || true
fi

if ! check_url "external_home" "https://ai-tool.indevs.in/"; then
  recover_by_threshold "external_home"
  post_recovery_recheck "external_home" "https://ai-tool.indevs.in/" || true
fi

if ! check_url "external_tushare" "https://ai-tool.indevs.in/tushare/health" -H "X-API-Key: ${TUSHARE_HEALTH_API_KEY}"; then
  recover_by_threshold "external_tushare"
  post_recovery_recheck "external_tushare" "https://ai-tool.indevs.in/tushare/health" -H "X-API-Key: ${TUSHARE_HEALTH_API_KEY}" || true
fi

exit 0
