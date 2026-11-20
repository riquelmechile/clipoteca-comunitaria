from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clipoteca")
    parser.add_argument("command", choices=("version",))
    args = parser.parse_args(argv)
    if args.command == "version":
        print("clipoteca 0.1.0")
    return 0
