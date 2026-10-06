#!/bin/sh
# Copies the files Desk Crit shares with Lumr Studio into server/desk_crit/.
#
#   tools/sync-from-lumr.sh           write the copies
#   tools/sync-from-lumr.sh --check   exit 1 when a copy differs from what the script would write
#
# The sources are read from ${LUMR_STUDIO_DIR:-<this repo>/../lumr-studio}, in
# server/lumr_studio/. Nothing there is changed. Each copy is the source with
# a rewrite table applied (below) and one header line put in front, so a fix
# made in Lumr Studio reaches Desk Crit with one command. tests/test_copies.py
# runs --check, so the suite fails until the copies are written again.
# Plain POSIX sh, so it runs on the sh macOS ships.

set -eu

case "$0" in
  */*) here=${0%/*} ;;
  *) here=. ;;
esac
repo=$(cd "$here/.." && pwd)
lumr=${LUMR_STUDIO_DIR:-$repo/../lumr-studio}
src=$lumr/server/lumr_studio
dest=$repo/server/desk_crit

# The copied files, relative to the package folder. Every other file in
# server/desk_crit/ is written by hand.
FILES="errors.py jobs.py mcp_results.py transcript.py frames.py speech/__init__.py speech/__main__.py speech/transcribe.py speech/ui.py"

mode=write
case "${1:-}" in
  "") ;;
  --check) mode=check ;;
  *) echo "usage: sync-from-lumr.sh [--check]" >&2; exit 2 ;;
esac

if [ ! -d "$src" ]; then
  echo "sync-from-lumr.sh: no Lumr Studio sources at $src" >&2
  echo "Set LUMR_STUDIO_DIR to the lumr-studio folder." >&2
  exit 2
fi

# Writes the copy of $1 to stdout: the header, then the source with the
# rewrite table applied, in this order:
#   1. every lumr_studio becomes desk_crit
#   2. frames.py: the two imports Desk Crit's ffmpeg.py answers
#   3. jobs.py: the one sentence that names tools Desk Crit doesn't have
render() {
  rel=$1
  printf '# COPIED by tools/sync-from-lumr.sh from lumr-studio/server/lumr_studio/%s. Do not edit: change it in Lumr Studio and run the script.\n' "$rel"
  case "$rel" in
    frames.py)
      sed -e 's/lumr_studio/desk_crit/g' \
          -e 's/from desk_crit\.edit import clock/from desk_crit.ffmpeg import clock/' \
          -e 's/from desk_crit\.look import MAX_FILE_BYTES, MAX_LONG_EDGE, _run/from desk_crit.ffmpeg import MAX_FILE_BYTES, MAX_LONG_EDGE, _run/' \
          "$src/$rel"
      ;;
    jobs.py)
      sed -e 's/lumr_studio/desk_crit/g' \
          -e 's/Job ids come from transcribe, preview and render;/Job ids come from transcribe;/' \
          "$src/$rel"
      ;;
    *)
      sed -e 's/lumr_studio/desk_crit/g' "$src/$rel"
      ;;
  esac
}

# A source that Lumr Studio changed so a rewrite no longer matches would copy
# quietly and then fail at import. Say so here instead.
verify() {
  rel=$1
  out=$2
  case "$rel" in
    frames.py)
      grep -q '^from desk_crit\.ffmpeg import clock$' "$out" \
        && grep -q '^from desk_crit\.ffmpeg import MAX_FILE_BYTES, MAX_LONG_EDGE, _run$' "$out" \
        || { echo "sync-from-lumr.sh: frames.py's imports changed in Lumr Studio. Update the rewrite table." >&2; return 1; }
      ;;
    jobs.py)
      grep -q 'Job ids come from transcribe;' "$out" \
        || { echo "sync-from-lumr.sh: jobs.py's job-id sentence changed in Lumr Studio. Update the rewrite table." >&2; return 1; }
      ;;
  esac
  if tail -n +2 "$out" | grep -q 'lumr_studio'; then
    echo "sync-from-lumr.sh: $rel still names lumr_studio after the rewrite." >&2
    return 1
  fi
}

work=$(mktemp -d "${TMPDIR:-/tmp}/desk-crit-sync.XXXXXX")
trap 'rm -rf "$work"' EXIT

status=0
for rel in $FILES; do
  if [ ! -f "$src/$rel" ]; then
    echo "sync-from-lumr.sh: $src/$rel is missing." >&2
    exit 2
  fi
  want=$work/$(printf '%s' "$rel" | tr '/' '_')
  render "$rel" > "$want"
  verify "$rel" "$want" || exit 2
  if [ "$mode" = check ]; then
    if [ ! -f "$dest/$rel" ]; then
      echo "missing: server/desk_crit/$rel"
      status=1
    elif ! cmp -s "$want" "$dest/$rel"; then
      echo "differs: server/desk_crit/$rel"
      status=1
    fi
  else
    mkdir -p "$dest/$(dirname "$rel")"
    cp "$want" "$dest/$rel"
    echo "wrote server/desk_crit/$rel"
  fi
done

if [ "$mode" = check ] && [ "$status" -ne 0 ]; then
  echo "Run tools/sync-from-lumr.sh to write the copies again." >&2
fi
exit "$status"
