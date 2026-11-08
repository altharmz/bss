# Sourced by web-smoke.sh and mcp-smoke.sh (not executable on its own).
#
# smoke_retry <method> <label> <command> [args...]
#   Runs <command>: ONE curl whose --write-out is '%{http_code} %{time_pretransfer}'. Prints
#   the HTTP status, or 000 when no answer came. A request that failed at the connection
#   level (curl exit != 0) is tried again - SMOKE_ATTEMPTS in all (3), SMOKE_RETRY_DELAY
#   seconds apart (3) - but only when repeating it is safe: a GET, or a request that never
#   got as far as being sent (time_pretransfer 0: no connection or no TLS). A POST that may
#   have reached the server is not repeated, and a wrong status or body is never retried.
#
# Why (#257): during deploys the public endpoint has been seen to stall one connection at
# a time (curl exit 28 on the TCP or proxy connect, a TLS handshake timeout) while every
# other request passes; without a retry that single stall rolled back a healthy release.
smoke_retry() {
  local method="$1" label="$2" attempts="${SMOKE_ATTEMPTS:-3}" delay="${SMOKE_RETRY_DELAY:-3}"
  local attempt rc out
  shift 2
  for (( attempt = 1; ; attempt++ )); do
    rc=0
    out="$("$@")" || rc=$?
    if (( rc == 0 )); then
      printf '%s\n' "${out%% *}"
      return 0
    fi
    if (( attempt >= attempts )) || ! [[ "${method}" == GET || "${out#* }" == 0.000000 ]]; then
      echo 000
      return 0
    fi
    printf '  retry %s %s: no answer (curl exit %s), attempt %s of %s in %ss\n' \
      "${method}" "${label}" "${rc}" "$(( attempt + 1 ))" "${attempts}" "${delay}" >&2
    sleep "${delay}"
  done
}
