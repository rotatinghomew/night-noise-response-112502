#!/usr/bin/env python3
"""夜间噪声证据与巡查资源复议命令行入口。

用法（在仓库根目录）：
    python3 run_report.py --data data --target-night 2026-10-07
    python3 run_report.py --data data --correction sv-n01-v2
    python3 run_report.py --data data --trace rec-new-001

脚本把 src/ 加入模块搜索路径，无需安装包；不修改任何原始资料。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from night_noise_response.pipeline import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
