# Spec (design input): The CEO overseer & Superhuman Session Fleet

**Created:** 2026-08-13
**Last refined:** 2026-08-13 (requirements round 3 — HITL autonomy, levels of done, self-improvement)
**Status:** Draft for approval — pre-project discovery. Feeds **three** superhuman phases (§16).
**Owner:** project maintainer
**Discovery method:** research + existing-tooling inventory + three rounds of requirements elicitation.

> Durable capture of a design conversation (requirement #3 of the session plan). It does **not**
> restate superhuman internals; it cites them.

---

## 1. Problem statement

> "I am working on many projects at once… I don't know session dependencies and lose track of where
> I am in workplans. I want a single agent that tracks and manages all the others."

Refined into **two separate-but-related goals with a little overlap**:

- **A. The CEO overseer** — *active, autonomous* entity that tracks/manages **everything**: every
  session (whether or not asked), every roadmap issue, every superhuman project. It **delegates to but
  also supervises** superhuman. Its job is to keep work from **dying, being forgotten, or lost** —
  periodically prioritizing and checking what is stuck, what is next, what got dropped.
- **B. Superhuman managing its own project** — superhuman never causes a session to exist (spawned,
  relayed, **or** a manual "next prompt" handoff) without tracking it and its dependencies.

Overlap = the **shared manifest** (§13): superhuman is a durable *writer*; the CEO is the active *loop*.

## 2. Field research

- **"CEO"/lead-agent pattern** — orchestrator decomposes, delegates to isolated-context sub-agents,
  checks in, stays auditable. (Addy Osmani; Shipyard.)
- **Native Agent Teams** (Anthropic 2026, experimental) — shared task list, real-time status/dependency
  tracking, **auto-unblocks dependents**. Claude-specific (§6). (Claude Code Docs; MindStudio.)
- **Kanban "command center"** — one board of goals/assignments/status/handoff. (MindStudio.)

**Recurring lesson:** dependencies must be **declared explicitly** (→ G2).

## 3. Existing assets (reuse; do not reinvent)

| Asset | Does | Gap / harness note |
| --- | --- | --- |
| **`session-relay`** | HANDOFF/NEW/INVENTORY; `session_scan.py` KEEP/REVIEW/SAFE/ORPHAN; worktree-safe reap; KICKOFF | Ad hoc; no persistent map. **Claude-Desktop-specific** → it is the *Claude adapter* |
| **Native session tools** | `list_sessions`/`get_session`/`send_message`/`archive_session`/`spawn_task` | No standing dashboard. **Claude-only** |
| **Roadmap tracker** (GH Issues) | SoT for cross-cutting "what & when"; area/type/priority labels | Sessions not linked; deps not edges; no resurfacing |
| **`superhuman`** | Vision→acceptance; roles; gates; drift watch; **HITL levels H/M/L**. Harness-portable | Loses the plot across sessions; can silently stall; **calls "done" before prod** |
| **Governing disciplines skill** | 8 Rules; **Rule 8: promotion to UAT/Prod needs explicit approval, every time; nothing complete until promotion lands** | Governing peer (§7 levels of done) |
| **Auto-memory + `docs/superhuman/`** | Durable per-project state | Not aggregated |
| **`schedule`/scheduled-tasks MCP** | Cron | Not pointed at backlog / CEO loop / overnight self-review |
| **Native Agent Teams** | Real-time shared task list + auto-unblock | Experimental, off, intra-project, **Claude-specific** |

**Missing organs:** (a) a **portable persistent manifest** tying sessions ↔ issues ↔ phases, and
(b) an **active supervisory loop** over it.

## 4. Three dependency layers

1. **Session ↔ session** (runtime; partly derivable, partly declared).
2. **Roadmap-issue ↔ issue** (declared in GitHub).
3. **Phase ↔ gate within a superhuman project** (superhuman already models this).

The CEO reconciles across all three.

## 5. Two entities — core architecture

```
        ┌──────────────────────────────────────────────────────┐
        │  CEO OVERSEER  (active, autonomous, always-on)          │
        │  • surveys every session, issue, SH project             │
        │  • HUNTS sessions that escaped tracking (R12)           │
        │  • detects stuck / dropped / dying; prioritizes         │
        │  • autonomy SCALED BY HITL level (§8), capped by DP#5   │
        │  • SUPERVISES superhuman (a managed entity)             │
        │  • keeps a self-improvement log (Phase 3, §16)          │
        └───────────────────────┬────────────────────────────────┘
                                │ reads/writes
                    ┌───────────▼───────────┐
                    │   SHARED MANIFEST      │  ← the overlap (§13)
                    │ sessions·deps·state·   │
                    │ HITL·done-level        │
                    └───────────▲───────────┘
                                │ writes (every session it causes to exist)
     ┌──────────────────────────┴──────────────────────────┐
     │ SUPERHUMAN (per-project, harness-portable)             │
     │  • gate-driven; may legitimately pause at a gate       │
     │  • registers spawned, relayed, AND manual handoffs     │
     └────────────────────────────────────────────────────────┘
```

**CEO ⊃ superhuman.** Superhuman is reactive within a project (and can stall); the CEO notices and
resurfaces. The **manifest is the contract**; session-relay/Agent Teams are Claude-side adapters.

## 6. Harness-agnostic constraint (hard)

Superhuman runs under **Claude, OpenClaw, Hermes, Codex…**. Therefore the **manifest is the portable
core** (files + schema, no harness calls). Live-session facts and the proactive trigger come from
**per-harness adapters** (Claude = native tools + settings hooks + optional Agent Teams, with
session-relay + `session_scan.py` as that adapter; others bring their own; fallback = git/PR + fs +
cron). **Agent Teams is the Claude adapter's real-time mechanism, evaluated but not required for
portability.** Consequence: **session-relay is the Claude adapter, not a hard dependency.**

## 7. Levels of done — the `D`-ladder (meaningful shorthand)

Superhuman calls things "done" when they are only **code-complete** — before promotion. Per disciplines
**Rule 8** and the environment ladder (Laptop → Lab → Test → UAT → Prod), *done is a ladder*.
Self-documenting labels (per the meaningful-shorthand habit — the token carries its meaning):

| Level | Meaning | Autonomy (profile `act_unattended`) |
| --- | --- | --- |
| **D0-code** | Code-complete — tests pass locally | self |
| **D1-merged** | Merged / integrated | self |
| **D2-test** | Deployed to a Test env, tests green | self (where rung permits) |
| **D3-uat** | Deployed to UAT **and accepted** | **human only** (`promote_into: human`) |
| **D4-prod** | Deployed to **Production and accepted by a human** ← *truly done* | **human only** |

**Rules (corrected per round-3 feedback):**
- **The human-gate is at promotion INTO UAT and Prod (D3-uat / D4-prod)** — always, every time (Rule 8;
  the profile marks the UAT and prod rungs `act_unattended: never, promote_into: [human]`). That is the
  DP#5 irreversible line.
- **Reaching D2-test can be autonomous.** Where the resolved rung permits unattended work
  (`act_unattended: [self]` — laptop, lab, test) **and** the project is at HITL-L, superhuman
  *or* the CEO may drive work all the way to **D2-test** (build → merge → deploy-to-test → run tests)
  without human intervention. The cap is entry to UAT, not entry to Test. *(This corrects the earlier
  draft, which wrongly gated D2.)*
- **Nothing is terminal-`done` until D4-prod** (human-accepted in prod). The manifest carries a
  `done-level`; superhuman's internal "done" maps to **D0-code / D1-merged** only and must never
  masquerade as terminal.
- **No production environment yet**: the project reaches its current ceiling and
  parks at **`pending-prod`** — a status the CEO keeps **permanently visible**, never closed. (Cf. memory
  "superhuman never past Test": superhuman's *own* ceiling is Test — a project-specific D-ceiling,
  not a redefinition of D4-prod.)

## 8. HITL-scaled CEO autonomy (new)

The CEO reads the project's **HITL level** and scales how far it may act on a stuck/stalled run:

| HITL | CEO may… |
| --- | --- |
| **High** | Only **surface/escalate** to the human. Never auto-act. |
| **Medium** | **Nudge** within bounds (re-dispatch the next chunk, restart a dead session, re-ask a pending question), surface decisions. |
| **Low** | Get a stuck run **back on track autonomously** — restart, re-spawn, unblock, advance to the next chunk — without waiting for the human. |

**Hard cap regardless of HITL (dev-principle #5):** the CEO never autonomously crosses an irreversible /
safety-critical line — **promotion into UAT or Prod (D3-uat / D4-prod)**, money, credentials, deletions,
external sends. Those stay human at every HITL level. Deploying up to **D2-test** is *within* the
reversible autonomy band where the rung permits it (§7). HITL widens the reversible band; never the cap.

## 9. Elicited requirements

- **R1** Superhuman owns its sessions (incl. session-relay, incl. dependencies).
- **R2** Skill relationships / bundling — companion-vs-dependency + bundle-vs-schema-contract (§14).
- **R3** Visible dashboard + explicit dependency tracking (table + Mermaid DAG).
- **R4** Queryable any time: "how does X relate to / depend on Y?"
- **R5** the roadmap tracker used more effectively — intentional issue dependencies.
- **R6** Backlog resurfacing — nothing rots.
- **R7** Adopt native Agent Teams (Claude adapter real-time layer).
- **R8** The **active** CEO — proactively **surveys and prioritizes across all three surfaces** (open
  sessions, roadmap issues, superhuman projects), determines what's next, and keeps things from dying.
  **Phase 2** (session/project prioritization → P2.4; roadmap-issue prioritization → P2.6). *Not Phase 1.*
- **R9** Proactive **trigger/hook** — CEO looks whether or not asked.
- **R10** Track **manual handoffs** — `awaiting-launch` row at prompt-emission; stale row = dropped thread.
- **R11** **Harness-agnostic** (§6).
- **R12 [new]** CEO **actively hunts sessions that escaped tracking** — enumerate all live sessions,
  diff against the union of manifests, adopt/flag orphans.
- **R13 [new]** CEO is **HITL-aware** — autonomy scaled by the project's HITL level (§8), DP#5-capped.
- **R14 [new]** **Levels of done** (§7) — the manifest tracks `done-level`; the CEO surfaces the gap
  between superhuman-"done" and prod-accepted; `pending-prod` stays visible where no prod exists.
- **R15 [new]** **Self-improvement log** — the CEO keeps a *separate, prioritized* log of self-
  improvements and does **not** implement meaningful ones absent HITL. An **overnight scheduled job**
  reviews problems, failures, inefficiencies, cost/task, and UX (reduced human involvement, clarity of
  responses/questions) and ranks proposals. → **Phase 3** (§16).
- **R16 [new]** **Provider-plan / budget awareness.** The CEO is aware of LLM-provider plan usage (e.g.
  Claude's rolling **5-hour** and **1-week** windows) and throttles so it never exhausts the window —
  always leaving headroom for the operator, maximizing value extracted from providers against the limits.
  → **Phase 3/4**; short-term stopgap if the limit bites sooner (§16).
- **R17 [new]** **Superhuman self-un-stall (works without the CEO).** Others may install superhuman
  *without* the CEO, so superhuman must not silently stall on its own: from the manifest it detects its
  own dead/idle sessions and stale `awaiting-launch` handoffs and **re-surfaces or re-dispatches** them.
  The CEO adds cross-project, HITL-scaled healing *on top*; it is not a prerequisite for baseline recovery.
  **Scheduled: Phase 2** (deferred out of Phase 1 at G0, 2026-08-13) — but built **superhuman-side**, not
  CEO-side, so it functions with no CEO installed. Phase 1 only makes stalls *visible* in the manifest.
- **R18 [new]** **Portability proof each phase.** At the end of every phase, ship to **hermeslab** (a
  Hermes harness) and exercise the deliverable there, proving the portable core actually runs off-Claude —
  portability is *demonstrated per phase*, not asserted and deferred. (Revises the VISION's NG-5.)
- **R19 [new]** **CEO model-tier routing / cost-effectiveness.** The CEO runs its own judgment
  (prioritization, stuck-diagnosis, next-action) on a **most-capable** model (Opus / GPT-class) but
  **delegates easier work to cheaper agents** (Sonnet/Haiku) — the same cheapest-capable-model discipline
  superhuman already applies (dev-principles; `adaptation/dispatch.md`). **Cost-effectiveness is an
  explicit build goal**, decided early (the CEO's role→tier table) rather than retrofitted. Relates to
  R16 (budget awareness). → **Phase 2** (early decision).

## 10. Vision

**Two cooperating layers over one shared manifest, delivered in three phases.** The **CEO overseer**
is a standing, autonomous, HITL-aware supervisor that keeps the enterprise alive — surveying sessions,
issues, and superhuman projects; hunting escaped sessions; detecting stuck/dropped/aging work;
self-healing what it safely may (scaled by HITL, capped by DP#5); and refusing to let anything read as
"done" before it is accepted in production by a human. **Superhuman** becomes a disciplined citizen:
it tracks every session it causes to exist, portably, and reports honest done-levels. A nightly
**self-improvement** loop proposes — never silently enacts — how the whole system should get better.

## 11. Goals

- **G1** One SoT for "where is every session and what is it blocked on" — per project and across.
- **G2** Dependencies explicit, never implicit (§4).
- **G3** Visible dashboard on demand: table **plus** Mermaid DAG (blocked edges highlighted).
- **G4** Queryable relationships.
- **G5** No backlog rot.
- **G6** Reuse over reinvent.
- **G7** Deterministic where safety-critical (DP#5); LLM only for synthesis/recommendation.
- **G8** **Active liveness (anti-rot)** — proactively detect+counteract stalling/dropping/forgetting;
  superhuman's silent gate-pauses are explicitly guarded.
- **G9** **Proactive trigger** — runs whether or not invoked (hook/cron).
- **G10** **Harness portability** — manifest + tracking harness-neutral; Claude tools are one adapter.
- **G11 [new]** **Honest done** — nothing terminal-`done` until human-accepted in prod (D4); the gap is
  always visible; `pending-prod` never closes silently.
- **G12 [new]** **HITL-scaled self-healing** — the CEO recovers stuck work autonomously up to the
  project's HITL band, never past the DP#5 irreversible cap.
- **G13 [new]** **Disciplined self-improvement** — a prioritized improvement log + nightly review;
  meaningful changes proposed, never enacted without HITL.

**GitHub in the goals:** not a goal-outcome — it is the **backbone + a watched surface** (G5; open
issues are one of the three surfaces the CEO watches under G8). Kept as mechanism, surfaced here.

## 12. Non-goals

- **NG-1** The CEO is not a subfeature of one superhuman project (though built *by* one — §16).
- **NG-2** Not replacing session-relay (Claude executor/adapter) or superhuman (planner/tracker).
- **NG-3** No new roadmap tracker — the existing one stays SoT.
- **NG-4** No introspection of a session's internal reasoning — track *state and edges*, not thoughts.
- **NG-5** Manifest core carries no harness-specific calls.
- **NG-6 [new]** The CEO does **not** self-modify meaningfully, promote/deploy, or cross any DP#5 line
  autonomously — regardless of HITL. Self-improvement is *proposal*, not *enactment*.

## 13. The manifest — shared substrate

- **Home (open, §18):** dedicated `FLEET.md` per slug vs. `## Fleet` in `SUPERHUMAN.md` (leaning file).
- **Per-entry fields:** session id · title · cwd/branch · owned gate/chunk or issue# · **status**
  (`awaiting-launch|active|blocked|needs-review|pending-prod|done|orphaned`) · **`depends-on:`**
  `[session-id|#issue]` · **project HITL level** (H/M/L) · **`done-level`** (D0-code … D4-prod, §7).
- **Manual-handoff mechanism (R10):** superhuman writes the row **when it emits the prompt**
  (`awaiting-launch`); the launched session self-registers → `active` on first action; a stale
  `awaiting-launch` = a detectable **dropped handoff** (feeds G8).
- **Escaped-session hunt (R12):** the CEO diffs live sessions (harness adapter) against the union of
  all manifests and adopts/flags anything untracked or stale.

## 14. Skills bundle vs. schema contract (R2)

Interop point = the **manifest schema** (harness-neutral). **Now:** define a shared **manifest
contract** (schema + location). **Later, if it earns it:** bundle the tightly-coupled trio
(superhuman + CEO + Claude session adapter). **Independent:** disciplines (governing peer),
skill-audit (conditional QA companion). Decision belongs to the projects, not pre-committed here.

## 15. Low-hanging-fruit menu (maps to phases in §16)

| ID | Item | Effort | Phase |
| --- | --- | --- | --- |
| LHF-1 | Per-project **manifest** (`FLEET.md`) — foundation | XS | **P1** |
| LHF-2 | Explicit `depends-on:` notation | XS | **P1** |
| LHF-3 | Register every session — spawned, relayed, **and manual** | S | **P1** |
| LHF-9 | Formalize skill relationships + schema-vs-bundle | XS | **P1** |
| LHF-8 | Native Agent Teams pilot (Claude real-time layer) | XS | **P1?** |
| LHF-4 | Dashboard render (table) | S–M | **P2.1** |
| LHF-5 | Mermaid dependency DAG + queries | S | **P2.2** |
| LHF-6 | Roadmap native issue deps + Blocked/Ready views | S | **P2.6** |
| LHF-7 | Backlog-resurfacing cron | S | **P2.6** |

## 16. Phased delivery — each phase (and P2 sub-phase) ships standalone value

**Phase 1 — Superhuman session tracking & dependencies.** Superhuman never causes a session to exist
without tracking it, portably. Scope: LHF-1/2/3/9 (+ maybe 8); the portable manifest interface (§6);
manual-handoff mechanism (§13); and the manifest **schema** including the `HITL` and `done-level`
fields that Phase 2 acts on. Phase 1 makes stalls *visible* only — self-un-stall (R17) was deferred to
Phase 2 at G0. **Phase 1's G0 decides the exact "implement now" scope.** *Value: honest, durable
visibility of everything superhuman spawns.*

**Phase 2 — The CEO overseer.** Broken into value-delivering slices (Phase 2's G0 sequences them):

| Slice | Deliverable | Standalone value |
| --- | --- | --- |
| **P2.1** | **Read-only fleet dashboard** — reconcile manifests + live sessions + git/PR into one status table | See everything in one place |
| **P2.2** | **Dependency DAG + relationship queries** (Mermaid; "how does X relate to Y") | Dependency visibility (R3/R4) |
| **P2.3** | **Escaped-session sweep + orphan adoption** (R12) | Nothing hides from tracking |
| **P2.4** | **Stall/drop/aging detection + escalation** (passive; proactive trigger G9) | Anti-rot alerting; HITL-High behavior |
| **P2.5** | **HITL-scaled autonomous recovery** (§8) — self-heal stuck runs within the HITL band, DP#5-capped | Self-healing; less human babysitting |
| **P2.6** | **Roadmap dependency hygiene + backlog-resurfacing cron** (LHF-6/7) | Backlog never rots |
| **P2.7** | **Levels-of-done tracking** (§7) — surface the gap to prod-acceptance; keep `pending-prod` visible | No false "done" |

Phase 2 also ships **P2.0 — superhuman-native self-un-stall (R17)**: a superhuman-side capability
(re-surface stale `awaiting-launch`, re-dispatch a dead subagent) that works with **no CEO installed**,
distinct from the CEO's cross-project HITL-scaled healing. Deferred here from Phase 1 at G0.

*Dependency: **Phase 1 blocks Phase 2** — the CEO cannot supervise a manifest superhuman is not yet
populating. Within Phase 2, P2.1 unblocks P2.2–P2.4; P2.5 depends on P2.4.*

**Phase 3 — Disciplined self-improvement (R15/G13).** A **separate prioritized self-improvement log**
plus an **overnight scheduled job** that reviews problems, failures, inefficiencies, cost/task, and UX
(reduced human involvement; clarity of responses/questions) and **ranks proposals**. Meaningful changes
are **proposed, never enacted, without HITL** (NG-6). *Value: the system compounds — getting cheaper,
clearer, and less human-dependent over time, safely.*

**Phase-end portability proof (R18) — every phase.** Before a phase is accepted, its deliverable is
shipped to **hermeslab** (Hermes harness) and exercised there. A phase that only works under Claude is
not done. This turns "harness-agnostic" from an assertion into a per-phase test.

**Phase 4 (candidate) — provider-plan / budget awareness (R16).** The CEO learns the operator's LLM-plan
windows (Claude 5-hour / 1-week) and throttles to leave headroom, maximizing value against limits. Pulled
forward as a stopgap if the limit bites during Phase 2/3.

**Roadmap / research ideas (not yet phased — route to the roadmap tracker):**
- **Agents-as-employees.** Refactor superhuman's agent use so a role agent (developer, architect) holds a
  *task list* and, on getting stuck, **escalates and switches to another task** instead of blocking — a
  "mini-CEO" per agent type. Larger than this effort; a research/roadmap candidate, not a phase here.

## 17. Working sequence

1. Vision & goals ✅  2. Low-hanging fruit ✅  3. Document everything ✅
4. ~~Selectively implement now~~ → **deferred into Phase 1's G0** (operator's instruction).
5. Run **superhuman Phase 1** → then **Phase 2** (P2.1…P2.7) → then **Phase 3**.

## 18. Open questions (for the phases' G0)

1. **Manifest home** — `FLEET.md` per slug vs. section in `SUPERHUMAN.md`. *(P1)*
2. **Cross-project roll-up** — single top-level CEO view vs. per-project + roll-up. *(P2)*
3. **Dashboard surface** — markdown vs. HTML artifact vs. both. *(P2.1)*
4. **Edge inheritance** — session edges auto-inherit from served issue/phase, or declared+reconciled? *(P1/P2)*
5. **Agent Teams boundary** — Claude real-time vs. portable durable manifest. *(P1)*
6. **CEO cadence & the autonomy line** — how often it runs; the exact HITL×action matrix and where the
   code/LLM line sits per DP#5. *(P2)*
7. **Backlog cron scope** — the roadmap tracker only vs. every repo in the org. *(P2.6)*
8. **Bundle vs. schema contract** — §14. *(P1)*
9. **CEO portability** — harness-agnostic CEO vs. per-harness supervisor over a portable manifest. *(P2)*
10. **Done-level detection** — how the CEO learns a project's current D-level (deploy registry? env
    probes? disciplines ledger?) and its D-ceiling where no prod exists. *(P2.7)*
11. **Self-improvement job autonomy** — what (if anything) it may auto-apply (trivial/safe only) vs.
    always-propose; where it writes its log. *(P3)*

## 20. Round-4 review adoptions (2026-08-14)

Consolidated from three independent reviews — PM/G0 self-review, `gpt-5` (Codex) adversarial review, and
the roadmap scan. **All adopted.** Phase-1 items (CC-1…CC-8) are formalized in
`docs/superhuman/session-tracking/REQUIREMENTS.md`; later-phase items (CC-9…CC-12) are recorded here.

**Phase 1 (in REQUIREMENTS):**
- **CC-1 Storage** — manifest = append-only JSONL event log + per-session fragment files, atomic
  create/rename, idempotency keys; `FLEET.md` is a generated *view*, not the SoT. *Supersedes the
  provisional single-`status` schema in §13.* (Resolves the concurrency open question.)
- **CC-2 Status decomposed** — orthogonal fields `lifecycle · block_state · review_state ·
  adoption_state · done_level` (one enum can't say "active AND blocked" without lying).
- **CC-3 Dependency graph** — namespaced node IDs (ids collide across repos/harnesses), typed edges,
  edge source/evidence (derived vs declared), cycle handling; auto-derive where possible.
- **CC-4 Handoff durability** — durable `handoff_id` + expiry/cancel; a Phase-1 stale-handoff report.
- **CC-5 Evidence-backed `done_level`** — commit/PR/CI/deploy-id/env/approver/timestamp + project
  D-ceiling; not self-assertable.
- **CC-6 State-ownership** — superhuman owns intra-project execution state; the CEO observes/recommends
  or calls a recovery interface, never mutates owned lifecycle fields except scoped orphan flags.
- **CC-7 Tiny throwaway `fleet status` viewer** in Phase 1 (validates schema + early win; superseded by
  P2.1). **LHF-8 (Agent Teams) cut** from the Phase-1 critical path.
- **CC-8 Portability = conformance suite** (harness-neutral create/update/validate/query); hermeslab is
  confirmation once a real Hermes adapter exists, not a hard blocker.

**Phase 2/3 (recorded for later):**
- **CC-9** HITL autonomy = enforceable **action-class matrix** (reversibility / external side-effect /
  cost / credential / data-loss / env) + bounded retries + evidence before escalation. Harvest IDEA-034
  shadow-mode probation + confidence thresholds.
- **CC-10** Orphan-hunt is **flag-only + scoped** to allowlisted workspace roots; classify on
  cwd/title/branch, **never transcripts** (preserves NG-4).
- **CC-11** CEO **budget hard-limits from day one of Phase 2** (max cycles/day, sessions/cycle, respawns,
  tokens/run, stop-when-uncertain) — do not wait for Phase 4.
- **CC-12** Self-improvement **proposals-only first**; auto-apply later, reports/metadata only, after
  explicit policy approval.

**Roadmap cross-refs** (registry: the roadmap tracker's research index):
IDEA-034 (CEO / self-improvement prior art), IDEA-035 (rejected — the DP#5 guardrail boundary),
IDEA-036 & IDEA-030 (cost / model-tier backing for R16/R19 — secondary), IDEA-013 → roadmap #95 (Hermes
multi-session — the hermeslab reference), IDEA-028 (role-based audit primitive). This effort was
registered as a new IDEA in that file (2026-08-14).

**Attribution rule (IDEA-028 tweak):** every signature/attribution this system writes — commit trailers,
manifest entries, the CEO audit trail — names **roles** (CEO, Project Manager, Developer…),
**never** an AI/model/provider.

## 19. Sources

- Addy Osmani — "The Code Agent Orchestra" — addyosmani.com/blog/code-agent-orchestra/
- Shipyard — "Multi-agent orchestration for Claude Code in 2026" — shipyard.build/blog/claude-code-multi-agent/
- Claude Code Docs — "Agent Teams" — code.claude.com/docs/en/agent-teams
- MindStudio — "Claude Code Agent Teams: parallel workflows" — mindstudio.ai/blog/claude-code-agent-teams-parallel-workflows
- MindStudio — "AI command center: managing multiple Claude Code agents" — mindstudio.ai/blog/ai-command-center-managing-multiple-claude-code-agents
