#!/usr/bin/env bash
# autonomous-iter.sh — deterministic per-iteration audit-trail driver for the
# superhuman v0.2.x autonomous loop.
#
# WHY THIS EXISTS (skill-design Rule 5: safety/audit-critical paths are CODE):
#   The v0.2.0 live smoke showed a capable orchestrator reach the fitness goal
#   but SKIP the per-iteration snapshot -> commit -> tag-or-rollback discipline
#   (it edited files until tests passed, leaving no audit trail). Relying on the
#   LLM to remember that git dance is fragile. This script makes the dance
#   non-skippable: the recipe calls `pre` before an attempt and `decide` after,
#   and ALL tagging / committing / archiving / rollback happens here,
#   deterministically. The LLM's only job is the improvement attempt in between.
#
# SUBCOMMANDS
#   pre    --project-root P --version V --run-id R --iter N \
#          (--measure-pytest DIR | --measure 'CMD')
#       Tag the pre-iteration snapshot  v<V>-alpha-<R>.iter-<N>-pre  at HEAD,
#       measure fitness, and print:  fitness_before=<f>
#
#   decide --project-root P --slug S --version V --run-id R --iter N \
#          (--measure-pytest DIR | --measure 'CMD') \
#          --fitness-before F [--min-delta D]   (D default 0.01)
#       Measure fitness_after, then:
#         KEEP  iff  after > before + min_delta :
#                 git commit -am  +  tag v<V>-alpha-<R>.iter-<N>
#         ROLLBACK otherwise (ties included, per conventions/autonomous.md):
#                 archive the rejected diff + WHY.md under
#                 docs/superhuman/<S>/archive/<ts>-iter-<N>-rolled-back/,
#                 then  git reset --hard  to the -pre snapshot.
#       Append one row to SUPERHUMAN.md "## Autonomous iterations log" and print:
#         decision=KEEP|ROLLBACK fitness_before=.. fitness_after=.. delta=..
#
#   final  --project-root P --version V --run-id R
#       Tag the run result  v<V>-beta-<R>  at HEAD.
#
# MEASUREMENT
#   --measure-pytest DIR : run pytest in DIR and use pass-rate = passed/(passed+
#                          failed+errors) as the fitness scalar (the python+pytest
#                          common case for superhuman projects).
#   --measure 'CMD'      : run CMD; its LAST non-empty stdout line must be a float
#                          in [0,1] (the generic case; CMD encodes GOAL.md's
#                          measurement command + extraction rule).
#
# Exit codes: 0 ok; 2 usage error; 3 git/measurement error.

set -euo pipefail

die() { echo "autonomous-iter: $*" >&2; exit "${2:-3}"; }
usage() {
  sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
}

[ $# -ge 1 ] || { usage >&2; exit 2; }
SUB="$1"; shift

# ---- arg parsing (shared) --------------------------------------------------
ROOT="" SLUG="" VERSION="" RUNID="" ITER="" MEASURE="" MEASURE_PYTEST="" \
FITNESS_BEFORE="" MIN_DELTA="0.01"
while [ $# -gt 0 ]; do
  case "$1" in
    --project-root) ROOT="$2"; shift 2 ;;
    --slug) SLUG="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    --run-id) RUNID="$2"; shift 2 ;;
    --iter) ITER="$2"; shift 2 ;;
    --measure) MEASURE="$2"; shift 2 ;;
    --measure-pytest) MEASURE_PYTEST="$2"; shift 2 ;;
    --fitness-before) FITNESS_BEFORE="$2"; shift 2 ;;
    --min-delta) MIN_DELTA="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) die "unknown argument: $1" 2 ;;
  esac
done

[ -n "$ROOT" ] || die "--project-root is required" 2
[ -d "$ROOT" ] || die "--project-root '$ROOT' is not a directory" 2
GIT="git -C $ROOT -c safe.directory=$ROOT"

# ---- measurement -----------------------------------------------------------
# Echo a single float in [0,1] on stdout.
measure_fitness() {
  if [ -n "$MEASURE_PYTEST" ]; then
    local py out summary p f e total
    py="$(command -v python || command -v python3 || true)"
    [ -n "$py" ] || die "no python interpreter for --measure-pytest"
    # Non-fatal: a failing test suite is data, not a script error.
    # PYTHONDONTWRITEBYTECODE + no:cacheprovider keep measurement deterministic:
    # successive measurements must read fresh source, never a stale .pyc/.pytest_cache
    # left by the previous measurement (a same-second source edit could otherwise
    # make the post-attempt run import old bytecode and misreport the fitness).
    out="$(cd "$ROOT" && PYTHONDONTWRITEBYTECODE=1 "$py" -m pytest "$MEASURE_PYTEST" -q --tb=no -p no:cacheprovider 2>&1 || true)"
    summary="$(printf '%s\n' "$out" | grep -E '[0-9]+ (passed|failed|error)' | tail -n1)"
    p="$(printf '%s' "$summary" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' || true)"
    f="$(printf '%s' "$summary" | grep -oE '[0-9]+ failed' | grep -oE '[0-9]+' || true)"
    e="$(printf '%s' "$summary" | grep -oE '[0-9]+ error' | grep -oE '[0-9]+' || true)"
    p="${p:-0}"; f="${f:-0}"; e="${e:-0}"
    total=$((p + f + e))
    if [ "$total" -eq 0 ]; then echo "0"; else awk -v p="$p" -v t="$total" 'BEGIN{printf "%.4f", p/t}'; fi
  elif [ -n "$MEASURE" ]; then
    local out fit
    out="$(cd "$ROOT" && eval "$MEASURE" 2>/dev/null || true)"
    fit="$(printf '%s\n' "$out" | awk 'NF{last=$0} END{print last}')"
    printf '%s' "$fit" | grep -qE '^[0-9]+(\.[0-9]+)?$' \
      || die "measurement did not yield a float on its last line (got: '$fit')"
    echo "$fit"
  else
    die "one of --measure-pytest or --measure is required" 2
  fi
}

