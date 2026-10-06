#!/bin/sh
# Sets Desk Crit up for an agent other than Claude Code.
#
#   setup-agent.sh opencode|codex|print [--remove]
#
# opencode  adds a desk-crit entry to ~/.config/opencode/opencode.json and links
#           the skill into ~/.config/opencode/skills.
# codex     adds a [mcp_servers.desk-crit] table to ~/.codex/config.toml and
#           links the skill into ~/.agents/skills.
# print     prints the launch command, the skill folder and a JSON snippet for
#           Cursor and Gemini CLI. It changes nothing.
# --remove  takes the entry and the link back out. It never deletes a backup
#           or a real folder.
#
# The server runs from this checkout: `uv run --locked --project <repo>/server
# desk-crit-server`, after a one-time `uv sync` that builds server/.venv.
#
# HOME says where the agents' config lives. DESK_CRIT_UV overrides the path of
# uv, so tests can swap in a stub.
# Exit 0 on success, 1 on a refusal, 2 on bad usage. Plain POSIX sh.

usage() {
  echo "usage: setup-agent.sh opencode|codex|print [--remove]" >&2
  echo "  opencode  add Desk Crit to OpenCode's config and link the skill" >&2
  echo "  codex     add Desk Crit to Codex's config and link the skill" >&2
  echo "  print     show the launch command and a snippet for Cursor and Gemini CLI" >&2
  echo "  --remove  undo opencode or codex" >&2
}

refuse() {
  echo "$1" >&2
  exit 1
}

# VIRTUAL_ENV from the caller's shell would make uv warn about the wrong environment.
unset VIRTUAL_ENV

mode=""
remove=0
for arg in "$@"; do
  case "$arg" in
    opencode|codex|print)
      if [ -n "$mode" ]; then usage; exit 2; fi
      mode=$arg ;;
    --remove) remove=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done
if [ -z "$mode" ]; then usage; exit 2; fi
if [ "$mode" = print ] && [ "$remove" -eq 1 ]; then
  echo "setup-agent.sh: print has nothing to remove." >&2
  usage
  exit 2
fi

root=$(cd "$(dirname "$0")/.." && pwd -P) || refuse "Couldn't work out where the Desk Crit folder is."
skill_dir="$root/skills/desk-crit"
server_dir="$root/server"

if [ -n "${DESK_CRIT_UV:-}" ]; then
  uv=$DESK_CRIT_UV
else
  uv=$(command -v uv)
fi

json_str() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }
toml_str() { json_str "$1"; }

# ---- print ------------------------------------------------------------------

if [ "$mode" = print ]; then
  [ -n "$uv" ] || uv=uv
  ju=$(json_str "$uv")
  js=$(json_str "$server_dir")
  echo "Launch command. Run the sync once first, it builds the environment (about 530 MB):"
  echo "  $uv sync --locked --project $server_dir"
  echo "  $uv run --locked --project $server_dir desk-crit-server"
  echo
  echo "Skill folder. Point your agent's skills setting at it:"
  echo "  $skill_dir"
  echo
  echo "Cursor (.cursor/mcp.json) and Gemini CLI (~/.gemini/settings.json) take this JSON:"
  cat <<EOF
{
  "mcpServers": {
    "desk-crit": {
      "command": "$ju",
      "args": ["run", "--locked", "--project", "$js", "desk-crit-server"],
      "env": { "HF_HUB_DISABLE_TELEMETRY": "1" }
    }
  }
}
EOF
  exit 0
fi

# ---- the skill link ---------------------------------------------------------

if [ -z "${HOME:-}" ]; then
  refuse "HOME isn't set, so I can't tell where the agent's config lives."
fi

case "$mode" in
  opencode)
    config="$HOME/.config/opencode/opencode.json"
    skill_link="$HOME/.config/opencode/skills/desk-crit" ;;
  codex)
    config="$HOME/.codex/config.toml"
    skill_link="$HOME/.agents/skills/desk-crit" ;;
esac
backup="$config.bak-desk-crit"

