import ast
from pathlib import Path


def test_all_environment_and_evidence_write_functions_record_audit():
    root = Path(__file__).parents[1] / "app"
    for relative in ("environment/service.py", "evidence/service.py"):
        tree = ast.parse((root / relative).read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = [
                child
                for child in ast.walk(node)
                if isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == "session"
            ]
            writes = [call for call in calls if call.func.attr in {"add", "delete", "add_all"}]
            if writes:
                assert any(
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and child.func.id == "record"
                    for child in ast.walk(node)
                ), f"{relative}:{node.name} mutates session without record()"
