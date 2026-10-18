#!/usr/bin/env bash
# Check changed Markdown documents against docs/INDEX.md without staging files.
# Default/--staged: compare staged documents with the staged index (commit hook).
# --worktree: check staged, unstaged, and untracked documents against the working index.

set -euo pipefail

usage() {
  printf 'Usage: bash scripts/check-docs-index.sh [--staged|--worktree]\n'
}

MODE="${1:---staged}"
if [ "$#" -gt 1 ]; then
  usage >&2
  exit 2
fi
case "$MODE" in
  --staged|--worktree) ;;
  --help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"
INDEX="docs/INDEX.md"

# Archives are historical evidence; the index does not need to link to itself.
is_exempt() {
  case "$1" in
    docs/archive/*|docs/INDEX.md) return 0 ;;
    *) return 1 ;;
  esac
}

# NUL-delimited paths preserve filenames containing spaces or other shell characters.
changed_docs() {
  git diff --cached --name-only --diff-filter=ACMR -z -- 'docs/*.md' 'docs/**/*.md'
  if [ "$MODE" = "--worktree" ]; then
    git diff --name-only --diff-filter=ACMR -z -- 'docs/*.md' 'docs/**/*.md'
    git ls-files --others --exclude-standard -z -- 'docs/*.md' 'docs/**/*.md'
  fi
}

DOCS=()
while IFS= read -r -d '' file; do
  is_exempt "$file" && continue
  if [ "$MODE" = "--worktree" ] && [ ! -f "$file" ]; then
    continue
  fi
  DOCS+=("$file")
done < <(changed_docs | sort -zu)
# A failed Git read must not look like an empty set of changed documents.
wait "$!"

if [ "${#DOCS[@]}" -eq 0 ]; then
  printf 'No documentation changes to check (%s).\n' "$MODE"
  exit 0
fi

if [ "$MODE" = "--worktree" ]; then
  if [ ! -f "$INDEX" ]; then
    printf 'Missing working %s; cannot check changed documents.\n' "$INDEX" >&2
    exit 1
  fi
  INDEX_CONTENT=$(cat "$INDEX")
elif ! INDEX_CONTENT=$(git show ":$INDEX" 2>/dev/null); then
  printf 'Missing staged %s; cannot check staged documents.\n' "$INDEX" >&2
  exit 1
fi

MISSING=()
for file in "${DOCS[@]}"; do
  base=$(basename "$file" .md)
  if ! grep -qF -- "$file" <<< "$INDEX_CONTENT" && \
     ! grep -qF -- "$base" <<< "$INDEX_CONTENT"; then
    MISSING+=("$file")
  fi
done

if [ "${#MISSING[@]}" -gt 0 ]; then
  printf 'Documents missing from %s (%s):\n' "$INDEX" "$MODE" >&2
  printf '  - %s\n' "${MISSING[@]}" >&2
  printf 'Add references to the matching index before repeating this check.\n' >&2
  exit 1
fi

printf 'Checked %d document(s) against %s (%s).\n' "${#DOCS[@]}" "$INDEX" "$MODE"
