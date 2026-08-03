"""Leak patterns for the publication guard.

Kept in its own module for two reasons, both learned the hard way:

1. **The self-exemption must be narrow.** When these patterns lived in the test
   file, exempting that file also exempted every docstring in it — which
   silently hid four real leaks in unrelated test prose.
2. **The patterns need their own tests.** An earlier revision of this table was
   written with literal backspace bytes where word-boundary escapes were
   intended, so most patterns matched nothing and the guard passed vacuously.
   `test_publication_guard.py` now asserts every pattern against a
   known-positive and a known-negative sample. A guard with no test of its own
   is a guard you cannot trust.
"""

from __future__ import annotations

#: Infrastructure leaks that no published repository should contain. These are
#: patterns, not names, so the guard is useful to anyone publishing a fork — not
#: only to whoever wrote it.
#:
#: Each entry is ``(regex, label, positive_sample, negative_sample)``. The
#: samples are not documentation: the guard's own tests run them, so a pattern
#: that stops working fails loudly instead of silently passing.
LEAK_PATTERNS: tuple[tuple[str, str, str, str], ...] = (
    (
        # Routable IPv4 only. Loopback and wildcard binds are legitimate in
        # shipped code — servers bind them by default — and are not leaks.
        r"\b(?!0\.0\.0\.0\b)(?!127\.)(?:\d{1,3}\.){3}\d{1,3}\b",
        "routable IP address",
        "deploy to 203.0.113.7 nightly",
        'BIND_HOST="127.0.0.1"  # or 0.0.0.0',
    ),
    (
        r"(?<![\w/])/opt/[a-z][\w.-]*/",
        "absolute server path",
        "cd /opt/myenv/workspace",
        "see <workspace>/skills/",
    ),
    (
        # RFC 2606 reserves example.com/org/net and the .test/.invalid/
        # .localhost TLDs precisely so they can be written down. Excluding them
        # keeps the guard from flagging every test fixture, which is how a guard
        # trains people to ignore it.
        r"\b\w+@(?!example\.(?:com|org|net)\b)"
        r"(?![a-z0-9.-]*\.(?:test|invalid|localhost)\b)"
        r"(?:\d{1,3}(?:\.\d{1,3}){3}|[a-z0-9-]+\.[a-z]{2,})",
        "ssh or email target",
        "ssh root@10.1.2.3",
        "ssh <user>@<host>, or test@example.com in a fixture",
    ),
    (
        r"\.github_pat\b|\bgh[po]_[A-Za-z0-9]{16,}",
        "credential file or token",
        "cat /run/secrets/.github_pat",
        "authenticate with gh auth login",
    ),
    (
        r"\bssh-(?:rsa|ed25519)\s+AAAA",
        "public key material",
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5 someone",
        "generate a key with ssh-keygen",
    ),
    (
        r"BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY",
        "private key material",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        "store the private key outside the repo",
    ),
)

#: Organisation-specific words are site-specific by nature, so they are NOT
#: hardcoded here. Put one token per line in `.publication-tokens` at the repo
#: root (gitignored) and the guard rejects those too. That keeps an operator's
#: environment vocabulary out of the published tree without baking anyone's
#: names into a shared test.
TOKENS_FILE = ".publication-tokens"

#: File extensions the guard reads — anything textual that ships.
SCANNED_SUFFIXES = (
    ".md", ".tpl", ".py", ".sh", ".yaml", ".yml",
    ".json", ".cmd", ".cjs", ".js", ".ts", ".dot",
)

#: Paths exempt from the leak scan, each for a stated reason. Keep this list as
#: short as possible: every exemption names a file the guard cannot vouch for.
PUBLICATION_EXEMPT = (
    # Defines the patterns and their samples, so it necessarily contains them.
    "tests/publication_patterns.py",
    # Asserts the patterns against those samples, so it contains them too.
    "tests/test_publication_guard.py",
)
