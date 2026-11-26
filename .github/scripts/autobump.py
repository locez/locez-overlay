#!/usr/bin/env python3
"""CLI entrypoint for the locez-overlay automated package bumper."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bumper.cli import main

if __name__ == "__main__":
    sys.exit(main())
