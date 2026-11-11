#!/usr/bin/env bash
# Nomad Atlas - stop (macOS / Linux). Your data stays saved.
cd "$(dirname "$0")" || exit 1
echo
if [ ! -x ".venv/bin/python" ]; then
  echo "  Atlas has not been started on this computer yet."
else
  .venv/bin/python -m atlas stop
fi
echo
