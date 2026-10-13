#!/usr/bin/env python3
"""后端的 code 与前端词条对不对得上。

后端**不产出给人读的句子**（见 CLAUDE.md「语言约定」）：业务错误带
`code="control.not_found"`，解析告警带 `code="shape.one_clause_dominates"`，
前端按 code 去 `en.json` 查词条。

漏一条词条**不会报错**——`ApiError` 与 `parseWarningText` 都会回落到后端那句
中文，界面照常能用，只是英文用户看到中文。也就是说这类缺失**不会被任何测试
或构建抓到**，只会被用户撞见。这个脚本就是为此存在的。

查两件：
  1. 后端用到的 code，en.json 里都得有词条；
  2. 词条里有、后端没用到的（多半是改名留下的死词条）。

产品只有英文界面（2026-09-23 起，zh.json 已删除）。原来的第三件——中英两边的
{{变量}} 一致——随之取消。

不连数据库、不进容器，纯读文件。`make i18n-check`，也在 `make verify` 里。
"""

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend" / "app"
LOCALES = ROOT / "frontend" / "src" / "locales"

# 告警 code 独占这两个命名空间，所以字面量扫到就算——`numbering.py` 把 code 放在
# 一张表里再取出来用，`code=` 那条规则看不见它们。
WARNING_NAMESPACES = ("parse.", "shape.")
# 控制点合并预览里的丢弃项与阻断原因（app/controls/merge.py），词条在 mergeReasons 下。
MERGE_NAMESPACES = ("merge.",)
# 解析失败的 code（documents.parse_error_code）。多数写成 `code=`，但兜底的
# 「未预期错误」是直接赋值给列的，`code=` 规则看不见——同告警一样按前缀扫字面量。
PARSE_ERROR_NAMESPACES = ("parse_error.",)


