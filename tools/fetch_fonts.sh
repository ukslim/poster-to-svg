#!/bin/bash
# Thin wrapper; the corpus is defined in fetch_fonts.py.
exec python3 "$(dirname "$0")/fetch_fonts.py" "$@"
