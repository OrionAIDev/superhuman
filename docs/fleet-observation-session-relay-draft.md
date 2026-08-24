# DRAFT — proposed `session-relay` `references/handoff.md` call-out

**Status:** draft only, held for PM review. This file lives in the `superhuman` repo as project
documentation of a cross-repo proposal — it is NOT the cross-repo edit itself. Per Chunk 6's binding
cross-repo note ("the `session-relay` edit lands in a different repository ... must not be bundled
into this repo's history. Flag to the PM before touching it."), the `session-relay` repository has
not been touched.

**Where this would land, in `session-relay`:** a new subsection in `session-relay`'s
`references/handoff.md`, placed after the existing HANDOFF-flow documentation (the point where
`session-relay` has just delivered a kickoff prompt to a newly launched session). The proposed prose
follows the same non-gating, fail-soft framing `superhuman`'s own seam prose already uses in
`roles/pm.md` ("Handoff prompt emission", "Fleet dispatch observation (spawned path)") — see those
two subsections for the house style this draft matches.

---

## Proposed subsection text (verbatim, for `session-relay/references/handoff.md`)

> ## Fleet observation (relayed path)
>
> After `session-relay` delivers a HANDOFF kickoff prompt to a newly launched session — the moment
> a relayed session's identity (harness, session id, workspace, branch) is first known —
> `session-relay` calls:
>
> ```
> fleet observe relay --harness claude --session-id <session-id> --workspace <path> \
>     --slug <slug> --writer-role pm
> ```
>
> to record the relay. `<session-id>` is the launched session's native id; `<path>` and `<slug>`
> identify the target superhuman project the same way every other `fleet observe` call does.
>
> This step is purely observational: it never blocks the handoff, it never delays prompt delivery,
> and a failure (`fleet` unconfigured for the target project, an unavailable manifest write, or any
> other fault) is logged and the handoff itself proceeds unaffected. `fleet observe relay` always
> exits `0`; `session-relay` does not need to inspect its output or react to its exit code. If the
> `fleet` CLI is not present at all (e.g. `superhuman` not installed in the target workspace), the
> call is simply skipped — `session-relay`'s own handoff mechanics are entirely independent of
> whether this observation succeeds, is attempted, or is even possible.

---

## Notes for the PM's review

- Command shape and flags verified against `scripts/fleet/cli.py`'s `relay` subparser (`--workspace`,
  `--slug`, `--writer-role`, plus the shared `--harness`/`--session-id`/`--session-relay-script`
  harness arguments) as of this repo's `superhuman/fleet-wiring` branch, HEAD `34c49f1`.
- Framing mirrors `roles/pm.md`'s existing "Handoff prompt emission" and "Fleet dispatch observation
  (spawned path)" subsections: non-gating, "logged and proceeds unaffected", always-exits-0.
- Not addressed here (left for the PM/`session-relay` maintainer to decide at apply time): exactly
  which point in `session-relay`'s own control flow this call is inserted at, and whether
  `--session-relay-script` should be passed through so the relay's own adapter gets the same
  git-fact enrichment `session-relay` itself would apply.
