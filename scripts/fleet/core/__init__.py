"""Harness-neutral manifest core.

Everything under ``scripts.fleet.core`` is importable and fully testable with
no Claude-specific tools, no ``session-relay``, and no adapter of any kind on
the import path (NFR-2). The only harness-aware layer is ``scripts.fleet.adapter``,
which calls into this package — never the other way around. See
``docs/superhuman/session-tracking/ARCHITECTURE.md`` "Dependency map".
"""
