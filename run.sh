#!/usr/bin/env sh
cd "$(dirname "$0")/src"
uv run flask --app app run --debug --no-reload
