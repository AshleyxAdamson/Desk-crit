#!/bin/sh
# Starts the Desk Crit server in the plugin's own Python environment.
#
#   start-server.sh <plugin-root> <plugin-data>
#
# .mcp.json runs this and passes both folders as arguments. An MCP server's
# process may not get CLAUDE_PLUGIN_ROOT or CLAUDE_PLUGIN_DATA as environment
# variables, so the script doesn't read them.
#
# The environment lives in <plugin-data>/venv, outside the plugin's versioned
# folder, so it survives a plugin update. VIRTUAL_ENV points uv at it and
# --active tells uv to use it. Nothing here changes where packages come from.
# uv reads its package sources from the locked file in <plugin-root>/server.
#
# hooks/build-env.sh builds the same environment ahead of time with `uv sync`.
# A test (test_doctor.py) checks the two stay in step.
# Plain POSIX sh, so it runs on the sh macOS ships.

if [ -z "$1" ] || [ -z "$2" ]; then
  echo "start-server.sh: needs the plugin root and the plugin data folder as arguments." >&2
  echo "usage: start-server.sh <plugin-root> <plugin-data>" >&2
  exit 2
fi

VIRTUAL_ENV="$2/venv"
HF_HUB_DISABLE_TELEMETRY=1
export VIRTUAL_ENV HF_HUB_DISABLE_TELEMETRY

exec uv run --active --locked --project "$1/server" desk-crit-server
