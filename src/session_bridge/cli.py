"""session-bridge CLI.

    session-bridge inspect --from claude-code SESSION.jsonl
    session-bridge convert --from codex --to hermes SESSION.jsonl -o out.jsonl
    session-bridge convert --from claude-code --to codex SESSION.jsonl --no-handshake
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .convert import HARNESSES, convert, dump_jsonl, read_session
from .ir import BlockType


def _count_blocks(session, block_type):
    return sum(1 for m in session.messages for b in m.content if b.type is block_type)


def cmd_inspect(args: argparse.Namespace) -> int:
    session = read_session(args.source, args.path)
    m = session.meta
    print(f"source harness : {m.source_harness}")
    print(f"session id     : {m.session_id}")
    print(f"model          : {m.model}  (provider: {m.model_provider})")
    print(f"cwd            : {m.cwd}")
    print(f"permission     : {m.permission_mode}")
    print(f"messages       : {len(session.messages)}")
    print(f"  text blocks    : {_count_blocks(session, BlockType.TEXT)}")
    print(f"  reasoning      : {_count_blocks(session, BlockType.REASONING)}")
    print(f"  tool calls     : {_count_blocks(session, BlockType.TOOL_CALL)}")
    print(f"  tool results   : {_count_blocks(session, BlockType.TOOL_RESULT)}")
    print(f"tool schemas   : {len(session.tools)}")
    p = session.pending
    print("pending state  :")
    print(f"  open tool calls    : {list(p.open_tool_calls)}")
    print(f"  queued user input  : {len(p.queued_user_messages)}")
    print(f"  active goal        : {p.active_goal or '-'}")
    return 0


def cmd_convert(args: argparse.Namespace) -> int:
    result = convert(
        args.source,
        args.target,
        args.path,
        inject_handshake=not args.no_handshake,
    )
    out = args.output or (Path(args.path).with_suffix("").name + f".{args.target}.jsonl")
    dump_jsonl(result.records, out)
    print(f"wrote {len(result.records)} records -> {out}")

    if result.report.warnings:
        print(f"\n{len(result.report.warnings)} conversion note(s):", file=sys.stderr)
        for w in result.report.warnings:
            print(f"  - {w}", file=sys.stderr)
    else:
        print("\nlossless conversion (no warnings).", file=sys.stderr)

    if args.handshake_out:
        Path(args.handshake_out).write_text(result.handshake, encoding="utf-8")
        print(f"wrote resume handshake -> {args.handshake_out}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="session-bridge", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    insp = sub.add_parser("inspect", help="parse a session and print its structure")
    insp.add_argument("--from", dest="source", required=True, choices=HARNESSES)
    insp.add_argument("path")
    insp.set_defaults(func=cmd_inspect)

    conv = sub.add_parser("convert", help="convert a session between harnesses")
    conv.add_argument("--from", dest="source", required=True, choices=HARNESSES)
    conv.add_argument("--to", dest="target", required=True, choices=HARNESSES)
    conv.add_argument("path")
    conv.add_argument("-o", "--output", help="output JSONL path")
    conv.add_argument("--handshake-out", help="also write the resume handshake to this path")
    conv.add_argument("--no-handshake", action="store_true",
                      help="do not prepend the resume handshake message")
    conv.set_defaults(func=cmd_convert)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
