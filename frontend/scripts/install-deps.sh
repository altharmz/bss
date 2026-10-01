#!/bin/sh
# make web-install: the only place npm downloads anything. npm ci installs exactly
# package-lock.json (install scripts are off, see .npmrc), then the lockfile hash is
# stored for scripts/check-deps.sh.
set -eu
npm ci
sha256sum package-lock.json | cut -d' ' -f1 > node_modules/.lock-sha256