# A real folder or file at the link path is never touched.
check_link() {
  if [ -e "$skill_link" ] || [ -L "$skill_link" ]; then
    if [ ! -L "$skill_link" ]; then
      refuse "There's a real folder or file at $skill_link, so I left it alone. Move it aside and run this again."
    fi
  fi
  return 0
}

make_link() {
  if [ -L "$skill_link" ]; then
    if [ "$(readlink "$skill_link")" = "$skill_dir" ]; then
      echo "The skill link at $skill_link already points to $skill_dir."
      return 0
    fi
    rm "$skill_link" || refuse "Couldn't replace the link at $skill_link."
    mkdir -p "$(dirname "$skill_link")" && ln -s "$skill_dir" "$skill_link" || refuse "Couldn't make the skill link at $skill_link."
    echo "Pointed the skill link at $skill_link to $skill_dir."
    return 0
  fi
  mkdir -p "$(dirname "$skill_link")" && ln -s "$skill_dir" "$skill_link" || refuse "Couldn't make the skill link at $skill_link."
  echo "Linked $skill_link to $skill_dir."
}

drop_link() {
  if [ -L "$skill_link" ]; then
    if [ "$(readlink "$skill_link")" = "$skill_dir" ]; then
      rm "$skill_link" || refuse "Couldn't remove the link at $skill_link."
      echo "Removed the skill link at $skill_link."
    else
      echo "The link at $skill_link points somewhere else, so I left it alone."
    fi
  elif [ -e "$skill_link" ]; then
    echo "There's a real folder or file at $skill_link, so I left it alone."
  else
    echo "There was no skill link at $skill_link."
  fi
}

# ---- running Python ---------------------------------------------------------

# The server's own Python, so no system python is needed.
run_py() {
  if [ -x "$server_dir/.venv/bin/python" ]; then
    "$server_dir/.venv/bin/python" - "$@"
  else
    "$uv" run --locked --project "$server_dir" python - "$@"
  fi
}

build_env() {
  if [ -e "$server_dir/.venv/bin/desk-crit-server" ]; then
    echo "The Python environment is already built, so I skipped the sync."
    return 0
  fi
  echo "Building the Python environment with uv. It's about 530 MB and takes a few minutes."
  "$uv" sync --locked --project "$server_dir" >&2 || refuse "uv couldn't build the Python environment. Nothing was changed."
}

# ---- opencode ---------------------------------------------------------------

edit_opencode() {  # $1 = add or remove
  run_py "$1" "$config" "$backup" "$uv" "$root" <<'PYEOF'
import json
import os
import shutil
import sys

action, path, backup, uv, root = sys.argv[1:6]
want = {
    "type": "local",
    "command": [uv, "run", "--locked", "--project", os.path.join(root, "server"), "desk-crit-server"],
    "enabled": True,
    "environment": {"HF_HUB_DISABLE_TELEMETRY": "1"},
    "timeout": 60000,
}


def stop(message):
    print(message, file=sys.stderr)
    sys.exit(1)


existed = os.path.isfile(path)
if existed:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, OSError):
        stop(f"I couldn't read {path} as plain JSON (comments aren't allowed), so I left it alone.")
    if not isinstance(data, dict):
        stop(f"{path} isn't a JSON object, so I left it alone.")
else:
    data = {}

mcp = data.get("mcp")
if mcp is not None and not isinstance(mcp, dict):
    stop(f"The \"mcp\" key in {path} isn't an object, so I left it alone.")

if action == "remove":
    if not mcp or "desk-crit" not in mcp:
        print(f"There's no desk-crit entry in {path}, so there was nothing to remove.")
        sys.exit(0)
    del mcp["desk-crit"]
    message = f"Removed the desk-crit entry from {path}."
else:
    if not existed:
        data = {"$schema": "https://opencode.ai/config.json", "mcp": {}}
        mcp = data["mcp"]
    elif mcp is None:
        mcp = data["mcp"] = {}
    if mcp.get("desk-crit") == want:
        print(f"The desk-crit entry in {path} is already up to date.")
        sys.exit(0)
    message = (f"Updated the desk-crit entry in {path}." if "desk-crit" in mcp
               else f"Added the desk-crit entry to {path}." if existed
               else f"Created {path} with the desk-crit entry.")
    mcp["desk-crit"] = want

