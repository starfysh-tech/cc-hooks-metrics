#!/usr/bin/env bash
PYTHONPATH="$(dirname "$0")" exec python3 -m hooks_report "$@"
