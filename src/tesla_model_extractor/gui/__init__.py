"""Desktop app: the extractor behind a window, for people who would rather not use a terminal."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None, source: str | None = None) -> int:
    try:
        import PySide6  # noqa: F401
    except ImportError:
        print(
            "The desktop app needs PySide6: pip install 'tesla-model-extractor[gui]'",
            file=sys.stderr,
        )
        return 1
    from .window import run

    return run(argv, source)
