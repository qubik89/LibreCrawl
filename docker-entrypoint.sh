#!/bin/sh
set -e

mkdir -p /app/data
chown -R mitmore_seo_crawl:mitmore_seo_crawl /app/data

if [ "$(id -u)" = "0" ]; then
  exec gosu mitmore_seo_crawl "$@"
fi

exec "$@"
