#!/bin/sh
# Starts as root only long enough to make sure the data folder belongs to the app
# user (it is usually a folder on the host, created by root), then drops to that
# user for good and runs the command (by default: python manage.py serve).
set -e

DATA_DIR="${PINKSHEET_DATA_DIR:-/data}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"
    if [ "$(stat -c %u "$DATA_DIR")" != "$(id -u dispodex)" ]; then
        echo "Giving the data folder to the dispodex user (first start only)..."
        chown -R dispodex:dispodex "$DATA_DIR"
    fi
    exec setpriv --reuid=dispodex --regid=dispodex --init-groups "$@"
fi

exec "$@"
