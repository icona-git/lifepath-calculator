#!/bin/bash
# ICONA Pre-Deploy Security Audit (PDSP) -- v1.0 (2026-08-16)
#
# Canonical copy: ICONA site-register repo -> pdsp/predeploy-audit.sh
# Every project repo carries an IDENTICAL copy at scripts/predeploy-audit.sh
# (verify with:  shasum -a 256 scripts/predeploy-audit.sh  against the canonical).
#
# Usage:  ./scripts/predeploy-audit.sh [base-branch]
#   base-branch defaults to the first that exists: origin/main, main, origin/master, master
# Exit codes:  0 = PASS   1 = FAIL (do not open PR / do not deploy)   2 = could not audit (treat as FAIL)
#
# Gates (see PDSP.md):  1 dependencies  2 secrets  3 malicious patterns  4 diff scope  (5 = human review)
#
# Notes vs the 2026-08-16 PDSP draft (kept the same gates + output format; fixed real defects):
#  - Gate 3 regexes: unescaped "(" made grep -E error out, so eval/base64_decode/assert/gzinflate
#    silently never matched; "\|" is not portable ERE (GNU vs BSD grep differ) -> all patterns rewritten
#  - Base branch resolution fails CLOSED (unknown base = exit 2, not an empty diff that PASSes)
#  - Secret scan limited to this branch's commits and --redact (output gets pasted into chat);
#    built-in fallback patterns run when gitleaks is not installed
#  - npm/composer audit: FAIL on real advisories, WARN (not FAIL) when the tool cannot run
#  - Also audits nested package.json/composer.json (SilverStripe themes/), scans py/sh/ts/yml/svg/ini
#  - Filenames with spaces handled; auditor self-modification and minified/vendored files called out
set -uo pipefail

BASE_ARG="${1:-}"
FAIL=0
WARN=0
TMP=$(mktemp -d "${TMPDIR:-/tmp}/pdsp.XXXXXX") || { echo "FAIL: cannot create temp dir"; exit 2; }
trap 'rm -rf "$TMP"' EXIT

say()  { printf '%s\n' "$*"; }
flag() { say "FLAG: $*"; FAIL=1; }
fail() { say "FAIL: $*"; FAIL=1; }
warn() { say "WARN: $*"; WARN=$((WARN+1)); }

if [ "$BASE_ARG" = "-h" ] || [ "$BASE_ARG" = "--help" ]; then
  sed -n '2,12p' "$0"; exit 0
fi

# ---- repo + base branch (fail closed) ----
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  say "FAIL: not inside a git repository"; exit 2
fi
ROOT=$(git rev-parse --show-toplevel) && cd "$ROOT" || exit 2
REPO=$(basename "$ROOT")
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo detached)

resolve_base() {
  local c
  if [ -n "$BASE_ARG" ]; then
    for c in "$BASE_ARG" "origin/$BASE_ARG"; do
      git rev-parse --verify --quiet "$c^{commit}" >/dev/null 2>&1 && { printf '%s\n' "$c"; return 0; }
    done
    return 1
  fi
  for c in origin/main main origin/master master; do
    git rev-parse --verify --quiet "$c^{commit}" >/dev/null 2>&1 && { printf '%s\n' "$c"; return 0; }
  done
  return 1
}
if ! BASE=$(resolve_base); then
  say "FAIL: base branch not found (tried: ${BASE_ARG:-origin/main main origin/master master})."
  say "      git fetch origin, or pass the base explicitly: $0 <base-branch>"
  exit 2
fi
if ! git merge-base "$BASE" HEAD >/dev/null 2>&1; then
  say "FAIL: no common history between $BASE and HEAD"; exit 2
fi
RANGE="$BASE...HEAD"

echo "=== ICONA PRE-DEPLOY AUDIT (PDSP v1.0) ==="
say "repo: $REPO   branch: $BRANCH   base: $BASE ($(git rev-parse --short "$BASE"))   head: $(git rev-parse --short HEAD)"

# All changed files (any status) and content-scannable ones (added/copied/modified) - NUL-safe
git diff "$RANGE" --name-only -z > "$TMP/all.z"
git diff "$RANGE" --name-only -z --diff-filter=ACM > "$TMP/acm.z"
N_ALL=$(tr -cd '\0' < "$TMP/all.z" | wc -c | tr -d ' ')
say "changed files vs $BASE: $N_ALL"
if [ "$N_ALL" -eq 0 ]; then
  say "NOTHING TO AUDIT: HEAD has no changes vs $BASE. Run this from your rc/* branch (before the PR),"
  say "not from $BASE itself. Dependency/secret gates were NOT run."
  echo "=================================="
  say "RESULT: INCONCLUSIVE - nothing audited (HEAD == $BASE). Not a PASS."
  say "PDSP-SUMMARY: repo=$REPO branch=$BRANCH base=$BASE head=$(git rev-parse --short HEAD) files=0 result=INCONCLUSIVE"
  exit 2
