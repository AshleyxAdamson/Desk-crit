#!/bin/sh
# Builds the plugin's own Python environment from its locked package list.
#
#   build-env.sh <lock-dir>
#
# doctor.sh starts this detached, at SessionStart, when the environment isn't
# built yet. It does what `uv run --locked` does when the server starts
# (hooks/start-server.sh), minus the server, so a kill of the server can't cut the build short. Claude Code
# waits 30 seconds for a server and then kills it, and a cold build takes
# longer than that. The wheels a killed try had finished stay in uv's cache, but
# a slow connection may never finish the largest one inside 30 seconds.
#
# The server's own `uv run` and this build use one environment. uv locks it
# (venv/.lock), so whichever comes second waits, then finds the packages
# installed. Nothing here can corrupt the other.
#
# This builds the plugin's environment only. It installs no system software.
#
# Needs CLAUDE_PLUGIN_ROOT and CLAUDE_PLUGIN_DATA. <lock-dir> is the folder
# doctor.sh made to keep a second build from starting; it goes when this ends.
# If this is killed hard, the folder stays, holding the pid of a process that is
# gone or is no longer this script, and the next session takes it over.
# Plain POSIX sh, so it runs on the sh macOS ships.

lock=$1
uv=""

# Every way out ends here: the log says how it ended, and the exit trap below
# removes the lock.
finish() {
  echo "uv sync finished with status $1"
  exit "$1"
}

# Stops uv, waits for it to go, and leaves with 128 plus the signal number.
stop() {
  if [ -n "$uv" ]; then
    kill "$uv" 2>/dev/null
    wait "$uv" 2>/dev/null
  fi
  finish "$1"
}

# nohup left SIGHUP ignored. INT and TERM go through stop. A shell runs a trap
# only once its foreground command returns, so uv runs in the background and
# this script waits for it, which a signal can interrupt.
trap 'rm -rf "$lock"' EXIT
trap 'stop 130' INT
trap 'stop 143' TERM

# The same two variables hooks/start-server.sh gives the server. test_doctor.py
# checks the two stay in step.
VIRTUAL_ENV="$CLAUDE_PLUGIN_DATA/venv"
HF_HUB_DISABLE_TELEMETRY=1
export VIRTUAL_ENV HF_HUB_DISABLE_TELEMETRY

echo "Building the Desk Crit environment in $VIRTUAL_ENV"
uv sync --active --locked --project "$CLAUDE_PLUGIN_ROOT/server" &
uv=$!
wait "$uv"
finish $?
