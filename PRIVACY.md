# Privacy

Last updated 2026-10-05.

Desk Crit doesn't collect your data. It has no server, no accounts, no analytics and no telemetry. Nothing on your Mac is ever sent to the person who wrote it.

Questions about this page go to [GitHub Issues](https://github.com/AshleyxAdamson/Desk-crit/issues). The page lives at `PRIVACY.md` in [the repo](https://github.com/AshleyxAdamson/Desk-crit/blob/main/PRIVACY.md), and its history is public.

## What stays on your Mac

- Your recording and its audio. The plugin opens a file only when you name it to Claude, like "use desk-crit on this recording". It never browses your folders. It never uploads your recording or its audio, and it has no tool that could.
- What it makes from them. The transcript and the screenshots. The section below says where each one lives.

## What Claude sees

Desk Crit runs inside Claude Code, so Claude is part of the conversation. The plugin adds these to it:

- The transcript text, in short packed lines.
- The results of each tool.
- The screenshots Claude takes with `frames`. They're whole frames from your recording, at the times Claude picks. In a screen recording that means anything that was visible on screen then, such as emails, names or keys. If something on screen is private, tell Claude which parts to skip.

That goes wherever your Claude Code sends its requests, under your Claude plan's terms. Anthropic's policy is at https://www.anthropic.com/legal/privacy. The plugin doesn't add a second copy, and its author doesn't get one.

## What the plugin downloads

Nothing of yours goes out in these. Each host sees the request itself: your IP address and the file you asked for, as with any download. The plugin's own downloads send one fixed name as the user agent, `desk-crit-plugin`, and no ID.

| Host | What | When |
|---|---|---|
| `pypi.org`, `files.pythonhosted.org` | The plugin's locked Python packages, through `uv` | The first session, in the background, and again when an update changes the package list |
| `github.com/astral-sh/python-build-standalone` | A Python build, only if your Mac has none from 3.11 to 3.13 | The same first session |
| `huggingface.co`, and the download hosts it redirects to, such as `us.aws.cdn.hf.co` | The speech model, `mlx-community/parakeet-tdt-0.6b-v2`, at one pinned commit | Only after you say yes |

Hugging Face's usage telemetry is switched off (`HF_HUB_DISABLE_TELEMETRY=1`). Every model file is checked against a size and a sha256 pinned in the plugin before it's installed.

The first-run check may print `https://brew.sh` in a message when Homebrew is missing. It doesn't visit it.

## What's kept, and for how long

The plugin keeps files on your Mac until you delete them. The author holds nothing, so there's nothing to ask the author to delete.

| What | Where | To remove it |
|---|---|---|
| Project files: `receipts.jsonl`, `frames/` | `~/Lumr/desk-crit/projects/<recording name>-<id>/`. The `~/Lumr` part follows `LUMR_HOME`, or the folder named in `~/.config/lumr/home.txt`. | Delete the project folder |
| The transcript | `<recording>.words.json`, beside your recording. It's the only file the plugin puts in your recording's folder. Lumr Studio writes and reads the same file. | Delete the file |
| The Python environment and `build.log` | The plugin's data folder, under `~/.claude/plugins/data/` | Uninstalling the plugin removes it |
| The speech model | `~/.cache/huggingface/hub`, or under `HF_HOME` | Delete it by hand. It stays after an uninstall, because other tools share that cache. |

No tool in the plugin deletes a file of yours.

## Children

The plugin collects nothing from anyone, whatever their age. It's built for people who give feedback on their own work with Claude Code.

## Changes

If this page changes, the change shows in the repo's history and the date at the top moves. A new host or a new kind of data changes this page in the same release.