fi

# ---- Gate 1: Dependency audit ----
say "--- [1/4] Dependencies ---"
find_manifests() {
  find . -maxdepth 4 -type f -name "$1" \
    -not -path './.git/*' -not -path '*/node_modules/*' -not -path '*/vendor/*' 2>/dev/null | sort
}
NPM_SEEN=0
while IFS= read -r m; do
  [ -n "$m" ] || continue
  NPM_SEEN=1
  d=$(dirname "$m")
  if [ ! -f "$d/package-lock.json" ] && [ ! -f "$d/npm-shrinkwrap.json" ]; then
    warn "$d: package.json has no package-lock.json - npm audit cannot run (commit a lockfile)"; continue
  fi
  if ! command -v npm >/dev/null 2>&1; then warn "npm not installed - cannot audit $d"; continue; fi
  say "npm audit ($d):"
  if ( cd "$d" && npm audit --audit-level=high >"$TMP/npm.out" 2>&1 ); then
    say "  ok (no high/critical)"
  elif grep -qE 'npm ERR!|npm error' "$TMP/npm.out"; then
    sed 's/^/  /' "$TMP/npm.out" | tail -6
    warn "npm audit could not complete in $d (offline / registry blocked?) - verify manually"
  else
    sed 's/^/  /' "$TMP/npm.out" | tail -25
    fail "high/critical npm vulnerabilities in $d"
  fi
done < <(find_manifests package.json)
# New/updated packages introduced on this branch (added lockfile lines only)
if git diff "$RANGE" --name-only | grep -qE '(^|/)(package-lock\.json|npm-shrinkwrap\.json)$'; then
  say "NOTICE: npm lockfile changed on this branch - resolved packages added/updated:"
  git diff "$RANGE" -- '*package-lock.json' '*npm-shrinkwrap.json' | grep -E '^\+.*"resolved"' | sed 's/^+/  /' | head -20
fi

COMPOSER_SEEN=0
while IFS= read -r m; do
  [ -n "$m" ] || continue
  COMPOSER_SEEN=1
  d=$(dirname "$m")
  if [ ! -f "$d/composer.lock" ]; then warn "$d: composer.json has no composer.lock - composer audit skipped"; continue; fi
  if ! command -v composer >/dev/null 2>&1; then warn "composer not installed - cannot audit $d"; continue; fi
  say "composer audit ($d):"
  # JSON output is stable across composer versions; severity threshold mirrors npm's --audit-level=high
  if ( cd "$d" && composer audit --no-interaction --format=json >"$TMP/composer.out" 2>"$TMP/composer.err" ); then
    say "  ok (no advisories)"
  elif grep -qE '"severity": *"(high|critical)"' "$TMP/composer.out"; then
    grep -E '"(packageName|severity|title|cve|advisoryId)"' "$TMP/composer.out" | sed 's/^[[:space:]]*/    /' | head -40
    fail "high/critical composer security advisories in $d"
  elif grep -qE '"severity": *"' "$TMP/composer.out"; then
    say "NOTICE: composer advisories below high severity in $d (review; not blocking):"
    grep -E '"(packageName|severity)"' "$TMP/composer.out" | sed 's/^[[:space:]]*/    /' | head -12
  else
    { cat "$TMP/composer.err"; grep -E '"(abandoned|name)"' "$TMP/composer.out"; } 2>/dev/null | sed 's/^/  /' | tail -8
    warn "composer audit did not pass cleanly in $d (abandoned packages / tool error?) - read output above"
  fi
done < <(find_manifests composer.json)
if git diff "$RANGE" --name-only | grep -qE '(^|/)composer\.lock$'; then
  say "NOTICE: composer.lock changed on this branch - packages added/updated:"
  git diff "$RANGE" -- '*composer.lock' | grep -E '^\+[[:space:]]*"name":' | sed 's/^+/  /' | sort -u | head -20
fi
[ "$NPM_SEEN" -eq 0 ] && [ "$COMPOSER_SEEN" -eq 0 ] && say "no package.json / composer.json found - nothing to audit"

