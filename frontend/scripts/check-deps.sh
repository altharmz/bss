#!/bin/sh
# Runs inside the offline web container before make web-build/-lint/-test/-types/-fmt.
# It never installs anything: it only checks that the node_modules volume holds exactly
# what package-lock.json says, via the lockfile hash `make web-install` stored there.
set -eu
stamp=node_modules/.lock-sha256
want="$(sha256sum package-lock.json | cut -d' ' -f1)"
have="$(cat "$stamp" 2>/dev/null || echo none)"
if [ "$have" != "$want" ]; then
  echo "node_modules does not match package-lock.json (installed: $have, lock: $want)." >&2
  echo "Run: make web-install" >&2
  exit 1
fi