os.makedirs(os.path.dirname(path), exist_ok=True)
if existed and action == "add" and not os.path.exists(backup):
    shutil.copy2(path, backup)
    print(f"Saved a backup of your config at {backup}.")
tmp = path + ".desk-crit-tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")
os.replace(tmp, path)
print(message)
PYEOF
}

# ---- codex ------------------------------------------------------------------

# [mcp_servers.desk-crit], with or without quotes around the name.
codex_header='^[[:space:]]*\[mcp_servers\.("desk-crit"|desk-crit)\][[:space:]]*(#.*)?$'

codex_has_block() {
  [ -f "$config" ] && grep -Eq "$codex_header" "$config"
}

add_codex() {
  if codex_has_block; then
    echo "The [mcp_servers.desk-crit] block in $config is already there, so I left it alone."
    return 0
  fi
  mkdir -p "$(dirname "$config")" || refuse "Couldn't make the folder for $config."
  if [ -f "$config" ] && [ ! -e "$backup" ]; then
    cp -p "$config" "$backup" || refuse "Couldn't save a backup of $config."
    echo "Saved a backup of your config at $backup."
  fi
  existed=0
  [ -s "$config" ] && existed=1
  {
    # Start on a fresh line, with a blank line before the block.
    if [ "$existed" -eq 1 ]; then
      if [ -n "$(tail -c 1 "$config")" ]; then printf '\n'; fi
      printf '\n'
    fi
    printf '[mcp_servers.desk-crit]\n'
    printf 'command = "%s"\n' "$(toml_str "$uv")"
    printf 'args = ["run", "--locked", "--project", "%s", "desk-crit-server"]\n' "$(toml_str "$server_dir")"
    printf 'env = { HF_HUB_DISABLE_TELEMETRY = "1" }\n'
    printf 'startup_timeout_sec = 60.0\n'
  } >> "$config" || refuse "Couldn't write to $config."
  if [ "$existed" -eq 1 ]; then
    echo "Added the [mcp_servers.desk-crit] block to the end of $config."
  else
    echo "Created $config with the [mcp_servers.desk-crit] block."
  fi
}

remove_codex() {
  if ! codex_has_block; then
    echo "There's no [mcp_servers.desk-crit] block in $config, so there was nothing to remove."
    return 0
  fi
  tmp="$config.desk-crit-tmp"
  awk '
    function flush() { printf "%s", held; held = "" }
    /^[[:space:]]*\[/ {
      if ($0 ~ /^[[:space:]]*\[mcp_servers\.("desk-crit"|desk-crit)(\]|\.)/) { held = ""; skipping = 1; next }
      skipping = 0
      flush()
      print
      next
    }
    skipping { if ($0 ~ /^[[:space:]]*$/) { held = held $0 "\n" }; next }
    /^[[:space:]]*$/ { held = held $0 "\n"; next }
    { flush(); print }
    END { if (!skipping) flush() }
  ' "$config" > "$tmp" && mv "$tmp" "$config" || { rm -f "$tmp"; refuse "Couldn't edit $config, so I left it alone."; }
  echo "Removed the [mcp_servers.desk-crit] block from $config."
}

# ---- run --------------------------------------------------------------------

if [ "$remove" -eq 1 ]; then
  case "$mode" in
    opencode)
      if [ -f "$config" ]; then
        edit_opencode remove || exit 1
      else
        echo "There's no config file at $config, so there was nothing to remove."
      fi ;;
    codex)
      if [ -f "$config" ]; then
        remove_codex
      else
        echo "There's no config file at $config, so there was nothing to remove."
      fi ;;
  esac
  drop_link
  exit 0
fi

[ -n "$uv" ] || refuse "I can't find uv. Install it with brew install uv, then run this again."
[ -d "$skill_dir" ] || refuse "I can't find the skill folder at $skill_dir."
check_link
build_env
case "$mode" in
  opencode) edit_opencode add || exit 1 ;;
  codex) add_codex ;;
esac
make_link
exit 0
