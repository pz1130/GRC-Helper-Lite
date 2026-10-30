"""可选 IT 内容库：只供人工选用，不产生控制、映射或评分。v1 发布后保持不变。"""

import json
from pathlib import Path

CATALOG = json.loads(Path(__file__).with_name("it_catalog_v1.json").read_text())
GUIDE_IDS = frozenset(guide["id"] for guide in CATALOG["guides"])
