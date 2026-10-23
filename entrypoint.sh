#!/bin/sh
# Bind-mounted volumes are usually owned by root. Fix ownership of the data dir,
# then drop privileges so the app never runs as root.
set -e
DATA="${DATA_DIR:-/data}"
if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DATA"
  chown -R 10001:10001 "$DATA" 2>/dev/null || echo "warning: could not chown $DATA" >&2
  exec setpriv --reuid=10001 --regid=10001 --clear-groups "$@"
fi
exec "$@"
