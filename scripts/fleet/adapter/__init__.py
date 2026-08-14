"""The only harness-aware layer in ``scripts.fleet`` (NFR-2).

Everything Claude-specific (which session am I, enumerate live sessions,
emit a handoff prompt) is reached only through the ``SessionAdapter``
interface (`adapter.base`) and its two Phase-1 implementations —
`adapter.portable` (git + filesystem + env only) and `adapter.claude`
(native session tools + ``session-relay``). ``scripts.fleet.core`` never
imports anything from here; see
``docs/superhuman/session-tracking/ARCHITECTURE.md`` "Dependency map".
"""
