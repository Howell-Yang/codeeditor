#!/bin/bash
# Rebuild editor/static/vendor/codemirror from the official npm package.
# Usage: scripts/vendor_codemirror.sh [version]
set -euo pipefail
VERSION="${1:-5.65.21}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="$ROOT/editor/static/vendor/codemirror"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

FILES=(
  lib/codemirror.js
  mode/python/python.js
  mode/clike/clike.js
  mode/javascript/javascript.js
  addon/edit/closebrackets.js
  addon/edit/matchbrackets.js
  addon/comment/comment.js
  addon/selection/active-line.js
)

(cd "$WORK" && npm pack "codemirror@$VERSION" --silent >/dev/null && tar xzf "codemirror-$VERSION.tgz")
PACKAGE="$WORK/package"
mkdir -p "$TARGET"
{
  printf '/* CodeMirror %s (https://codemirror.net/5/), MIT licence - see LICENSE in this folder.\n' "$VERSION"
  printf ' * This file is the unmodified concatenation of the following files of the npm package\n'
  printf ' * "codemirror@%s", in this order (see scripts/vendor_codemirror.sh):\n' "$VERSION"
  for file in "${FILES[@]}"; do printf ' *   %s\n' "$file"; done
  printf ' */\n'
  for file in "${FILES[@]}"; do
    printf '\n/* ---- %s ---- */\n' "$file"
    cat "$PACKAGE/$file"
    printf '\n'
  done
} > "$TARGET/codemirror.bundle.js"
cp "$PACKAGE/lib/codemirror.css" "$TARGET/codemirror.css"
cp "$PACKAGE/LICENSE" "$TARGET/LICENSE"
echo "CodeMirror $VERSION -> $TARGET"
