---
phase: 0
title: Kickoff
gates: [G0, G1]
driver: pm
consulted: [business-expert]
---

# Phase 0: Kickoff

## Inputs

- User's invocation message (the "build/design/implement <task>" text).

## Steps

0.5. **Stale-state and pre-existing-code detection.**
   - Look for `<project>/docs/superhuman/<slug>/SUPERHUMAN.md`.
   - If it exists but is INVALID (no `## Decisions log` section, or no `G<n>: ...; user decision:` entries), escalate to G6 with three options: (a) archive-and-restart (`scripts/cleanup-project.sh <project>`), (b) treat-as-legacy-import (still run all 8 gates), (c) abandon. Do not proceed past this step until user picks.
   - Look for pre-existing implementation code (`<project>/src/`, `<project>/tests/`, `pyproject.toml`, `setup.py`, `*.py` at project root) that lacks corresponding entries in any valid SUPERHUMAN.md `## Chunk log`. If found, same three-option G6.
   - Only after stale-state is resolved → proceed to Step 1 (Initialize project state).

1. **Initialize project state.**
   - Choose project slug from user message (or ask if ambiguous).
   - Create `<project>/docs/superhuman/<slug>/` if not present.
   - Copy `templates/SUPERHUMAN.md.tpl` → `<project>/docs/superhuman/<slug>/SUPERHUMAN.md`.
   - Fill the front-matter fields (slug, started, superhuman-version from `VERSION`).
   - **Detect `Modifies-existing-code`.** Check whether the project root already has tracked
     content unrelated to this superhuman scaffold (existing git history predating this session,
     or source/config files beyond `docs/superhuman/`). If so, set `Modifies-existing-code: yes`;
     for an empty/fresh repo, `no`. This reuses the same signal as the Step 0.5 pre-existing-code
     check above, just recorded as a declared fact for later gating (HITL-L's rollback-plan
     precondition reads this field — see Step 3 below).

1.5. **Project-id minting (D3, FR-11/FR-12; a code step, not a prose reminder).**
   - Run `python -m scripts.fleet.cli project check --workspace <project-root> --slug <slug>`
     (`<dispatch:bash>`).
   - If it exits non-zero (no `**Project-id:**` yet), run
     `python -m scripts.fleet.cli project mint --workspace <project-root> --slug <slug>`
     (`<dispatch:bash>`) to assign one. `mint` is a no-op on a record that already has an id —
     an id is never re-minted (Phase 1 Decision F, reaffirmed by this project's own
     DECISIONS.md D3).
   - This exists so a project record cannot silently lack a `**Project-id:**` the way ~24
     records across the estate did before this chunk — the check is fail-closed by
     construction, not something that depends on anyone remembering to fill in a field.