def backend_codes() -> tuple[set[str], set[str], set[str]]:
    """返回（业务错误 code，解析告警 code，合并原因 code）。"""
    errors: set[str] = set()
    warnings: set[str] = set()
    merges: set[str] = set()
    for path in sorted(BACKEND.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "code":
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    value = node.value.value
                    if value.startswith(WARNING_NAMESPACES):
                        warnings.add(value)
                    elif value.startswith(MERGE_NAMESPACES):
                        merges.add(value)
                    else:
                        errors.add(value)
                elif isinstance(node.value, ast.JoinedStr):
                    print(f"  ⚠️  {path.relative_to(ROOT)}: code 是拼出来的，扫不到——请写成字面量")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value.startswith(WARNING_NAMESPACES) and " " not in node.value:
                    warnings.add(node.value)
                elif node.value.startswith(PARSE_ERROR_NAMESPACES) and " " not in node.value:
                    errors.add(node.value)
            elif isinstance(node, ast.ClassDef) and path != BACKEND / "errors.py":
                # 类体里的 `code = "..."`：每次 raise 不写 code= 也带着它。2026-09-23 之前这里
                # 扫不到，于是 `RoutingError`（「任务尚未配置 provider」，新部署第一个撞上的
                # 错误）等三条从没有词条，英文界面显示中文。errors.py 里的是 HTTP 类别
                # （not_found / conflict …），按设计回落到后端那句中文，不算。
                for stmt in node.body:
                    if (isinstance(stmt, ast.Assign)
                            and any(isinstance(t, ast.Name) and t.id == "code" for t in stmt.targets)
                            and isinstance(stmt.value, ast.Constant)
                            and isinstance(stmt.value.value, str)):
                        errors.add(stmt.value.value)
    return errors, warnings, merges


def catalogue(name: str, namespace: str) -> dict[str, str]:
    data = json.loads((LOCALES / name).read_text())
    flat: dict[str, str] = {}

    def walk(node: dict, prefix: str) -> None:
        for key, value in node.items():
            full = f"{prefix}.{key}" if prefix else key
            walk(value, full) if isinstance(value, dict) else flat.update({full: value})

    walk(data.get(namespace, {}), "")
    return flat


def dotted_keys(name: str) -> list[str]:
    """词条的键里不许有点号。

    点号是 i18next 的 keySeparator。`errors` 下一旦同时有 `"control": {...}` 和
    `"control.merge_into_self": "..."`，查 `errors.control.merge_into_self` 会先
    走进嵌套的 control，找不到就判缺失，**不会回头试扁平键**——于是界面回落到
    后端的中文。2026-09-23 实测 62 条词条就这样在运行时查不到，而本脚本一直是绿的：
    下面的 `catalogue()` 把两种写法摊平成同一种再比，比的是一个与运行时不同的模型。
    """
    found: list[str] = []

    def walk(node: dict, path: list[str]) -> None:
        for key, value in node.items():
            if "." in key:
                found.append(".".join([*path, key]))
            if isinstance(value, dict):
                walk(value, [*path, key])

    walk(json.loads((LOCALES / name).read_text()), [])
    return found


def check(label: str, codes: set[str], namespace: str) -> list[str]:
    en = catalogue("en.json", namespace)
    problems = []
    for code in sorted(codes - set(en)):
        problems.append(f"{label}: 后端用了 {code}，en.json 里没有")
    for code in sorted(set(en) - codes):
        problems.append(f"{label}: en.json 有 {code}，后端已经不用了")
    print(f"  {label}: 后端 {len(codes)}，en {len(en)}")
    return problems



def _safe_parse(path: Path) -> ast.Module:
    import ast as _ast

    try:
        return _ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return _ast.parse("")


def check_every_error_carries_a_code() -> int:
    """业务错误必须带 code=，否则英文界面只能看到后端那句中文。

    这条是 2026-09-22 补的。此前的检查只校验「已有的 code 有没有词条」，对
    「根本没写 code」完全是瞎的——于是 143 条里有 89 条压根没进过词条表，
    迁移停在 57% 而没有任何信号。**只校验已登记项的检查，测不出未登记的缺口。**

    ValidationFailure 不在此列：它是 runner 内部的信号，reason 由模型输出决定，
    不可能有固定词条。
    """
    import ast

    # 不写死名单：名单会漏掉自定义子类。2026-09-22 第一版就是这么漏的——
    # `TooManyAttempts(AppError)`（登录限流）不在名单里，于是它没有 code
    # 这件事被这条检查一路放行，而这条检查本身就是为了防同类事故写的。
    # 所以先把「继承自 AppError 的类」全扫出来，再按这个集合找 raise。
    errors = {"AppError", "Conflict", "NotFound", "Forbidden", "Unauthorized"}
    for path in (ROOT / "backend" / "app").rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(
                isinstance(b, ast.Name) and b.id in errors for b in node.bases
            ):
                errors.add(node.name)
    class_level_code = {
        node.name
        for path in (ROOT / "backend" / "app").rglob("*.py")
        for node in ast.walk(_safe_parse(path))
        if isinstance(node, ast.ClassDef)
        and any(
            isinstance(stmt, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "code" for t in stmt.targets)
            for stmt in node.body
        )
    }
    missing: list[str] = []
    for path in sorted((ROOT / "backend" / "app").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id not in errors:
                continue
            if "code" in {kw.arg for kw in node.keywords}:
                continue
            if node.func.id in class_level_code:
                continue   # 类体里声明了 code = "..."，每次 raise 不必重复写
            rel = path.relative_to(ROOT)
            missing.append(f"  {rel}:{node.lineno}  {node.func.id} 没有 code=")
    print(f"  业务错误都带 code: 缺 {len(missing)} 处")
    if missing:
        print("\n❌ 这些 raise 没有 code=，英文界面只会看到后端那句中文：")
        print("\n".join(missing[:30]))
        return 1
    return 0


def main() -> int:
    errors, warnings, merges = backend_codes()
    problems = (
        check("业务错误", errors, "errors")
        + check("解析告警", warnings, "parseWarnings")
        + check("合并原因", merges, "mergeReasons")
    )
    for name in ("en.json",):
        dotted = dotted_keys(name)
        print(f"  {name} 里带点号的键: {len(dotted)}")
        problems += [f"{name}: 键 {key} 带点号，运行时按嵌套路径查，查不到它" for key in dotted]
    # 两条检查都要跑完再判成败：一次看到全部缺口，而不是修一条跑一次。
    uncoded = check_every_error_carries_a_code()
    if problems:
        print()
        for problem in problems:
            print(f"  ❌ {problem}")
        print(f"\n  共 {len(problems)} 处对不上")
    if problems or uncoded:
        return 1
    print("  ✅ code 与词条一一对上，且每个业务错误都带 code")
    return 0


if __name__ == "__main__":
    sys.exit(main())
