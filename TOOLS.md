# Desk Crit tool contract

The agreement between the MCP server and the skill. The server implements
these tools. The skill tells Claude how to use them. Change this file first,
then the code.

Scope: one screen recording, from the file on disk to a set of screenshots
Claude can look at. Nothing is cut or rendered, and the recording is never
changed.

## What the plugin offers

The server lists five tools: `transcribe`, `read_transcript`, `find_words`,
`frames` and `job_status`. There are no extras and nothing switched off.

## Rules for every tool

- Every tool takes `video_path`, an absolute path to the recording, except
  `job_status`.
- The recording is never modified or overwritten.
- Results are short. A tool returns a summary and file paths. It never returns
  a whole transcript.
- Failures return an error that says what was wrong and how to fix it, so
  Claude can correct the call and retry.
- Times are seconds in the recording, as floats.
- Long work returns a `job_id` at once. `job_status` reports on it.
- Every tool carries `title`, `readOnlyHint`, and `destructiveHint`.
- No tool deletes or overwrites a file.

## Project folder

One per recording, created on first use:

```
<LUMR_HOME>/desk-crit/projects/<recording stem>-<first 12 hex of sha1 of the resolved path>/
  receipts.jsonl     one line per completed step
  frames/            screenshots of the recording made by `frames`
```

`LUMR_HOME` is `~/Lumr` unless the environment variable `LUMR_HOME` or the
folder named in `~/.config/lumr/home.txt` says otherwise. The environment
variable `DESK_CRIT_PROJECTS_DIR` overrides the whole projects root. Tests
must set it.

The transcript sits beside the recording, as `<video>.words.json`. It's the
only file the plugin puts in the recording's folder. It's the same sidecar
Lumr Studio writes, so a recording transcribed by one is never transcribed
again by the other.

## Tools

### transcribe

Read only: no. Destructive: no. Long job. Writes `<video>.words.json` beside
the recording.

| Input | Type | Note |
|---|---|---|
| `video_path` | string | |
| `force` | bool, default false | transcribe again even if a transcript exists |
| `download_models` | bool, default false | fetch the model the last answer said was missing. Pass true only after the creator said yes. |

| Answer | When |
|---|---|
| `{job_id}` | there is no transcript yet, or `force` is true. The job writes it. |
| `{status: "exists", words_path, word_count, duration}` | a transcript is already there. Nothing to do. |
| `{status: "needs_models", models, total_mb, note}` | the speech model is not on the machine. Nothing has downloaded and nothing started. See "Models". |
| `{status: "downloading", job_id}` | `download_models` was true and the model was missing. The job fetches it. See "Models". |

When a transcription job is done, its result carries the same fields as
`exists`. The words come with the times the speech model gave them. There's no
second timing model and no field for word times.

#### Models

One model is needed, and it doesn't ship with the plugin.

| Model | Needed when | From | Size | License |
|---|---|---|---|---|
| NVIDIA Parakeet TDT 0.6B v2, the speech recognition | there is no transcript, or `force` is true | `huggingface.co`, `mlx-community/parakeet-tdt-0.6b-v2` at one pinned commit | 2.47 GB | CC-BY-4.0 |

Nothing downloads until the creator says yes. The flow, in one tool:

1. `transcribe` finds the model missing and answers `needs_models`:
   `models` is a list of `{name, size_mb, source_host, license, folder}`,
   `total_mb` adds up the sizes as listed, and `note` tells Claude what to do.
2. Claude tells the creator what would download, how big, from where and under
   which license, and asks. Claude asks before it calls anything again.
3. On a yes, Claude calls `transcribe` with `download_models: true`. The answer
   is `{status: "downloading", job_id}`, and the job's kind is
   `download_models`. `job_status` reports it like any long job, with
   `progress` from 0 to 1 over the bytes of every file still to fetch.
4. When the job is done, Claude calls `transcribe` again with the same
   `video_path`. It goes on as if the model had been there.

A second call with `download_models: true` while the job runs answers with the
same `job_id`. A call with it true when nothing is missing downloads nothing
and goes on. Downloading is the only thing in the plugin that fetches a model:
the speech model reads its files from the pinned folder with Hugging Face set
offline.