2. **Vision elicitation (conversational; G0's approval is presented in Step 3, not here).**
   - Probe purpose and reason. Use the patterns from DESIGN.md §2 G0 (e.g. "stock trading" → backtest+paper+live+strategy discovery; "health app" → probe for personal health context).
   - May dispatch Business Expert in parallel if domain is clear; multiple parallel invocations OK for multi-domain projects.
   - **If the vision declares removing / retiring / sunsetting / consolidating an existing product
     system** (e.g. "decommission X", "migrate off Y"), invoke `references/deprecating-a-system/`
     (a conditional sub-skill, not a gate) to shape the migration scope, pattern (Strangler /
     Adapter / Feature-flag), and cutover chunks. It applies to **product code**, never to
     superhuman's own artifacts (those stay archive-never-delete).
   - Cap at 5-7 exchanges before drafting.
   - Draft `VISION.md` from `templates/artifacts/VISION.md.tpl`. Do not present its approval prompt
     yet — whether G0 is presented alone or combined with G1 depends on the HITL-level chosen in
     Step 3, so drafting and approving are split across these two steps.

3. **Workflow preferences, including HITL-level — then present G0 (and G1, combined at level 2).**
   - Ask via `<dispatch:ask>`:
     - **HITL-level: 0 (High) | 1 (Medium) | 2 (Low).** Ask this first, since it determines how
       the rest of this step (and G0's presentation) proceeds. Default/recommend **0**. Briefly
       characterize each: 0 = every gate pauses for you; 1 = a surrogate answers the routine
       implementation gates, you still see drift/acceptance/parallelism decisions; 2 = the PM
       resolves nearly everything itself (researching how comparable decisions were made
       elsewhere and reporting its choice), you're only interrupted if it's genuinely stuck.
     - Cadence: per-chunk | on-divergence
     - Value-vs-foundation: value-first | foundation-first | hybrid
     - Git: none | local | remote (if remote, follow-ups per conventions/git.md)
     - Parallelism: PM-decides | gate-each | serial-only
     - **Deployment ladder: the ordered rungs this work must reach before it is COMPLETE, or
       `none`.** There is no blank answer here — `none` (work that is finished where it is
       authored: a doc change, a local tool, a spec) is a real answer, and it is the right one
       for most small projects. Lowest rung first, e.g. `dev > staging > production`.
       **Where the ladder is already knowable — the operator's profile declares a `ladder:`, or
       the harness context states one — present it and ask the user to CONFIRM or correct it,
       rather than asking cold.** Do not read the rungs' order off the profile: a profile's
       `ladder:` is ordered by detection precedence (narrowest first), not by promotion order.
       Record the answer verbatim in SUPERHUMAN.md's `**Deployment ladder:**` field. This is what
       HARD-GATE rule 3 reads to tell *accepted* from *complete*; it is locked at G1, and changing
       it later is a G6 drift event.
   - If user picks per-chunk cadence, display the token-cost advisory from DESIGN.md §9.
   - If git=remote, run the remote-sync flow per conventions/git.md.
   - **If HITL-M or 2 was chosen, validate the precondition** before accepting it:
     - Require git + remote to already be selected above — refuse level 1/2 without it, falling
       back to asking again with level 0 as the only option until git/remote is set.
     - Run `scripts/autonomous-precondition.sh <project> --level <1|2> --slug <slug> --kickoff`
       (`<dispatch:bash>`). `--kickoff` is correct **only here**: the project's own `SUPERHUMAN.md`
       front-matter and `GOAL.md` are written further down this same step, so those two checks
       cannot yet be answered. It defers exactly those two; the rung and git+remote are still
       enforced. On non-zero exit, surface the script's message verbatim and fall back one level
       (2→1→0) rather than silently proceeding — never continue at a level whose precondition failed.
     - **Level 2 only, when `Modifies-existing-code: yes`:** elicit the revert target + procedure
       from the user now and write `docs/superhuman/<slug>/ROLLBACK.md` from
       `templates/artifacts/ROLLBACK.md.tpl`. Net-new/greenfield projects
       (`Modifies-existing-code: no`) skip this — there's nothing pre-existing to roll back to.
   - **If HITL-M or 2, handle GOAL.md** (file-first override): if
     `<project-root>/GOAL.md` or `<project>/docs/superhuman/<slug>/GOAL.md` already exists, use it
     verbatim and skip elicitation. Otherwise elicit the objective, fitness function, measurement
     command, and budget interactively, then write `GOAL.md` from `templates/artifacts/GOAL.md.tpl`.
     Phase 3 then uses `phases/3-autonomous-loop.md` instead of `phases/3-implementation.md`.
   - Update SUPERHUMAN.md front-matter with the chosen values, including `HITL-level:` and
     `Modifies-existing-code:`. Declare the latter explicitly as `yes` or `no` — leaving it blank
     is not an implicit `no`, and the gate treats an undeclared field as a gap.
   - **Operator model-tier elicitation (#139, provider-neutral — first run only).** This configures
     the *operator's* global `~/.superhuman/profile.yaml`, not this project, so it does not belong
     on every kickoff. Before asking anything, check the resolved profile's `models:` block (see
     `scripts/superhuman_profile.py`, tiers `most_capable` / `standard` / `cheap`):
     - **If all three tiers already have a real (non-`PROMPT_ME`) `primary` entry, skip this
       elicitation entirely** — nothing to ask, proceed to Present G0 (and G1) below.
     - **roadmap#275 addendum to the skip condition above.** The primary-only check above is
       necessary but no longer sufficient: also require every tier to have a real (non-`PROMPT_ME`)
       **`effort`** — an *absent* `effort` key counts as unconfigured too, the same as an explicit
       `PROMPT_ME` (see `scripts/superhuman_profile.py`'s `Profile.models` docstring) — AND require
       the harness step added below, run against the *current* profile, to report nothing to do
       (see that step's own "nothing to do" condition). Skip only when both this addendum and the
       original bullet's condition hold; otherwise elicit, per the bullets below.
     - **Otherwise (profile/`models:` absent, or any tier still `PROMPT_ME`), elicit.** Ask via
       `<dispatch:ask>`, per tier `most_capable`, `standard`, `cheap`, a **primary** AND a
       **fallback** provider·model pair the operator wants used at that capability level. The
       question set is provider-neutral: do not pre-fill or suggest any concrete vendor/model as
       *the* answer. Illustrative examples are fine when clearly marked (e.g. "for example, a
       vendor's flagship reasoning model for `most_capable`, a fast/cheap tier model for `cheap`")
       — but the field itself must start blank, never defaulted to one provider.
     - **roadmap#275 addendum: effort, and for `most_capable`, raised effort.** Alongside the
       primary/fallback question above, for each tier also ask a **reasoning effort**: one of
       `low | medium | high | xhigh | max | n/a` (`n/a` is a real answer for a model that ignores
       effort — it is not a gap). Unlike model/vendor, effort is harness/provider-neutral
       vocabulary this skill defines, so **suggest `medium`** as the default answer rather than
       leaving the field cold — the operator can just confirm it. Additionally, for `most_capable`
       only, ask a **raised effort** (one of `low | medium | high | xhigh | max`, no `n/a` — a
       raised tier is always a real level) — this is what a role opting into `most_capable+raised`
       (security review, or a PM/Architect/code-quality-review opt-in per `adaptation/dispatch.md`)
       runs at. **Suggest `high`** as the default answer. `most_capable`'s raised effort is
       optional — the operator may decline it, in which case any `+raised` dispatch class is
       simply never generated (see the harness step added below) and a role opting into it
       degrades to its default class with a C-DISP-style warning, per `adaptation/dispatch.md`.
     - **Decline/defer.** The operator may decline or defer any or all tiers (e.g. "not sure yet",
       "skip for now"). This must never block kickoff — proceed regardless of how many tiers were
       answered.
     - **roadmap#275 addendum: per-field decline.** The decline/defer bullet above applies per
       field, not only per whole tier — the operator may answer `primary`/`fallback` now and defer
       `effort` (or vice versa); this still must never block kickoff.
     - **Resolve the interpreter by EXECUTING candidates, not by testing for their existence.**
       `command -v python` only checks that something named `python` is on `PATH` and marked
       executable — on Windows that can resolve to the Microsoft Store "app execution alias" stub,
       which exists, is executable, and exits 49 with an advert instead of running Python. Mirror
       the execute-probe loop `scripts/autonomous-precondition.sh` already uses (see that script's
       "Prefer the bundle's venv interpreter…" comment): try each candidate by actually running it,
       and take the first one that succeeds, before invoking the CLI. **Resolve `PY` as a bash
       ARRAY, not a plain string** — the `py -3` launcher is two words (`py` and `-3`), and a
       single-word `PY="py -3"` string, later invoked double-quoted (`$PY` inside quotes), makes
       bash search for one executable literally named `py -3` (with the space), which does not
       exist and fails — even
       though the unquoted probe (`$candidate -c ...`, which word-splits) reported it as working.
       Every candidate, single-word ones included, is stored and invoked as an array so the probe
       and the real invocation split the SAME way:
       ```
       PY=()
       for candidate in \
         ".venv/bin/python" \
         ".venv/Scripts/python.exe" \
         "$(command -v python3 || true)" \
         "$(command -v python || true)"
       do
         [ -n "$candidate" ] || continue
         if "$candidate" -c "import sys; sys.exit(0)" >/dev/null 2>&1; then
           PY=("$candidate")
           break
         fi
       done
       if [ "${#PY[@]}" -eq 0 ] && command -v py >/dev/null 2>&1; then
         for cand in "py -3" "py"; do
           IFS=' ' read -r -a cand_arr <<< "$cand"
           if "${cand_arr[@]}" -c "import sys; sys.exit(0)" >/dev/null 2>&1; then
             PY=("${cand_arr[@]}")
             break
           fi
         done
       fi
       if [ "${#PY[@]}" -eq 0 ]; then
         echo "kickoff: no working python interpreter found (tried .venv, python3, python, py -3, py)." >&2
         exit 1
       fi
       ```
       (`<dispatch:bash>`). Do not shortcut this with `command -v python || command -v python3` —
       that finds a name on `PATH`, not a working interpreter, and would silently select the
       Store-stub instead of failing loud.
     - **Write, don't hand-write.** Elicitation is inference (this recipe); writing the profile is
       code (dev-principle #5). Call the deterministic writer's CLI entry point — `models set`,
       which wraps `write_models_block(profile_path, answers, decline=...)` — never author the YAML
       by hand and never call `write_models_block` directly as Python (it has no `<dispatch:bash>`
       seam). Never interpolate an operator alias or an elicited answer directly into a shell word
       (dev-principle #5) — a value containing a quote, `$`, or a backtick would break the quoting
       or inject shell. Instead, pipe the answers JSON directly to the command's STDIN through a
       QUOTED heredoc (a quoted delimiter disables all shell expansion inside it) using
       `--answers-json-file -`, with the profile path itself quoted too — no temp file, so there is
       nothing to clean up and nothing left behind in `/tmp` carrying operator model aliases:
       ```
       "${PY[@]}" scripts/superhuman_profile.py models set --profile "<profile-path>" \
         --answers-json-file - \
         --decline "<comma-separated declined/deferred tier names>" <<'JSON'
       {"most_capable": {"primary": "...", "fallback": "..."}, ...}
       JSON
       ```
       (`<dispatch:bash>`). No operator string is ever interpolated into a shell word — the answers
       travel over stdin inside the quoted heredoc, and the CLI reads them from there because
       `--answers-json-file -` means "read JSON from stdin". `--answers-json-file` carries the
       answered tiers as a JSON object (`{tier: {"primary": ..., "fallback": ...}}`); `--decline`
       carries the declined/deferred tier names (omit it, or pass all three tier names, to defer
       everything — if every tier is declined and none are answered, the heredoc may be an empty
       `{}` and `--decline` alone is enough). A declined or deferred tier — and any tier the
       operator never reaches — is written by the command as the neutral `PROMPT_ME` placeholder,
       never a vendor assumption: this **fails safe** and the operator is prompted again on a later
       first run once the profile still shows placeholders. A malformed answers JSON or an
       unrecognized tier name exits non-zero with a `ProfileError` message, never a silent no-op or
       a traceback — treat a non-zero exit here as a kickoff blocker, same as any other failed
       `<dispatch:bash>` step.
     - **roadmap#275 addendum: effort/raised_effort ride the same JSON object.** Each tier's
       object in the heredoc above may also carry `"effort": "..."` and (for `most_capable` only)
       `"raised_effort": "..."` alongside `primary`/`fallback` — e.g. `{"most_capable": {"primary":
       "...", "fallback": "...", "effort": "...", "raised_effort": "..."}, "standard": {"primary":
       "...", "effort": "..."}, "cheap": {"primary": "...", "effort": "n/a"}}`. Either field may be
       omitted per-tier if the operator declined just that one (an answered tier that omits
       `effort` is written with no `effort` key at all, not a placeholder — see
       `write_models_block`'s docstring; only a wholly untouched/declined tier gets the explicit
       `PROMPT_ME`, this time also covering `effort`).
     - **roadmap#275 addendum — harness step: install/merge the resolved model+effort into the
       running harness.** Config generation stays code (dev-principle #5), never hand-authored —
       after `models set` above (or immediately, on the skip path), detect which harness this
       session is running under:
       - **`<dispatch:agent>` harness** (this session's `<dispatch:agent>` maps to `Agent` per
         `adaptation/dispatch.md`'s harness table — the ordinary case for this skill): run
         (e.g. `"${PY[@]}" scripts/superhuman_profile.py models install-agents --harness claude-code --profile "<profile-path>"`)
         (`<dispatch:bash>`; `--agents-dir` defaults to (e.g.) `~/.claude/agents` and normally
         does not need overriding). This renders one tier-agent
         definition per dispatch class (`most_capable`, `most_capable+raised`, `standard`,
         `cheap`) that `adaptation/dispatch.md`'s `<dispatch:agent>` mapping dispatches through —
         see that file's "tier agent definitions" section. **"Nothing to do"** (the skip-condition's
         third clause above): running this command is idempotent and a no-op report (no file
         written, no warning) when every configured tier's rendered agent already matches what is
         on disk — check via `models doctor` first if you want to avoid a redundant write, but
         re-running it unconditionally is also safe.
       - **Hermes delegation harness** (this session is running inside a Hermes
         instance): run `"${PY[@]}" scripts/superhuman_profile.py models
         install-agents --harness hermes --profile "<profile-path>" --hermes-config "<path to
         this instance's config.yaml>"` (`<dispatch:bash>`). This merges the `standard` tier's
         primary model + effort into `config.yaml`'s `delegation:` block, preserving every other
         key — see `adaptation/dispatch.md`'s "Hermes — config-level fallback" section for why
         only `standard` (the `most_capable` roles run in the orchestrator session itself, never
         delegated).
       - **OpenClaw**: nothing is installed here — `sessions_spawn` takes `model`/`thinking`
         per call (`adaptation/dispatch.md`'s "OpenClaw — per-spawn model + thinking"), so the
         resolved tier table in `~/.superhuman/profile.yaml` is read live at dispatch time; note
         this in the elicitation summary so the operator isn't left wondering why nothing was
         written.
       - Treat a non-zero exit from either installer the same as a failed `models set` above — a
         kickoff blocker, not a silent skip.
       - **Exit 4 is not a blocker.** Either installer exits 4 when the profile configures no tier
         this harness can install (every tier declined/deferred). That is the decline path above,
         which must never block kickoff: note in the elicitation summary that no tier agents were
         installed, and proceed. Without `--profile`, both installers resolve the profile the way
         `doctor` does (`$SUPERHUMAN_PROFILE`, then the project walk, then
         `~/.superhuman/profile.yaml`) and print the path they read.
   - **If HITL-M or 2, re-run the gate WITHOUT `--kickoff`:**
     `scripts/autonomous-precondition.sh <project> --level <1|2> --slug <slug>`. Everything the
     deferred checks needed now exists, so this is the run that actually authorizes the level. On
     non-zero exit, fall back one level as above. This re-run is not optional — skipping it is how
     a level gets accepted with no fitness function and no rollback plan.
   - **Present G0 (and G1):**
     - **HITL-H or 1:** present G0 (vision) using gate-headers Type A — recommendation
       "approve and proceed to G1" with alternatives "refine VISION further" / "narrow scope" /
       "expand scope" — then, once approved, present G1 (workflow prefs) as its own gate, as before.
     - **HITL-L:** present G0 and G1 as **one combined confirmation** — VISION.md's summary
       plus the full set of workflow preferences (cadence, value-vs-foundation, git, parallelism,
       HITL-level: 2, and the GOAL.md summary) in a single `<dispatch:ask>` exchange. This is the
       one lightweight human checkpoint before the PM goes fully unattended except for G10. On
       approval, append **both** a G0 and a G1 entry to the Decisions log, timestamped from this
       one exchange — G0 at the exchange's time and G1 one second later, never the same
       timestamp, so the log's last gate reads unambiguously as G1 (gate format rule 5).

   **3.5 Seed commit (if git enabled).** If G1 selected git (local or remote), make an initial commit so subagents can query git state without errors: `git -C <project> add VISION.md SUPERHUMAN.md && git -C <project> commit -m "chore: project kickoff (VISION + SUPERHUMAN initialized)"`. No `-c user.*` overrides: the commit uses the identity resolved by the G1 git step in `roles/pm.md` (inherited, or set repo-local only where nothing resolved). If G1 chose remote, configure the remote before this commit, so identity routing keyed on the remote URL applies to it too.

## Outputs

- `<project>/docs/superhuman/<slug>/SUPERHUMAN.md` (initialized + G1 prefs filled, including HITL-level)
- `<project>/docs/superhuman/<slug>/VISION.md` (approved at G0)
- (HITL-L + `Modifies-existing-code: yes` only) `<project>/docs/superhuman/<slug>/ROLLBACK.md`

## Exit criteria

- G0 approved.
- G1 prefs recorded, including a validated HITL-level.
- `**Deployment ladder:**` answered — an ordered rung list or the literal `none`, never left blank.
- (If git=remote) initial push succeeded.

Next phase: 1-requirements.
