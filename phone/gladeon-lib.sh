#!/data/data/com.termux/files/usr/bin/bash
# Shared helpers for the Gladeon Termux scripts (spec 11.9, 11.10). Sourced, never run.
# Config: ~/.gladeon/config (KEY=value lines, read without eval), ~/.gladeon/cert.pem, ~/.gladeon/auth.header
set -u
GL_DIR="${GLADEON_DIR:-$HOME/.gladeon}"

gl_conf() {  # gl_conf KEY -> value from the config file; no eval, no sourcing
  local key="$1" line
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      "$key="*) printf '%s' "${line#*=}"; return 0 ;;
    esac
  done < "$GL_DIR/config"
  return 1
}

GL_IP1="$(gl_conf PC_IP_ETH || true)"
GL_IP2="$(gl_conf PC_IP_WLAN || true)"
GL_PIN="$(gl_conf SPKI_PIN || true)"     # base64 sha256 of the server public key
GL_PORT="$(gl_conf PORT || echo 8443)"
GL_CODE="" GL_BODY="" GL_EXIT=0
# Callers must not pipe into gl_request (a pipe runs it in a subshell); use < <(...) or <<<.

gl_clean() {  # strip control characters before anything reaches the terminal (R5: B5-3)
  tr -d '\000-\010\013-\037\177'
}

gl_uuid() {  # UUIDv4
  if [ -r /proc/sys/kernel/random/uuid ]; then cat /proc/sys/kernel/random/uuid; return; fi
  local h; h="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
  printf '%s-%s-4%s-%x%s-%s\n' "${h:0:8}" "${h:8:4}" "${h:13:3}" $(( (0x${h:16:1} & 3) | 8 )) "${h:17:3}" "${h:20:12}"
}

# gl_curl IP METHOD PATH [REQUEST_ID] ; body on stdin for POST. Prints "HTTPCODE\nBODY".
gl_curl() {
  local ip="$1" method="$2" path="$3" rid="${4:-}"
  local args=(--silent --show-error --cacert "$GL_DIR/cert.pem" --pinnedpubkey "sha256//$GL_PIN"
              --max-time 200 --connect-timeout 5 -H "@$GL_DIR/auth.header"
              -H "Content-Type: application/json" -w '\n%{http_code}' -X "$method")
  [ -n "$rid" ] && args+=(-H "X-Request-Id: $rid")
  if [ "$method" = POST ]; then args+=(--data-binary @-); fi
  curl "${args[@]}" "https://$ip:$GL_PORT$path"
}

# gl_request METHOD PATH [REQUEST_ID] ; retries a failed connect with the same id after 0.5/1.5/3 s,
# the second retry on the other IP (11.9.2). Sets GL_CODE, GL_BODY, GL_EXIT.
gl_request() {
  local method="$1" path="$2" rid="${3:-}" body="" out ip i
  [ "$method" = POST ] && body="$(cat)"
  local delays=(0 0.5 1.5 3) ips=("$GL_IP1" "$GL_IP1" "${GL_IP2:-$GL_IP1}" "$GL_IP1")
  for i in 0 1 2 3; do
    sleep "${delays[$i]}"
    ip="${ips[$i]}"
    out="$(printf '%s' "$body" | gl_curl "$ip" "$method" "$path" "$rid")"
    GL_EXIT=$?
    GL_CODE="${out##*$'\n'}"
    GL_BODY="${out%$'\n'*}"
    case "$GL_EXIT" in
      0|28) return 0 ;;          # done, or timed out (caller polls /result)
      6|7|35|52|55|56) continue ;; # connect failure / reset: retry same id
      *) return 0 ;;
    esac
  done
  return 0
}

gl_field() { printf '%s' "$GL_BODY" | jq -r --arg k "$1" '.[$k] // empty' 2>/dev/null | gl_clean; }

gl_say() {  # speak through the local TTS only (11.11.2)
  if [ -n "$1" ] && command -v termux-tts-speak >/dev/null; then termux-tts-speak "$1"; fi
  return 0
}

gl_err() {
  local e; e="$(gl_field error)"
  printf 'Gladeon: %s (HTTP %s)\n' "${e:-no reply}" "${GL_CODE:-?}" >&2
}
