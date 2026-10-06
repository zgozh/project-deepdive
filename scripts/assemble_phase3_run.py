#!/usr/bin/env python3
"""Create a portable package from already-generated Phase 2/3 artifacts."""

from __future__ import annotations

import sys

from phase3_run import assemble_main


if __name__ == "__main__":
    sys.exit(assemble_main())
