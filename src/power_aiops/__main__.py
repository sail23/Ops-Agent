"""CLI 包入口：`python -m power_aiops` → `power_aiops.cli:main`。"""

import sys

from power_aiops.cli import main

if __name__ == "__main__":
    sys.exit(main())