How a file arrives. Each file is pinned by exact size and sha256. It is
written to `<name>.part`, checked, then renamed, so a file at its real name is
whole and was checked. A `.part` left by a stopped download is resumed from
its last byte (or started again when the server won't resume). A file that
fails its checksum is deleted and the job fails with the reason. Nothing is
installed, and Claude can offer to try once more. A stopped connection fails
the job and keeps what arrived. Not enough free disk fails it before the first
byte. Files go to the cache other tools share and survive an uninstall:
Hugging Face's (`~/.cache/huggingface/hub`, or `HF_HOME`).

### read_transcript

Read only: yes.

| Input | Type | Note |
|---|---|---|
| `video_path` | string | |
| `start` | float, optional | window start |
| `end` | float, optional | window end |

Returns the transcript as packed plain text, one line per sentence or two,
each prefixed with its start and end in seconds:

```
[12.40-15.85] so the first thing i want to say is that this header feels heavy.
[16.75-19.20] and over here the spacing is off, it's too tight.
```

Windows are contiguous and never overlap. Every response ends with one of two
lines, so the reader always knows whether more remains:

- `NEXT <time>` when the size budget cut the window short. Pass that time as
  `start` to continue.
- `END` when the response reached the last word of the transcript.

### find_words

Read only: yes.

| Input | Type | Note |
|---|---|---|
| `video_path` | string | |
| `words` | list of string | one to five words or short phrases, such as `["this"]` or `["over here", "that one"]`. A phrase is at most four words. |
| `start` | float, optional | go on from a `NEXT <time>` line |

Returns every place each word is said, as packed text. First a line for each
phrase, then one line for each place:

```
this: said 14 times.
over here: said 3 times.
w12.80-13.02 0:12 so the first thing i want to say is [this] header feels heavy
w16.95-17.30 0:16 and [over here] the spacing is off it's too tight
END
```

| Part of a line | Meaning |
|---|---|
| `w12.80-13.02` | the start and end of the word, in seconds |
| `0:12` | the clock time, ready to show |
| words before, `[the word]`, words after | enough to tell which "this" it is |

A longer list ends with `NEXT <time>` instead of `END`. Pass that time as
`start` to continue. This is how Claude finds the exact moment of "this",
"here" and "that".

### frames

Read only: no. Destructive: no. Writes one picture per time into the project's
`frames/`.

| Input | Type | Note |
|---|---|---|
| `video_path` | string | |
| `times` | list of floats, 1 to 6 | seconds, each from 0 to the recording's length |
| `region` | list of 4 floats, optional | `[left, top, right, bottom]` as fractions of the frame (0 to 1), to zoom in on part of the screen. Applies to every time in the call. |

Returns screenshots of the recording, as images Claude can see. For each
time, in the order given, a line such as `Frame 2 of 3 at 1:23 (83.40 s). Find the cursor: what it's on is what the creator means.`
and then that frame. Last comes one compact JSON block:

```json
{"frames": [{"at": 83.4, "shown": 83.367, "clock": "1:23", "path": "...", "width": 1568, "height": 980}],
 "source_size": [2880, 1800], "region": null}
```

`at` is the time asked for. `shown` is the time of the frame that is on
screen then, and is never after `at`. `clock` is `at` as `m:ss`.

Screen recordings (QuickTime, Cmd-Shift-5) only write a frame when the
picture changes, so a still screen can go many seconds without one. The frame
shown is the last one at or before `at`, never the next one after it. The tool
works whether or not the recording has a transcript.

Limits:

- At most 6 times a call. More is an error that says to split the call.
- The long edge is at most 1568 px, after the crop. A picture is never
  scaled up.
- PNG when the file is at most 1 MB, else JPEG at quality 90. A screenshot of
  an app is mostly flat colour, so PNG usually fits and keeps small text
  crisp.
- `region` needs `0 <= left < right <= 1` and `0 <= top < bottom <= 1`, and
  the crop must be at least 64 px on each side in source pixels. Otherwise
  the error says what to pass.
- A time below 0 or past the end is an error that names the recording's
  length.

Files are `frames/frame-<m>m<ss.ss>s-<YYYYmmdd-HHMMSS>.png` (or `.jpg`),
named by `at`. A name that is taken gets `-2`, `-3`, so nothing is
overwritten, and nothing is written beside the recording. One line goes into
`receipts.jsonl`.

### job_status

Read only: yes.

| Input | Type | Note |
|---|---|---|
| `job_id` | string | from `transcribe` |
| `wait` | float, default 0 | seconds to wait for the job to finish before answering, at most 50 |

Returns `{job_id, kind, status, progress, result, error}`.

| Field | Meaning |
|---|---|
| `kind` | `transcribe` or `download_models` |
| `status` | `running`, `done` or `failed` |
| `progress` | 0 to 1, or null when the job can't tell |
| `result` | the job's answer once it's done |
| `error` | what went wrong, when it failed |

Pass `wait` to hold the call open while the job runs, so one call replaces
many. With `wait: 0` it answers at once.
