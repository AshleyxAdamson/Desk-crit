#!/bin/sh
# Desk Crit first-run check. Runs at SessionStart.
#
# Silent when all is well, so it costs no context. When something's missing it
# prints one plain line per problem with the exact fix. Claude Code adds this
# output to Claude's context, and Claude tells the user. It never installs
# system software (uv, ffmpeg), and it always exits 0.
#
# The one thing it does besides look: on the first session it starts a
# background build of the plugin's own Python environment (see step 4).
#
# Why a hook: with no uv the MCP server never starts, so no tool could say so.
# The first start is the other reason. It builds a 530 MB environment and
# outlasts Claude Code's 30 second startup limit, which then kills the server
# and the build with it. Only a hook can build outside the server's lifetime,
# and say that a failed server is expected.
# Plain POSIX sh, so it runs on the sh macOS ships.

problems=""
add() {
  problems="${problems}- $1
"
}

# Where this script lives. The build script sits beside it.
case "$0" in
  */*) here=${0%/*} ;;
  *) here=. ;;
esac

# Starts a background build of the plugin's Python environment, unless one is
# already running. Sets building=1 when one is running or has just started, and
# last_failed=1 when it starts one because the previous build failed.
#
# The lock is a folder, because mkdir either makes it or fails, never both for
# two hooks at once. Once the build is running, the folder holds its pid. Until
# then it holds nothing, and a folder with no pid that is under a minute old is
# a hook in the middle of starting a build, so it counts as running.
#
# A pid alone proves little. After a reboot or a kill -9 the build is gone, its
# folder stays, and the number can go to any other process. So the pid counts
# only while that process's command is our build script. Anything else means
# the build died before it could clean up, and the next hook takes the lock
# over. Two hooks taking over in the same instant could both build. uv locks the
# environment itself, so the second waits and finds it built, and nothing breaks.
start_build() {
  lock="$CLAUDE_PLUGIN_DATA/build.lock"
  log="$CLAUDE_PLUGIN_DATA/build.log"
  mkdir -p "$CLAUDE_PLUGIN_DATA" 2>/dev/null || return 0
  if ! mkdir "$lock" 2>/dev/null; then
    owner=""
    [ -r "$lock/pid" ] && IFS= read -r owner < "$lock/pid"
    case "$owner" in
      ''|*[!0-9]*) owner="" ;;
    esac
    if [ -n "$owner" ]; then
      # -ww: a long plugin path must not cut the command short of the script name.
      case "$(ps -ww -o command= -p "$owner" 2>/dev/null)" in
        *build-env.sh*) building=1; return 0 ;;
      esac
    elif [ -z "$(find "$lock" -maxdepth 0 -mmin +1 2>/dev/null)" ]; then
      building=1
      return 0
    fi
    rm -rf "$lock"
    if ! mkdir "$lock" 2>/dev/null; then
      # Another hook took it over first. Its build is the one running.
      [ -d "$lock" ] && building=1
      return 0
    fi
  fi

  # The new build overwrites the log, so read what the last one said first. A
  # build that ended in a nonzero status wrote "finished with status" and it.
  if [ -r "$log" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      case "$line" in
        *"finished with status "[1-9]*) last_failed=1 ;;
      esac
    done < "$log"
  fi

  # The build outlives this hook, the server, and Claude Code's kill of either.
  # set -m gives it a process group of its own, so a kill of this hook's group
  # can't reach it. nohup keeps a closed terminal from hanging it up. Its input
  # is /dev/null and its output a file, so nothing holds this hook's pipes open
  # and Claude Code sees the hook end at once. macOS has no setsid to do this.
  set -m
  CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$here/..}" \
    nohup sh "$here/build-env.sh" "$lock" \
    </dev/null >"$log" 2>&1 &
  builder=$!
  set +m
  # The build may already be over and the lock gone. Then there's nothing to write.
  { echo "$builder" > "$lock/pid"; } 2>/dev/null
  building=1
}

# 1. An Apple Silicon Mac. The locked environment (mlx) covers nothing else.
if [ "$(uname -s 2>/dev/null)" != "Darwin" ]; then
  add "Desk Crit only runs on a Mac with Apple Silicon (M1 or newer). This computer isn't a Mac."
  platform_bad=1
elif [ "$(uname -m 2>/dev/null)" != "arm64" ]; then
  if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ]; then
    add "This terminal runs as an Intel app under Rosetta. Open a native Terminal, then start Claude Code again."
  else
    add "Desk Crit needs a Mac with Apple Silicon (M1 or newer). Intel Macs aren't supported yet."
  fi
  platform_bad=1
fi

if [ -z "$platform_bad" ]; then
  # 2. uv starts the server. Without it, nothing else works.
  if command -v uv >/dev/null 2>&1; then
    have_uv=1
  else
    add "uv isn't on the PATH Claude Code sees. Run: brew install uv. If it's already installed, start Claude Code from a terminal, which has your PATH."
  fi

  # 3. ffmpeg and ffprobe. Desk Crit only decodes frames and reads durations,
  # so any version will do and no encoder is needed.
  have_ffmpeg=1
  have_ffprobe=1
  command -v ffmpeg >/dev/null 2>&1 || have_ffmpeg=""
  command -v ffprobe >/dev/null 2>&1 || have_ffprobe=""

  if [ -z "$have_ffmpeg" ] && [ -z "$have_ffprobe" ]; then
    add "ffmpeg and ffprobe aren't installed. Run: brew install ffmpeg"
  elif [ -z "$have_ffmpeg" ]; then
    add "ffmpeg isn't installed. Run: brew install ffmpeg"
  elif [ -z "$have_ffprobe" ]; then
    add "ffprobe isn't installed. It comes with ffmpeg. Run: brew install ffmpeg"
  fi

  # Every fix above is a Homebrew command. Say where to get it once.
  if [ -n "$problems" ] && ! command -v brew >/dev/null 2>&1; then
    add "Homebrew isn't installed. Get it from https://brew.sh first, then run the commands above."
  fi

  # 4. Is the server's Python environment built? The folder itself shows up
  # within milliseconds of a build starting, before one package is fetched, so
  # it can't tell built from building. The launcher script is only made once
  # every package has arrived. With no data folder given (an older Claude
  # Code), there's nothing to look at, and the server builds it on its own.
  if [ -n "$have_uv" ] && [ -n "$CLAUDE_PLUGIN_DATA" ] \
    && [ ! -e "$CLAUDE_PLUGIN_DATA/venv/bin/desk-crit-server" ]; then
    start_build
  fi
fi

if [ -n "$problems" ]; then
  printf "Desk Crit can't run yet. Tell the user what's missing, and let them run the fixes:\n%s" "$problems"
fi
# Not a problem to fix, so it stays out of the list. The build runs whatever
# else is wrong, and the user needs to know the server fails until it's done.
if [ -n "$building" ]; then
  printf "%s" "Desk Crit is building its Python environment in the background (about 530 MB, a few minutes). Tell the user the desk-crit server shows as failed until it's done, and that's expected. When it's done, they run /mcp and choose Reconnect on desk-crit, or start a new session."
  # The build has just replaced the log, so it holds this try. If this one fails
  # the same way, that's the reason.
  if [ -n "$last_failed" ]; then
    printf " The last build failed; if this retry fails too, tell the user to look at %s to see why." "$log"
  fi
  printf "\n"
fi
exit 0
