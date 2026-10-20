#!/bin/bash

cd alembic/

# The database is usually ready thanks to the compose healthcheck; retry a little in case it restarts.
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    alembic upgrade head && break
    if [ "$attempt" = "10" ]; then
        echo "Database migrations failed, giving up" >&2
        exit 1
    fi
    echo "Database not ready yet, retrying in 3 seconds ($attempt/10)" >&2
    sleep 3
done

cd ../src

exec python main.py