# ---- Gate 2: Secret scan ----
say "--- [2/4] Secrets ---"
if command -v gitleaks >/dev/null 2>&1; then
  # Only this branch's commits; --redact so no secret value is ever printed (output gets pasted into chat)
  if gitleaks detect --source . --no-banner --redact --log-opts="$BASE..HEAD" >"$TMP/gl.out" 2>&1; then
    say "gitleaks: no leaks in $BASE..HEAD"
  else
    grep -vE '^[[:space:]]*$' "$TMP/gl.out" | tail -30
    fail "gitleaks flagged potential secrets in this branch's commits"
  fi
else
  warn "gitleaks not installed - using built-in fallback patterns only (install: brew install gitleaks)"
fi
# Built-in fallback (always runs): high-confidence secret shapes -> FLAG; generic password assignments -> NOTICE.
# Prints file:line ONLY, never the matched content.
SECRET_HIGH='AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY|ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[0-9A-Za-z-]{10,}|sk_live_[0-9A-Za-z]{16,}|AIza[0-9A-Za-z_-]{35}|glpat-[0-9A-Za-z_-]{20}'
SECRET_GENERIC='(api[_-]?key|api[_-]?secret|secret[_-]?key|access[_-]?token|auth[_-]?token|passw(or)?d)[[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"'[:space:]]{8,}["'"'"']'
while IFS= read -r -d '' f; do
  [ -f "$f" ] || continue
  case "$f" in
    *.png|*.jpg|*.jpeg|*.gif|*.webp|*.ico|*.pdf|*.woff|*.woff2|*.ttf|*.eot|*.zip|*.gz|*.mp4|*.mov|*.min.js|*.min.css|*package-lock.json|*composer.lock|*yarn.lock) continue;;
    predeploy-audit.sh|*/predeploy-audit.sh) continue;;   # the auditor itself (any location)
  esac
  hits=$(grep -nE "$SECRET_HIGH" "$f" 2>/dev/null | cut -d: -f1 | head -5 | tr '\n' ',' | sed 's/,$//')
  [ -n "$hits" ] && flag "possible credential/private key in $f (lines $hits) - content withheld; inspect the file"
  ghits=$(grep -nEi "$SECRET_GENERIC" "$f" 2>/dev/null | cut -d: -f1 | head -5 | tr '\n' ',' | sed 's/,$//')
  [ -n "$ghits" ] && say "NOTICE: hard-coded password/key-looking assignment in $f (lines $ghits) - confirm it is a placeholder"
done < "$TMP/acm.z"

# ---- Gate 3: Malicious patterns ----
say "--- [3/4] Malicious patterns ---"
# POSIX ERE, portable across GNU and BSD grep. Case-insensitive. Any hit = FLAG = FAIL (surface it, never "fix" it).
PATTERNS=(
  'eval[[:space:]]*\('                                                   # eval execution (JS/PHP/Python)
  'base64_decode[[:space:]]*\('                                          # PHP obfuscation classic
  'atob[[:space:]]*\(.*eval'                                             # JS decode->exec
  'String\.fromCharCode.*eval'                                           # char-code obfuscation
  'curl[^|]*[|][[:space:]]*(sudo[[:space:]]+)?(ba|z|da|k)?sh([[:space:]]|$)'   # curl pipe to shell
  'wget[^|]*[|][[:space:]]*(sudo[[:space:]]+)?(ba|z|da|k)?sh([[:space:]]|$)'   # wget pipe to shell
  'document\.write[[:space:]]*\(.*<script'                               # injected script tags
  '<iframe[^>]*(display[[:space:]]*:[[:space:]]*none|visibility[[:space:]]*:[[:space:]]*hidden|(width|height)=["'"'"']?0["'"'"'[[:space:]]>])'   # hidden iframes
  "preg_replace[[:space:]]*\\([[:space:]]*['\"][^'\"]+[/#~!%][a-zA-Z]*e[a-zA-Z]*['\"]"   # PHP /e eval modifier
  'assert[[:space:]]*\([[:space:]]*\$'                                   # PHP assert-as-eval
  'gzinflate[[:space:]]*\(.*base64'                                      # compressed payloads
  '(^|[^.[:alnum:]_>])(shell_exec|passthru|proc_open|popen|system|pcntl_exec|exec|execSync)[[:space:]]*\('   # shell access (PHP/Node/Python)
  'create_function[[:space:]]*\('                                        # PHP legacy eval
  'new[[:space:]]+Function[[:space:]]*\('                                # JS eval-equivalent
  'os\.(system|popen)[[:space:]]*\('                                     # Python shell
  'subprocess\.[A-Za-z_]+\(.*shell[[:space:]]*=[[:space:]]*True'         # Python shell=True
  'auto_(prepend|append)_file'                                           # .htaccess / .user.ini backdoor hook
  'AddType[[:space:]].*php.*\.(jpe?g|png|gif|txt|ico|svg)'               # .htaccess: run images as PHP
  '<svg[^>]*[[:space:]]on[a-z]+[[:space:]]*='                            # SVG with event handler
)
: > "$TMP/scanned"; : > "$TMP/skipped"
while IFS= read -r -d '' f; do
  [ -f "$f" ] || continue
  case "$f" in
    node_modules/*|*/node_modules/*|vendor/*|*/vendor/*) printf '%s\n' "$f" >> "$TMP/skipped"; continue;;
    *.min.js|*.min.css|*.map) printf '%s\n' "$f" >> "$TMP/skipped"; continue;;
    predeploy-audit.sh|*/predeploy-audit.sh) continue;;   # the auditor itself is handled in Gate 4
  esac
  case "$f" in
    *.js|*.mjs|*.cjs|*.jsx|*.ts|*.tsx|*.vue|*.svelte|*.php|*.phtml|*.inc|*.html|*.htm|*.ss|*.twig|*.htaccess|*.json|*.py|*.rb|*.sh|*.bash|*.zsh|*.yml|*.yaml|*.ini|*.xml|*.svg) ;;
    *) continue;;
  esac
  printf '%s\n' "$f" >> "$TMP/scanned"
  for p in "${PATTERNS[@]}"; do
    if grep -nEi -- "$p" "$f" >/dev/null 2>&1; then
      flag "pattern '$p' in $f:"
      grep -nEi -- "$p" "$f" | head -3 | cut -c1-200 | sed 's/^/    /'
    fi
  done
