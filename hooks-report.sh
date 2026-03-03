#!/usr/bin/env bash
DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${DIR}/.venv/bin/python3"
[ -x "$PYTHON" ] || PYTHON=python3
PYTHONPATH="$DIR" exec "$PYTHON" -m hooks_report "$@"
