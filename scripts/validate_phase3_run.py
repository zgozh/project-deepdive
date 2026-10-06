#!/usr/bin/env python3
"""Re-read and re-audit a portable Phase 3 run package."""

from __future__ import annotations

import sys

from phase3_run import validate_main


if __name__ == "__main__":
    sys.exit(validate_main())
