---
name: desk-crit
description: Use when a designer has a screen recording of an app or website with their spoken feedback, pointing at things with the cursor, and wants it turned into notes Claude can act on: each note tied to a time in the recording and a screenshot of what they were pointing at.
---

# Desk crit

A desk crit is when someone sits at your desk and talks through your work with you. Here the creator is the critic and you made the work. You sit at their desk: they scroll through the app, talk, and point with the cursor, and you take it all down. You read what they said, find the moment each thing was on screen, look at it, and write down what they want. Their meaning stays what it was. You never make an ask stronger or weaker than they said it.

You can't hear the recording, and you can't see it until you take a screenshot. Check what's on screen before you name it.

## Ground rules

- Use the Desk Crit tools for the recording. Ordinary file and code tools are fine for the notes file and for fixes, but only after the creator says yes.
- Never delete a file. If the creator wants old screenshots cleared, tell them the folder and let them do it.
- The recording is never changed.
- To wait for a job, call `job_status` with `wait: 50`. Call it again if it's still running.
- Say this once, in your first reply: every screenshot you take goes into this conversation, the same way the transcript does. A screen recording can show emails, names, keys, or anything else that was open. If something on screen is private, the creator can tell you which parts to skip, and you'll leave those out.

## 0. Say up front what it needs

Desk Crit uses one speech model on this Mac, and the first time it has to download. Say so in your first reply, in a few short lines, before you ask for the recording:

- **Parakeet** (NVIDIA Parakeet TDT 0.6B v2, 2.47 GB from Hugging Face) writes the transcript: the words they said, with punctuation.
- It downloads once and is kept for every recording after. Nothing downloads until they say yes, and you'll ask before it does.

Skip this if the creator says they've used Desk Crit or Lumr Studio on this Mac before, or if a `transcribe` answer in this conversation already came back without `needs_models`.

## 1. Get the recording

Ask for the path to the recording. If they haven't recorded yet, tell them how: press Cmd-Shift-5, open Options, and choose a microphone. Then scroll through the app while talking, and point at things with the cursor as they go. A recording with no voice has nothing to transcribe.

Call `transcribe` with the path. If the model isn't on this Mac yet, it answers `needs_models` and downloads nothing.

| The answer | Do |
|---|---|
| `status: "needs_models"` | Nothing has downloaded, and nothing does until the creator says yes. Ask first. See below. |
| `status: "downloading"` with a `job_id` | The creator said yes and the download started. Wait on `job_status`, and say how far along it is. When it's done, call `transcribe` again with the same path. |
| `job_id` | Wait on `job_status`. Tell the creator it's transcribing, about a minute for a 20 minute recording. |
| `status: "exists"` | Go on. |

On `needs_models`, ask before you do anything else. In a few short lines say what would download, from `models`: the name, what it does, its size in MB, where it comes from (`source_host`) and its license. If you already told them about the model in step 0, keep this short and just ask. Then stop and wait for the answer.

- **Yes:** call `transcribe` with the same path and `download_models` set to true. Wait on `job_status` with `wait: 50`, and call it again while it runs. Say the progress when you have it. If the job fails, tell the creator the error in one sentence and offer to try again. A stopped download picks up where it left off.
- **No:** there's no transcript and nothing to read. Say so, say the speech model is what's needed, and stop. Don't download and don't look for another way.

## 2. Read the whole recording first

Call `read_transcript` from the start. Each response ends with `NEXT <time>` or `END`. On `NEXT`, call again with that time as `start`. Keep going until you see `END`.

Don't write a note until you've read all of it. People take things back later: "actually, ignore what I said about the header." A note written on half the recording can be wrong.

While you read, notice where the creator moves from one screen to the next, and what they say about each. You'll use that to name the screens.

## 3. Split it into notes

A note is one thing the creator wants changed, a bug, a question, or something they like.

- One sentence can hold two notes. Several sentences can be one note.
- A line like "okay, now I'm scrolling down to pricing" isn't a note. It says where they are. Use it to name the screen.
- Keep their words. Quote briefly. Never make the ask stronger or weaker than they said it. "Maybe a bit bigger" is not "make it huge".
- If they take something back later, drop that note, or mark it as changed and keep the last thing they said.
- Each note gets a start time and an end time, from the start and end times printed on the transcript lines.

## 4. Take screenshots

For each note, call `frames`. It takes source seconds and shows the frame that was on screen at each one. Take:

- one at the start of the note;
- one at each pointing word: "this", "here", "that", "these", "this button". Call `find_words` with those words to get their exact times. The cursor is usually on the thing as the creator says the word;
- if they're talking about something they just scrolled past ("that last section"), one more about 1 to 2 seconds before the note starts;
- if the screen changes during a long note, one in the middle and one at the end too.

`frames` takes up to 6 times a call, so batch the notes next to each other. Use `region` to zoom in when the thing is small, such as small text, an icon or a form field. It takes `[left, top, right, bottom]` as fractions of the frame, from 0 to 1.

Don't take screenshots of stretches the creator said to skip.

## 5. Look, and name the element

For each note, look at its screenshots. Say what's on screen and which element the creator means: its visible text, where it sits on the screen, and where the cursor is.

If you can't tell which element they mean, mark the note "unclear" and ask. Don't guess. A wrong guess sends the fix to the wrong place.

## 6. Show the notes in chat

Write a numbered list. For each note:

- The time. Use the `clock` value from `frames`. Never work out minutes and seconds yourself.
- Their words, quoted briefly.
- What's on screen and which element.
- The ask, in one plain line.
- The kind: **change**, **bug**, **question**, or **keep**. Keep means they like it and it shouldn't change.
- The screenshot paths.

Then:

- The unclear notes, each as a short question.
- One line offering to save the notes as a file, and to start on the fixes. Both only if they want.

## 7. Save the notes, only on a yes

Wait for a yes. On a yes, write `desk-crit/<video stem>.md` in the project the creator is working in, or where they say. The video stem is the recording's file name without its extension.

Write each note as a section: the time, their words, the screen and element, the ask, the kind, and the screenshots linked by the paths where `frames` saved them. Don't copy the screenshots unless they ask.

Never overwrite a file. If the name is taken, add `-2`, then `-3`, and so on.

## 8. Fix them, only on a yes

Wait for a yes. On a yes, work in the app's code one note at a time.

- Find the component from the screenshot. Search the code for the visible text first: a button label, a heading, a placeholder.
- Say which note each change is for. Keep each change small and about that one note.
- Skip the keep notes, and don't touch those parts.
- Skip any note still marked unclear until the creator has answered.
- When you're done, say what you changed for each note. If the creator can run the app, offer to compare it against the screenshot.

## Handling tool errors

Read the error message. It says what was wrong. Fix the call and retry once. If it fails again, tell the creator plainly what's wrong and what you'd need from them. Don't retry silently more than once, and don't guess at a fix you can't explain.
