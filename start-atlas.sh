#!/usr/bin/env bash
# Nomad Atlas - start (macOS / Linux). Double-click it, or run:  ./start-atlas.sh
# First start: sets up a Python environment and the parts Atlas needs
# (needs the internet once). Later starts work offline.
cd "$(dirname "$0")" || exit 1

echo
echo "  NOMAD ATLAS"
echo "  ==========="
echo

fail() {
  echo
  echo "  Atlas did not start. Nothing was lost."
  read -r -p "  Press Enter to close. " _
  exit 1
}

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  SYSPY=""
  for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      SYSPY="$candidate"
      break
    fi
  done
  if [ -z "$SYSPY" ]; then
    echo "  Python 3.11 or newer is not installed on this computer. Atlas needs it."
    echo "    macOS:  download it from https://www.python.org/downloads/ and run the installer"
    echo "    Linux:  install the packages python3 and python3-venv with your software manager"
    echo "  Then start Atlas again."
    fail
  fi
  echo "  Setting up Atlas for the first time."
  echo "  This needs the internet and takes a few minutes. Please wait ..."
  if ! "$SYSPY" -m venv .venv; then
    echo "  Atlas could not create its working folder \".venv\" here."
    echo "  On Linux, install the package python3-venv and start Atlas again."
    fail
  fi
fi

if ! cmp -s requirements.txt .venv/requirements.installed; then
  echo "  Installing the parts Atlas needs ..."
  if ! "$PY" -m pip install --disable-pip-version-check -q -r requirements.txt; then
    echo
    echo "  The parts Atlas needs could not be downloaded."
    echo "  Check that this computer is connected to the internet, then start Atlas again."
    echo "  This is only needed once."
    fail
  fi
  cp requirements.txt .venv/requirements.installed
fi

echo "  Starting ..."
echo
"$PY" -m atlas start || fail
