#!/bin/bash
# Django 3.2 needs SQLite >= 3.9. On hosts with an older system library
# (CentOS 7 ships 3.7) build a newer SQLite and preload it:
#
#   deploy/run_with_sqlite3.sh manage.py migrate
#   deploy/run_with_sqlite3.sh -m gunicorn codetool.asgi:application ...
#
export LD_PRELOAD="${SQLITE3_LIBRARY:-/usr/local/lib/libsqlite3.so}"
cd "$(dirname "$0")/.." && exec python3 "$@"
