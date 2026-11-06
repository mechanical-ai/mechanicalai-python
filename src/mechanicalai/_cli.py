from __future__ import annotations

import sys

from mechanicalai import __version__


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args in ([], ["--version"], ["-V"]):
        print(f"mechanicalai {__version__}")
        return 0
    print("The mechanicalai CLI is under development.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
