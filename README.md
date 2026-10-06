# Desk Crit

**Your coding agent can't see your screen. Until now, you had to describe every button in words. Now you record your screen, talk, and point.**

Desk Crit is a Claude Code plugin for giving feedback on an app or a website. You record your screen with your microphone on, scroll through the work and say what you'd change, pointing with the cursor as you go. Claude transcribes it, takes a screenshot at each moment you pointed at something, and turns what you said into numbered notes. Each note has a time and a screenshot. Claude shows them in chat, saves them to a file if you say yes, and works through the fixes in your code if you say yes.

It's a desk crit, like in design school. Someone sits at your desk and you talk through the work together. Here the agent made the work and you're the critic. It's free, with no subscription and no API keys to buy: the speech model runs on your Mac (Apple Silicon for now). Your recording never leaves your machine.

## Why it's different

Feedback for a coding agent usually means typing out where each thing is and what's wrong with it, or selecting elements in the DOM one at a time. Both are slow, and both strip out what you actually meant.

- **The agent sees what you pointed at.** When you say "this" or "over here", Claude finds the exact moment in the recording and looks at what was on screen. You don't have to name the element.
- **No DOM selecting.** You scroll and talk. Nothing to click, inspect or copy.
- **Your words, as you said them.** Each note quotes you. Claude never makes an ask stronger or weaker than you said it, and if you take something back later, the note follows what you said last.
- **Keep notes protect what you like.** When you say "I love this", it becomes a note that says don't touch it, and the fixes leave that part alone.
- **Nothing uploads.** The speech model runs on your Mac. Claude reads the transcript and the screenshots it takes, and that's all.

## What it does

- **You record.** Cmd-Shift-5, with a microphone on. Scroll through the app, talk, point.
- **Claude reads it all first.** The whole transcript, before any note is written, so a later "ignore what I said about the header" is caught.
- **Claude splits it into notes.** Each one is a change, a bug, a question or something to keep, with a start time, an end time and your words.
- **Claude takes screenshots.** One at the start of each note and one at each pointing word, zoomed in when the thing is small. It names the element on screen, or asks you if it can't tell.
- **You get the notes.** In chat, numbered. Saved to a file if you say yes.
- **Claude works through the fixes.** One note at a time, in your code, if you say yes. Keep notes are left alone.

## Start

You need a Mac with Apple Silicon, Claude Code 2.1.78 or newer, and `brew install uv ffmpeg`.

It also needs one model on your Mac. It downloads once, the first time you use Desk Crit, and only after you say yes:

| Model | What it does | Size | From |
|---|---|---|---|
| NVIDIA Parakeet TDT 0.6B v2 | Writes the transcript: the words you said, with punctuation. | 2.47 GB | Hugging Face |

Then install:

```sh
claude plugin marketplace add AshleyxAdamson/Desk-crit
claude plugin install desk-crit@desk-crit
```

To record, press Cmd-Shift-5, open Options and choose a microphone. Then record the screen, scroll through your app and talk. Point at things with the cursor as you say them. A recording with no voice has nothing to transcribe.

Start a session and tell Claude Code:

> Use desk-crit on ~/Movies/app-walkthrough.mov

The first session also builds the plugin's Python environment (about 530 MB). `TOOLS.md` is the tool contract, and [PRIVACY.md](PRIVACY.md) and [SECURITY.md](SECURITY.md) cover privacy and security.

### Things to try

> Use desk-crit on ~/Movies/app-walkthrough.mov. Show me the notes first, and don't touch the code yet.

> Use desk-crit on ~/Desktop/checkout-review.mov, save the notes, then fix the ones marked change.

> Use desk-crit on ~/Movies/landing-page.mov. Skip everything after 4:30, that's my inbox.

## What it runs and fetches

- **On your Mac:** a local MCP server, `ffmpeg` and `ffprobe` for taking screenshots, and the speech model. A check at the start of each session looks for `uv` and `ffmpeg`, and the first time it builds the plugin's Python environment with `uv`.
- **Downloads, once:** the locked Python packages from PyPI through `uv`, plus a Python build from `github.com/astral-sh/python-build-standalone` if your Mac has none. Parakeet from `huggingface.co`, only after you say yes. Each model file is checked against a pinned size and sha256.
- **What leaves your Mac:** nothing of yours, except what Claude reads in the conversation. That's the transcript text and the screenshots Claude takes of your recording with `frames`. A screen recording can show emails, names or keys, so if something on screen is private, tell Claude which parts to skip. Your video and audio never upload.

**Privacy:** Desk Crit collects nothing. Your recording, transcript and screenshots stay on your Mac. [Privacy policy](https://github.com/AshleyxAdamson/Desk-crit/blob/main/PRIVACY.md).

Desk Crit is a spin-off of [Lumr Studio](https://github.com/AshleyxAdamson/lumr-studio), the editing plugin for talking-head video. The two share a transcript file, so a recording transcribed by one is never transcribed again by the other.

PolyForm Noncommercial 1.0.0. See `LICENSE`.