require_loop_args() {
  [ -n "$VERSION" ] || die "--version is required" 2
  [ -n "$RUNID" ]   || die "--run-id is required" 2
  [ -n "$ITER" ]    || die "--iter is required" 2
}

PRE_TAG=""; KEEP_TAG=""
set_tags() { PRE_TAG="v${VERSION}-alpha-${RUNID}.iter-${ITER}-pre"; KEEP_TAG="v${VERSION}-alpha-${RUNID}.iter-${ITER}"; }

superhuman_md() {
  # Resolve the SUPERHUMAN.md for the slug; empty if not found.
  local p="$ROOT/docs/superhuman/$SLUG/SUPERHUMAN.md"
  [ -f "$p" ] && echo "$p" || true
}

append_iter_row() {
  # $1 fitness_before  $2 fitness_after  $3 delta  $4 KEEP|ROLLBACK  $5 tag  $6 archive-ref
  local md; md="$(superhuman_md)"
  [ -n "$md" ] || return 0   # no SUPERHUMAN.md → skip the log row (non-fatal)
  printf '| %s | %s | %s | %s | %s | %s | %s |\n' \
    "$ITER" "$1" "$2" "$3" "$4" "$5" "$6" >> "$md"
}

case "$SUB" in
  pre)
    require_loop_args; set_tags
    $GIT rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "not a git repo: $ROOT"
    $GIT tag "$PRE_TAG" || die "could not create snapshot tag $PRE_TAG (already exists?)"
    before="$(measure_fitness)"
    echo "fitness_before=$before"
    ;;

  decide)
    require_loop_args; set_tags
    [ -n "$SLUG" ] || die "--slug is required for decide" 2
    [ -n "$FITNESS_BEFORE" ] || die "--fitness-before is required for decide" 2
    $GIT rev-parse "$PRE_TAG" >/dev/null 2>&1 || die "snapshot tag $PRE_TAG not found — run 'pre' first"
    after="$(measure_fitness)"
    delta="$(awk -v a="$after" -v b="$FITNESS_BEFORE" 'BEGIN{printf "%+.4f", a-b}')"
    keep="$(awk -v a="$after" -v b="$FITNESS_BEFORE" -v d="$MIN_DELTA" 'BEGIN{print (a > b + d) ? 1 : 0}')"
    if [ "$keep" = "1" ]; then
      $GIT add -A
      # Commit only if there is something to commit.
      if ! $GIT diff --cached --quiet; then
        $GIT commit -m "autonomous: iter-${ITER} kept (fitness ${FITNESS_BEFORE} -> ${after})" >/dev/null
      fi
      $GIT tag "$KEEP_TAG" || die "could not create keep tag $KEEP_TAG"
      append_iter_row "$FITNESS_BEFORE" "$after" "$delta" "KEEP" "$KEEP_TAG" "-"
      echo "decision=KEEP fitness_before=$FITNESS_BEFORE fitness_after=$after delta=$delta tag=$KEEP_TAG"
    else
      # ROLLBACK — archive the rejected diff before resetting (archive-never-delete).
      ts="${RUNID}"
      arch_rel="docs/superhuman/${SLUG}/archive/${ts}-iter-${ITER}-rolled-back"
      arch_abs="$ROOT/$arch_rel"
      mkdir -p "$arch_abs"
      $GIT add -A
      $GIT diff --cached "$PRE_TAG" > "$arch_abs/rolled-back.diff" 2>/dev/null || \
        $GIT diff "$PRE_TAG" > "$arch_abs/rolled-back.diff" 2>/dev/null || true
      {
        echo "# Why this iteration was rolled back"
        echo
        echo "- iteration: $ITER"
        echo "- fitness_before: $FITNESS_BEFORE"
        echo "- fitness_after: $after"
        echo "- delta: $delta"
        echo "- min_delta: $MIN_DELTA"
        echo "- rule: strictly improving (ties roll back) — conventions/autonomous.md"
        echo "- restored to snapshot: $PRE_TAG"
      } > "$arch_abs/WHY.md"
      $GIT reset --hard "$PRE_TAG" >/dev/null
      append_iter_row "$FITNESS_BEFORE" "$after" "$delta" "ROLLBACK" "$PRE_TAG" "$arch_rel"
      echo "decision=ROLLBACK fitness_before=$FITNESS_BEFORE fitness_after=$after delta=$delta archive=$arch_rel"
    fi
    ;;

  final)
    [ -n "$VERSION" ] || die "--version is required" 2
    [ -n "$RUNID" ]   || die "--run-id is required" 2
    beta="v${VERSION}-beta-${RUNID}"
    $GIT tag "$beta" || die "could not create final tag $beta"
    echo "final_tag=$beta"
    ;;

  --help|-h) usage; exit 0 ;;
  *) die "unknown subcommand: $SUB (expected pre|decide|final)" 2 ;;
esac
