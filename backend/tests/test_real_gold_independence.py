"""真实语料标准答案必须独立于项目解析器：生成链路的模块不得导入 app。"""
import ast
from pathlib import Path

GOLD_CHAIN = ["extract.py", "label.py", "gold.py", "review_view.py"]


def test_gold_pipeline_does_not_import_project_code():
    root = Path(__file__).resolve().parents[1] / "evals" / "real"
    for name in GOLD_CHAIN:
        path = root / name
        if not path.exists():
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            assert not any(m == "app" or m.startswith("app.") for m in mods), f"{name} imports {mods}"
