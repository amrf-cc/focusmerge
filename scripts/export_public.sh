#!/usr/bin/env bash
# Build a clean, single-commit snapshot of the current HEAD for publishing, without the private
# history or the author's real e-mail address. Nothing is pushed or published by this script.
#
#   bash scripts/export_public.sh              -> dist/public/focusmerge (a new git repo, one commit)
#
# Then, only when you decide to publish:  cd dist/public/focusmerge && git remote add origin <url> && git push -u origin main
set -euo pipefail
cd "$(dirname "$0")/.."

NAME="${PUBLIC_GIT_NAME:-amrf-cc}"
EMAIL="${PUBLIC_GIT_EMAIL:-259136817+amrf-cc@users.noreply.github.com}"
OUT="dist/public/focusmerge"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' FocusMerge.lrdevplugin/engine/fm/__init__.py)"

[[ -z "$(git status --porcelain)" ]] || { echo "ERROR: commit or stash your changes first (the snapshot is built from HEAD)." >&2; exit 1; }

rm -rf "$OUT"; mkdir -p "$OUT"
git archive HEAD | tar -x -C "$OUT"

# ---- scan the snapshot ---------------------------------------------------------------------------
fail=0
scan() {  # scan <label> <extended-regex>
  local hits
  hits="$(grep -rIEn --exclude-dir=.git --exclude=export_public.sh "$2" "$OUT" || true)"
  if [[ -n "$hits" ]]; then echo "FOUND ($1):"; echo "$hits" | head -20; fail=1; fi
}
scan "private e-mail"        'riveraferran\.com'
scan "local user path"       '/Users/adrianriveraferran'
scan "personal name in paths" 'adrianriveraferran'
scan "personal volume names" 'Chip SSD|Gyro King|Doordash'
scan "secrets"               'ghp_[A-Za-z0-9]{20,}|github_pat_|AKIA[0-9A-Z]{16}|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY|sk-[A-Za-z0-9]{20,}'
big="$(find "$OUT" -type f -size +2M | head)"
[[ -z "$big" ]] || { echo "FOUND (files over 2 MB):"; echo "$big"; fail=1; }
raw="$(find "$OUT" -type f -not -path '*/docs/images/*' \( -iname '*.raf' -o -iname '*.tif' -o -iname '*.tiff' -o -iname '*.dng' -o -iname '*.jpg' \) | head)"
[[ -z "$raw" ]] || { echo "FOUND (photo files):"; echo "$raw"; fail=1; }
(( fail == 0 )) || { echo "ERROR: snapshot scan failed; nothing was committed." >&2; exit 1; }
echo "scan: clean"

# ---- one commit, neutral identity ----------------------------------------------------------------
git -C "$OUT" init -q -b main
git -C "$OUT" add -A
GIT_AUTHOR_NAME="$NAME" GIT_AUTHOR_EMAIL="$EMAIL" GIT_COMMITTER_NAME="$NAME" GIT_COMMITTER_EMAIL="$EMAIL" \
  git -C "$OUT" -c commit.gpgsign=false commit -q -m "FocusMerge $VERSION"
git -C "$OUT" tag "v$VERSION"
echo "snapshot ready: $OUT  (1 commit, tag v$VERSION, author $NAME <$EMAIL>)"
echo "log:"; git -C "$OUT" log --format='  %h %an <%ae> %s'
