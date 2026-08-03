"""A tiny greeting CLI used as a canonical superhuman smoke fixture.

This module is intentionally minimal: it exists so a human can point the
superhuman skill at a small, obviously-shaped project and watch the full
gate sequence (G0..G8) play out end to end.
"""

from __future__ import annotations

import argparse
import sys


def greet(name: str) -> str:
    """Build a greeting for the given name.

    Args:
        name: The name to greet.

    Returns:
        A greeting string of the form ``"Hello, <name>!"``.
    """
    return f"Hello, {name}!"


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser for the ``hello-cli`` command.

    Returns:
        The configured :class:`argparse.ArgumentParser` with a ``greet``
        subcommand.
    """
    parser = argparse.ArgumentParser(
        prog="hello-cli",
        description="A tiny greeting CLI.",
    )
    parser.add_argument("--version", action="version", version="hello-cli 0.1.0")

    sub = parser.add_subparsers(dest="command", required=True)
    greet_parser = sub.add_parser("greet", help="Print a greeting.")
    greet_parser.add_argument("--name", required=True, help="Name to greet.")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``hello-cli`` command.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code (0 on success).
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "greet":
        print(greet(args.name))
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2  # unreachable; parser.error exits


if __name__ == "__main__":
    sys.exit(main())
