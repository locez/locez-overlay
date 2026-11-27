#!/usr/bin/env python3
"""Entrypoint for the reusable Gentoo overlay automation engine."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.cli import main

if __name__ == "__main__":
    sys.exit(main())
