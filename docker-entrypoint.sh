#!/bin/sh
set -e

mkdir -p /app/data
chown -R librecrawl:librecrawl /app/data

if [ "$(id -u)" = "0" ]; then
  exec gosu librecrawl "$@"
fi

exec "$@"
