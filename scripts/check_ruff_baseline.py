"""ruff 告警数不许增加的闸门。

既有告警有一批历史基线（backend/.ruff-baseline），一次清不完；这里只保证
数量不涨。修掉告警后请把基线同步调低，让闸门跟着收紧。
用法（在 backend/ 下）：python ../scripts/check_ruff_baseline.py
"""

import json
import subprocess
import sys
from pathlib import Path

BASELINE = Path(".ruff-baseline")


def main() -> int:
    out = subprocess.run(
        ["ruff", "check", "--exit-zero", "--output-format", "json", "app", "tests"],
        capture_output=True, text=True, check=True,
    ).stdout
    count = len(json.loads(out or "[]"))
    baseline = int(BASELINE.read_text().strip())
    print(f"ruff violations: {count} (baseline {baseline})")
    if count > baseline:
        print("ruff 告警数超过基线：请修掉新增告警。", file=sys.stderr)
        return 1
    if count < baseline:
        print(f"告警减少了，请把 backend/.ruff-baseline 调到 {count}。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
