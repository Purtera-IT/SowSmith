"""SOWSmith CLI — render an envelope to a SOW markdown file.

Usage:

    sowsmith render <envelope.json> [--out sow.md]
    sowsmith --version

The envelope JSON is the ``orbitbrief.input.v2`` document produced by
parser-os (``app.core.orbitbrief_envelope.write_orbitbrief_envelope``).
Output is a contract-grade markdown SOW with every claim cited to a
source atom ID.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sowsmith import __version__
from sowsmith.render import SOW_VERSION, build_sow_markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sowsmith",
        description=(
            "Deterministic Statement of Work generator from an "
            "OrbitBrief envelope (orbitbrief.input.v2). Every claim "
            "in the output traces to a source atom ID."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"sowsmith {__version__} (renderer {SOW_VERSION})",
    )
    subs = parser.add_subparsers(dest="command", required=True)

    render_parser = subs.add_parser(
        "render",
        help="Render an envelope JSON file to a SOW markdown file.",
    )
    render_parser.add_argument(
        "envelope",
        type=Path,
        help="Path to the orbitbrief.input.v2 envelope JSON.",
    )
    render_parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "Output markdown path (default: <envelope-dir>/sow.md). "
            "Use '-' to write to stdout."
        ),
    )

    args = parser.parse_args(argv)

    if args.command == "render":
        return _cmd_render(args.envelope, args.out)
    parser.print_help()
    return 1


def _cmd_render(envelope_path: Path, out_path: Path | None) -> int:
    if not envelope_path.is_file():
        print(f"error: envelope not found: {envelope_path}", file=sys.stderr)
        return 2
    try:
        envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"error: envelope is not valid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(envelope, dict):
        print("error: envelope root must be a JSON object", file=sys.stderr)
        return 2

    schema = envelope.get("schema_version") or ""
    if not schema.startswith("orbitbrief.input."):
        print(
            f"warning: envelope schema_version={schema!r} does not look "
            "like an orbitbrief.input.v2 document; rendering anyway",
            file=sys.stderr,
        )

    sow_markdown = build_sow_markdown(envelope)

    if out_path is None:
        out_path = envelope_path.parent / "sow.md"
    if str(out_path) == "-":
        sys.stdout.write(sow_markdown)
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(sow_markdown, encoding="utf-8")
    print(f"wrote {out_path}  ({len(sow_markdown):,} chars, {len(sow_markdown.splitlines())} lines)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
