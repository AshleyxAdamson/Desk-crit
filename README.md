# Desk Crit

**Your coding agent can't see your screen. Until now, you had to describe every button in words. Now you record your screen, talk, and point.**

Desk Crit is a plugin for Claude Code, and a skill plus a local MCP server for OpenCode, Codex and any agent that speaks MCP. It's for giving feedback on an app or a website. You record your screen with your microphone on, scroll through the work and say what you'd change, pointing with the cursor as you go. Your agent transcribes it, takes a screenshot at each moment you pointed at something, and turns what you said into numbered notes. Each note has a time and a screenshot. The agent shows them in chat, saves them to a file if you say yes, and works through the fixes in your code if you say yes.

It's a desk crit, like in design school. Someone sits at your desk and you talk through the work together. Here the agent made the work and you're the critic. It's free, with no subscription and no API keys to buy: the speech model runs on your Mac (Apple Silicon for now). Your recording never leaves your machine.

## Why it's different

Feedback for a coding agent usually means typing out where each thing is and what's wrong with it, or selecting elements in the DOM one at a time. Both are slow, and both strip out what you actually meant.

- **The agent sees what you pointed at.** When you say "this" or "over here", the agent finds the exact moment in the recording and looks at what was on screen. You don't have to name the element.
- **No DOM selecting.** You scroll and talk. Nothing to click, inspect or copy.
- **Your words, as you said them.** Each note quotes you. The agent never makes an ask stronger or weaker than you said it, and if you take something back later, the note follows what you said last.
- **Keep notes protect what you like.** When you say "I love this", it becomes a note that says don't touch it, and the fixes leave that part alone.
- **Nothing uploads.** The speech model runs on your Mac. The agent reads the transcript and the screenshots it takes, and that's all.

## What it does

- **You record.** Cmd-Shift-5, with a microphone on. Scroll through the app, talk, point.
- **The agent reads it all first.** The whole transcript, before any note is written, so a later "ignore what I said about the header" is caught.
- **The agent splits it into notes.** Each one is a change, a bug, a question or something to keep, with a start time, an end time and your words.
- **The agent takes screenshots.** One at the start of each note and one at each pointing word, zoomed in when the thing is small. It names the element on screen, or asks you if it can't tell.
- **You get the notes.** In chat, numbered. Saved to a file if you say yes.
- **The agent works through the fixes.** One note at a time, in your code, if you say yes. Keep notes are left alone.

## Start

You need a Mac with Apple Silicon, `brew install uv ffmpeg`, and an agent: Claude Code 2.1.78 or newer, or one of the others in "Other agents" below.

It also needs one model on your Mac. It downloads once, the first time you use Desk Crit, and only after you say yes:

| Model | What it does | Size | From |
|---|---|---|---|
| NVIDIA Parakeet TDT 0.6B v2 | Writes the transcript: the words you said, with punctuation. | 2.47 GB | Hugging Face |

Then install:

```sh
claude plugin marketplace add AshleyxAdamson/Desk-crit
claude plugin install desk-crit@desk-crit
```

To record, press Cmd-Shift-5, open Options and choose a microphone. Then record the screen, scroll through your app and talk. Point at things with the cursor as you say them: the cursor is how the agent knows what "this" means. A recording with no voice has nothing to transcribe.

Start a session and tell Claude Code:

> Use desk-crit on ~/Movies/app-walkthrough.mov

In Claude Code, the first session also builds the plugin's Python environment (about 530 MB). `TOOLS.md` is the tool contract, and [PRIVACY.md](PRIVACY.md) and [SECURITY.md](SECURITY.md) cover privacy and security.

### Things to try

> Use desk-crit on ~/Movies/app-walkthrough.mov. Show me the notes first, and don't touch the code yet.

> Use desk-crit on ~/Desktop/checkout-review.mov, save the notes, then fix the ones marked change.

> Use desk-crit on ~/Movies/landing-page.mov. Skip everything after 4:30, that's my inbox.

## Other agents

The skill and the server work in OpenCode, Codex and any agent that speaks MCP. From a checkout of this repo, run one of these:

```sh
sh tools/setup-agent.sh opencode
sh tools/setup-agent.sh codex
sh tools/setup-agent.sh print
```

The first run builds the server's Python environment with `uv sync`. It's about 530 MB and takes a few minutes. After that, setup takes a second.

| Command | What it changes |
|---|---|
| `opencode` | Adds a `desk-crit` entry under `mcp` in `~/.config/opencode/opencode.json`, or creates the file. Saves your old file as `opencode.json.bak-desk-crit` the first time. Links `~/.config/opencode/skills/desk-crit` to the skill in this checkout. |
| `codex` | Adds a `[mcp_servers.desk-crit]` block to the end of `~/.codex/config.toml`, or creates the file. Saves your old file as `config.toml.bak-desk-crit` the first time. Links `~/.agents/skills/desk-crit` to the skill in this checkout. |
| `print` | Changes nothing. Prints the launch command, the skill folder and a JSON snippet for Cursor and Gemini CLI. |

Run it twice and the second run changes nothing. It leaves a real folder at the skill path alone and says so. To undo it, add `--remove`:

```sh
sh tools/setup-agent.sh opencode --remove
sh tools/setup-agent.sh codex --remove
```

That takes out the entry and the link. It never deletes a backup. The Python environment stays in `server/.venv`.

If you open your agent in a checkout of this repo, `.agents/skills/desk-crit` points at the skill, so Codex and OpenCode find it without the link. They still need the server entry from the commands above.

For Cursor, put this in `.cursor/mcp.json`. For Gemini CLI, put it in `~/.gemini/settings.json`. Use the paths `print` shows you, and run the `uv sync` it lists once first:

```json
{
  "mcpServers": {
    "desk-crit": {
      "command": "/absolute/path/to/uv",
      "args": ["run", "--locked", "--project", "/path/to/Desk-crit/server", "desk-crit-server"],
      "env": { "HF_HUB_DISABLE_TELEMETRY": "1" }
    }
  }
}
```

Your agent may show the tools with a prefix. OpenCode calls them `desk-crit_frames`, `desk-crit_transcribe` and so on. The skill uses the short names, which are the part after the prefix.

## What it runs and fetches

- **On your Mac:** a local MCP server, `ffmpeg` and `ffprobe` for taking screenshots, and the speech model. A check at the start of each session looks for `uv` and `ffmpeg`, and the first time it builds the plugin's Python environment with `uv`.
- **Downloads, once:** the locked Python packages from PyPI through `uv`, plus a Python build from `github.com/astral-sh/python-build-standalone` if your Mac has none. Parakeet from `huggingface.co`, only after you say yes. Each model file is checked against a pinned size and sha256.
- **What leaves your Mac:** nothing of yours, except what your agent reads in the conversation. That's the transcript text and the screenshots the agent takes of your recording with `frames`. A screen recording can show emails, names or keys, so if something on screen is private, tell the agent which parts to skip. That text and those screenshots go wherever your agent sends its requests. Your video and audio never upload.

**Privacy:** Desk Crit collects nothing. Your recording, transcript and screenshots stay on your Mac. [Privacy policy](https://github.com/AshleyxAdamson/Desk-crit/blob/main/PRIVACY.md).

Desk Crit is a spin-off of [Lumr Studio](https://github.com/AshleyxAdamson/lumr-studio), the editing plugin for talking-head video. The two share a transcript file, so a recording transcribed by one is never transcribed again by the other.

PolyForm Noncommercial 1.0.0. See `LICENSE`.
