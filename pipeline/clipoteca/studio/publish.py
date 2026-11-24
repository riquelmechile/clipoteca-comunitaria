"""Retired video publication entrypoint; use studio.index for clips only."""

import sys


def main(argv=None):
    print("Video publication is retired; use clipoteca.studio.index for clips.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