done < "$TMP/acm.z"
say "pattern-scanned files: $(wc -l < "$TMP/scanned" | tr -d ' ')"
if [ -s "$TMP/skipped" ]; then
  say "NOTICE: minified/vendored files changed but NOT pattern-scanned - confirm where they came from:"
  sed 's/^/  /' "$TMP/skipped"
fi
# Outbound domains referenced in changed files -- eyeball anything unfamiliar
say "Outbound domains referenced in changed files:"
{ while IFS= read -r -d '' f; do
    [ -f "$f" ] || continue
    case "$f" in *package-lock.json|*composer.lock|*yarn.lock|*.png|*.jpg|*.jpeg|*.gif|*.webp|*.pdf|*.woff|*.woff2) continue;; esac
    grep -hoEi '(https?|wss?)://[a-z0-9.-]+' "$f" 2>/dev/null
  done < "$TMP/acm.z"; } | tr 'A-Z' 'a-z' | sort -u | sed 's/^/  /'

# ---- Gate 4: Diff scope ----
say "--- [4/4] Diff scope ---"
say "All files changed on this branch vs $BASE:"
git diff "$RANGE" --name-status | sed 's/^/  /'
SENSITIVE=$(git diff "$RANGE" --name-only | grep -Ei '(^|/)(\.htaccess|\.env|wp-config|_config|composer\.json|package\.json|\.github/|scripts/|\.user\.ini|php\.ini|web\.config|Dockerfile|docker-compose)' || true)
if [ -n "$SENSITIVE" ]; then
  say "NOTICE - sensitive files changed, verify these were part of the task:"
  printf '%s\n' "$SENSITIVE" | sed 's/^/  /'
fi
if git diff "$RANGE" --name-only | grep -qx 'scripts/predeploy-audit.sh'; then
  say "NOTICE - THE AUDITOR ITSELF CHANGED on this branch. Before trusting this PASS, diff scripts/predeploy-audit.sh"
  say "         against the canonical copy (site-register/pdsp/predeploy-audit.sh). A compromised session edits the auditor first."
fi

echo "=================================="
HEADSHA=$(git rev-parse --short HEAD)
if [ "$FAIL" -eq 1 ]; then
  say "RESULT: FAIL - do not open PR. Investigate flags above."
  say "PDSP-SUMMARY: repo=$REPO branch=$BRANCH base=$BASE head=$HEADSHA files=$N_ALL result=FAIL warnings=$WARN"
  exit 1
fi
if [ "$WARN" -gt 0 ]; then
  say "RESULT: PASS with $WARN warning(s) above - read them, then proceed to PR + human review."
else
  say "RESULT: PASS - proceed to PR + human review."
fi
say "PDSP-SUMMARY: repo=$REPO branch=$BRANCH base=$BASE head=$HEADSHA files=$N_ALL result=PASS warnings=$WARN"
exit 0
