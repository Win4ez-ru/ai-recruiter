#!/bin/sh
set -eu

if [ "${RUN_DATABASE_MIGRATIONS:-true}" = "true" ]; then
    alembic upgrade head
fi

exec python run.py
