#!/bin/bash
# Start the web app: ./start.sh (the same as `./oyster run`; see `./oyster help`).
exec "$(dirname "$0")/oyster" run "$@"
