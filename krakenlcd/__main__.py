# SPDX-License-Identifier: GPL-3.0-or-later
"""Lets the package be run straight out of a checkout: python3 -m krakenlcd"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
