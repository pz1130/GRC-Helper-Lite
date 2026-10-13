#!/usr/bin/env python3
"""拦两类只在注释与 docstring 里出现的东西：机构标识，和整段英文。

**为什么只扫注释和 docstring。** 两次事故都发生在那里：解析层的调参注释按机构名
引用语料（`实测 06_XXX：56 个章节…`），测试的 docstring 拿真实文档举例。代码本身
反而是干净的——夹具里的句子是编的，常量名里的 `201_CREATED` 不是机构。扫全文会被
这些淹没，于是没人看结论。

**为什么不能靠词表。** 2026-09-22 的擦除用一份手写的机构名词表，替换、历史重写、
事后残留扫描三道全用同一份，于是词表里没有的那几家被三道一致地放过，还得出了
"零残留"的结论——三道检查共享同一个盲区时，互相印证等于什么都没证。所以这里
**一个机构名都不写**：规则全是形状（`NN_Name` 的语料编号、以 bank 结尾的专名、
监管类通名）。写成词表的那一刻，这个脚本自己就成了泄漏源。

英文那条对的是语言约定：仓库里面的人看的文字一律中文。它拦不住"写得烂的中文"，
只拦"整段英文"——后者是实际发生过的漂移（matrix/ 那一族整个模块没被统一到）。
"""

import ast
import re
import subprocess
import sys
import tokenize
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 形状：语料编号（形如 `06_Example_Corp` / `03_Samplebank`）。常量名与测试名里的 `201_CREATED`
# 这类不会进注释，所以不必在这里排除——真混进来了也该看一眼。
CORPUS_ID = re.compile(r"\d{2}[a-z]?_[A-Z][A-Za-z_]{2,}")

# 第二道网：**不点名**的机构形态。写成词表的那一刻，这个脚本自己就成了泄漏源——
# 2026-09-22 第一版就是这样：为了检测而把六家机构名写进仓库。所以这里只留形态：
#   · 以 bank 结尾的专名（连写形式，`\bBank\b` 抓不到）
#   · 监管/机构类通名（「某某当局」「某某监管手册」这一类）
ORG_SHAPE = re.compile(
    r"[A-Z][a-z]{2,}[Bb]ank\b|"
    r"\bBanco\b|\bBancorp\b|"
    r"Monetary Authority|Supervisory Policy|Central Bank|"
    r"\bBank of [A-Z][a-z]+|\bAuthority of [A-Z][a-z]+"
)
# 编出来的示例机构不算。真机构名不会长这样。
ORG_OK = re.compile(r"Acme|示例|Example|远洋")
BANK_WORD = re.compile(r"\bBank\b")

CJK = re.compile(r"[一-鿿]")
# 整段英文：连着 8 个英文词、且这一行没有中文。URL 与 `代码` 不算。
# 词尾允许跟标点：第一版用 `[\w'-]*\s+` 连词，于是 `approved;` 里的分号把词串
# 断成两截，一条 10 个词的英文注释被算成 5+5，整条漏过。它是靠人读代码发现的，
# 不是靠这条检查——所以这里宁可放宽，误报比漏报便宜。
ENGLISH_RUN = re.compile(r"(?:[A-Za-z][\w'-]*[,;:.()]*\s+){7}[A-Za-z][\w'-]*")

# alembic 的 docstring 是迁移标题，会打进 `alembic upgrade` 的输出，按英文写；
# prompts.py 里的字符串是**发给模型的提示词**，不是给人读的注释。两者都不归
# 语言约定管——那条约定管的是"仓库里面的人"看的文字。
SKIP_DIRS = (
    "backend/seeds/",
    "frontend/src/locales/",
    "node_modules/",
    "backend/alembic/versions/",
)
# 检测器必然包含自己的模式与举例，扫自己只会永远红。
SKIP_NAMES = ("prompts.py", "check_source_hygiene.py")
SKIP_SUFFIX = (".json", ".csv", ".lock", ".svg", ".png", ".ico", ".md")


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout
    return [
        f for f in out.split()
        if not f.startswith(SKIP_DIRS)
        and not f.endswith(SKIP_SUFFIX)
        and not f.endswith(SKIP_NAMES)
    ]


def py_comments(text: str):
    """(行号, 文字)：`#` 注释，加上模块/类/函数的 docstring。

    只取 docstring 而不是全部字符串字面量：夹具里的句子、提示词、错误消息
    都是字符串，把它们算进来会让结论淹没在误报里，没人再看。
    """
    block: list[str] = []
    start = 0
    last = -2
    try:
        for tok in tokenize.tokenize(BytesIO(text.encode()).readline):
            if tok.type != tokenize.COMMENT:
                continue
            if tok.start[0] != last + 1 and block:
                yield start, "\n".join(block)
                block = []
            if not block:
                start = tok.start[0]
            block.append(tok.string)
            last = tok.start[0]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    if block:
        yield start, "\n".join(block)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        doc = ast.get_docstring(node, clean=False)
        if not doc:
            continue
        yield (node.body[0].lineno if node.body else 1), doc


TS_COMMENT = re.compile(r"^\s*(//|/\*|\*)")


def ts_comments(text: str):
    block: list[str] = []
    start = last = 0
    for no, line in enumerate(text.splitlines(), 1):
        if TS_COMMENT.match(line):
            if no != last + 1 and block:
                yield start, "\n".join(block)
                block = []
            if not block:
                start = no
            block.append(line)
            last = no
        elif block:
            yield start, "\n".join(block)
            block = []
            last = 0
    if block:
        yield start, "\n".join(block)


def main() -> int:
    findings: list[str] = []
    english: list[str] = []
    for rel in tracked():
        path = ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        reader = py_comments if rel.endswith(".py") else ts_comments
        if not rel.endswith((".py", ".ts", ".tsx")):
            continue
        for no, block in reader(text):
            line = block.replace("\n", " ")
            if CORPUS_ID.search(line):
                findings.append(f"  {rel}:{no}  语料编号  {line.strip()[:70]}")
            elif ORG_SHAPE.search(line) and not ORG_OK.search(line):
                findings.append(f"  {rel}:{no}  机构形态  {line.strip()[:70]}")
            elif BANK_WORD.search(line) and not ORG_OK.search(line):
                findings.append(f"  {rel}:{no}  疑似机构  {line.strip()[:70]}")
            # 整块都没有中文才算——中文段落里夹一行英文示例是正常写法。
            if not CJK.search(block) and ENGLISH_RUN.search(line):
                english.append(f"  {rel}:{no}  {line.strip()[:70]}")

    print(f"  机构标识: {len(findings)} 处")
    print(f"  整段英文注释: {len(english)} 处")
    if findings:
        print("\n❌ 注释里点到了机构（历史证明这是泄漏最常发生的地方）：")
        print("\n".join(findings[:40]))
    if english:
        print("\n❌ 整段英文注释（语言约定：仓库里面的人看的一律中文）：")
        print("\n".join(english[:40]))
    if not findings and not english:
        print("  ✅ 注释与 docstring 里没有机构标识，也没有整段英文")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
