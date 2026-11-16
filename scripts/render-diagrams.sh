#!/usr/bin/env bash
# Render every ```mermaid block in a Markdown file to image files.
#
# Usage:
#   scripts/render-diagrams.sh [markdown-file] [output-dir] [formats]
#
# Defaults:
#   markdown-file  docs/ARCHITECTURE.md
#   output-dir     docs/diagrams
#   formats        "svg png"   (any of: svg png pdf)
#
# Output files are named after the nearest preceding heading, e.g.
#   docs/diagrams/03-containers.svg
#
# Requirements: Node.js 18+ (npx). The first run downloads mermaid-cli and Chromium.

set -euo pipefail

MERMAID_CLI_VERSION="11.4.2"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
input="${1:-$repo_root/docs/ARCHITECTURE.md}"
out_dir="${2:-$repo_root/docs/diagrams}"
formats="${3:-svg png}"

if [[ ! -f "$input" ]]; then
  echo "error: input file not found: $input" >&2
  exit 1
fi
if ! command -v npx >/dev/null 2>&1; then
  echo "error: npx not found; install Node.js 18+ first" >&2
  exit 1
fi

work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

# Chromium refuses to start as root / in some containers without --no-sandbox.
echo '{"args": ["--no-sandbox"]}' > "$work_dir/puppeteer.json"

# Extract blocks as NN-<heading-slug>.mmd
awk -v dir="$work_dir" '
  function slugify(s) {
    s = tolower(s)
    gsub(/[^a-z0-9]+/, "-", s)
    gsub(/^-+|-+$/, "", s)
    return s
  }
  /^#+ / { heading = $0; sub(/^#+ +/, "", heading); sub(/^[0-9.]+ +/, "", heading) }
  /^```mermaid[[:space:]]*$/ {
    n++
    file = sprintf("%s/%02d-%s.mmd", dir, n, slugify(heading))
    inside = 1
    next
  }
  /^```/ && inside { inside = 0; close(file); next }
  inside { print > file }
' "$input"

shopt -s nullglob
blocks=("$work_dir"/*.mmd)
if (( ${#blocks[@]} == 0 )); then
  echo "No mermaid blocks found in $input"
  exit 0
fi

mkdir -p "$out_dir"
echo "Rendering ${#blocks[@]} diagram(s) from $input -> $out_dir"

failed=0
for block in "${blocks[@]}"; do
  name="$(basename "$block" .mmd)"
  for fmt in $formats; do
    target="$out_dir/$name.$fmt"
    extra=()
    [[ "$fmt" == "png" ]] && extra=(--scale 2 --backgroundColor white)
    if npx -y "@mermaid-js/mermaid-cli@$MERMAID_CLI_VERSION" \
        --puppeteerConfigFile "$work_dir/puppeteer.json" \
        --input "$block" --output "$target" "${extra[@]}" \
        >"$work_dir/log.txt" 2>&1; then
      echo "  ok    $target"
    else
      echo "  FAIL  $target"
      grep -iE "error|parse" "$work_dir/log.txt" | head -5 | sed 's/^/        /'
      failed=1
    fi
  done
done

exit "$failed"
