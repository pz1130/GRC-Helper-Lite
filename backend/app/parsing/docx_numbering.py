"""还原 Word 自动编号的标题号。

**为什么需要**：Word 的标题编号不在文字里。`w:t` 里只有 "Introduction"，
前面那个 "1" 是渲染时按 numbering.xml 的规则画上去的。PDF 解析读的是渲染后的
文本，编号已经在纸上；docx 解析读 `w:t`，就什么都拿不到——实测一份真实文档的
32 条条款一个编号都没有，`citation_label` 只能退化成标题路径。

**为什么不读文档自带的目录**：目录确实写着 "1Introduction"，十几行就能抠出来。
但目录可能过期、缺失、或只覆盖前几级——那种碰巧能用的做法在解析层会反复咬人。

**这里做的**：styles.xml → numPr（`numId` 沿 `basedOn` 链继承）→ numbering.xml
的 `w:num` → `w:abstractNum`（处理 `numStyleLink` 间接引用与 `w:lvlOverride`）
→ 按文档顺序跑计数器状态机 → 用 `lvlText` 模板渲染。

不认识的编号格式**返回 None 而不是猜**：一个错的条款号比没有条款号更糟，
审计引用会指向不存在的位置。
"""

from dataclasses import dataclass, field

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

_ROMAN = [
    (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
    (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
]


def _val(element, tag: str) -> str | None:
    if element is None:
        return None
    child = element.find(f"{W}{tag}")
    return child.get(f"{W}val") if child is not None else None


def _letters(n: int) -> str:
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("a") + rem) + out
    return out


def _roman(n: int) -> str:
    out = ""
    for value, glyph in _ROMAN:
        while n >= value:
            out += glyph
            n -= value
    return out


_CN_DIGITS = "零一二三四五六七八九"


def _chinese(n: int) -> str | None:
    """1→一、10→十、12→十二、25→二十五、105→一百零五（1–999）。"""
    if not 0 < n < 1000:
        return None
    hundreds, rest = divmod(n, 100)
    tens, ones = divmod(rest, 10)
    out = f"{_CN_DIGITS[hundreds]}百" if hundreds else ""
    if tens:
        out += ("" if tens == 1 and not hundreds else _CN_DIGITS[tens]) + "十"
    elif hundreds and ones:
        out += "零"
    if ones:
        out += _CN_DIGITS[ones]
    return out


def format_counter(value: int, fmt: str) -> str | None:
    """不认识的格式返回 None——错的条款号比没有更糟。"""
    if fmt in ("chineseCounting", "chineseCountingThousand", "japaneseCounting",
               "taiwaneseCounting", "taiwaneseCountingThousand"):
        return _chinese(value)
    if fmt in ("decimalFullWidth", "decimalFullWidth2", "decimalEnclosedParen"):
        return str(value) if fmt != "decimalEnclosedParen" else f"({value})"
    if fmt == "decimal":
        return str(value)
    if fmt == "lowerLetter":
        return _letters(value)
    if fmt == "upperLetter":
        return _letters(value).upper()
    if fmt == "lowerRoman":
        return _roman(value)
    if fmt == "upperRoman":
        return _roman(value).upper()
    return None


@dataclass
class Level:
    start: int = 1
    fmt: str = "decimal"
    text: str = ""
    is_legal: bool = False


@dataclass
class _Abstract:
    levels: dict[int, Level] = field(default_factory=dict)
    num_style_link: str | None = None
    style_link: str | None = None


class HeadingNumbering:
    """一份文档的编号状态机。按文档顺序对每个编号段落调用 `number_for` 一次。"""

    def __init__(self, document) -> None:
        self._abstracts: dict[str, _Abstract] = {}
        self._num_to_abstract: dict[str, str] = {}
        self._overrides: dict[str, dict[int, Level]] = {}
        self._style_num: dict[str, str] = {}      # styleId → numId（styleLink 反查用）
        self._style_pr: dict[str, tuple[str | None, int | None, str | None]] = {}
        self._counters: dict[str, dict[int, int]] = {}
        self._load_numbering(document)
        self._load_styles(document)

    # ---- 载入 ----

    def _load_numbering(self, document) -> None:
        try:
            part = document.part.numbering_part
        except (KeyError, AttributeError, ValueError, NotImplementedError):
            return
        root = part.element
        for node in root.findall(f"{W}abstractNum"):
            abstract = _Abstract(
                num_style_link=_val(node, "numStyleLink"),
                style_link=_val(node, "styleLink"),
            )
            for lvl in node.findall(f"{W}lvl"):
                abstract.levels[int(lvl.get(f"{W}ilvl", "0"))] = Level(
                    start=int(_val(lvl, "start") or 1),
                    fmt=_val(lvl, "numFmt") or "decimal",
                    text=_val(lvl, "lvlText") or "",
                    is_legal=lvl.find(f"{W}isLgl") is not None,
                )
            self._abstracts[node.get(f"{W}abstractNumId")] = abstract

        for node in root.findall(f"{W}num"):
            num_id = node.get(f"{W}numId")
            abstract_id = _val(node, "abstractNumId")
            if abstract_id is None:
                continue
            self._num_to_abstract[num_id] = abstract_id
            for override in node.findall(f"{W}lvlOverride"):
                lvl = override.find(f"{W}lvl")
                if lvl is None:
                    continue
                self._overrides.setdefault(num_id, {})[int(override.get(f"{W}ilvl", "0"))] = Level(
                    start=int(_val(lvl, "start") or 1),
                    fmt=_val(lvl, "numFmt") or "decimal",
                    text=_val(lvl, "lvlText") or "",
                    is_legal=lvl.find(f"{W}isLgl") is not None,
                )

    def _load_styles(self, document) -> None:
        try:
            root = document.styles.element
        except AttributeError:
            return
        for style in root.findall(f"{W}style"):
            style_id = style.get(f"{W}styleId")
            ppr = style.find(f"{W}pPr")
            numpr = ppr.find(f"{W}numPr") if ppr is not None else None
            num_id = _val(numpr, "numId")
            ilvl = _val(numpr, "ilvl")
            self._style_pr[style_id] = (
                num_id, int(ilvl) if ilvl is not None else None, _val(style, "basedOn"),
            )
            if num_id is not None:
                self._style_num[style_id] = num_id

    # ---- 解析 ----

    def _from_style(self, style_id: str | None) -> tuple[str | None, int | None]:
        """沿 basedOn 链找 numId 与 ilvl。

        实测一份真实文档：Heading1 自己只有 ilvl=0、没有 numId，numId 要从
        basedOn 的 Heading2 继承。就近声明的值优先。
        """
        num_id: str | None = None
        ilvl: int | None = None
        seen: set[str] = set()
        current = style_id
        while current and current not in seen:
            seen.add(current)
            found = self._style_pr.get(current)
            if found is None:
                break
            style_num, style_ilvl, based_on = found
            if num_id is None:
                num_id = style_num
            if ilvl is None:
                ilvl = style_ilvl
            if num_id is not None and ilvl is not None:
                break
            current = based_on
        return num_id, ilvl

    def _resolve(self, num_id: str, depth: int = 0) -> str | None:
        """numId → abstractNumId，跟随 numStyleLink 的间接引用。"""
        if depth > 4:
            return None
        abstract_id = self._num_to_abstract.get(num_id)
        if abstract_id is None:
            return None
        abstract = self._abstracts.get(abstract_id)
        if abstract is not None and abstract.num_style_link:
            linked = self._style_num.get(abstract.num_style_link)
            if linked and linked != num_id:
                return self._resolve(linked, depth + 1)
        return abstract_id

    def _level(self, num_id: str, abstract_id: str, ilvl: int) -> Level | None:
        override = self._overrides.get(num_id, {}).get(ilvl)
        if override is not None:
            return override
        abstract = self._abstracts.get(abstract_id)
        return abstract.levels.get(ilvl) if abstract else None

    # ---- 状态机 ----

    def number_for(self, paragraph) -> str | None:
        """推进计数器并返回该段落的编号；无编号返回 None。

        必须按文档顺序对每个候选段落恰好调用一次——它有副作用。
        """
        ppr = paragraph._p.find(f"{W}pPr")
        numpr = ppr.find(f"{W}numPr") if ppr is not None else None
        num_id = _val(numpr, "numId")
        ilvl_raw = _val(numpr, "ilvl")
        ilvl = int(ilvl_raw) if ilvl_raw is not None else None

        if num_id is None or ilvl is None:
            style_num, style_ilvl = self._from_style(
                paragraph.style.style_id if paragraph.style is not None else None
            )
            num_id = num_id or style_num
            ilvl = ilvl if ilvl is not None else style_ilvl
        if num_id is None or ilvl is None or num_id == "0":
            return None

        abstract_id = self._resolve(num_id)
        if abstract_id is None:
            return None
        level = self._level(num_id, abstract_id, ilvl)
        if level is None or level.fmt == "none":
            return None

        # 计数器按 abstractNum 共享：同一编号定义被多个 numId 引用时，
        # Word 也是接着数的。
        counters = self._counters.setdefault(abstract_id, {})
        counters[ilvl] = counters.get(ilvl, self._start(abstract_id, num_id, ilvl) - 1) + 1
        for deeper in [key for key in counters if key > ilvl]:
            del counters[deeper]

        return self._render(level, counters, abstract_id, num_id, ilvl)

    def is_bullet(self, paragraph) -> bool:
        """段落是否挂在项目符号列表上（不推进计数器）。"""
        ppr = paragraph._p.find(f"{W}pPr")
        numpr = ppr.find(f"{W}numPr") if ppr is not None else None
        num_id = _val(numpr, "numId")
        ilvl_raw = _val(numpr, "ilvl")
        ilvl = int(ilvl_raw) if ilvl_raw is not None else None
        if num_id is None or ilvl is None:
            style_num, style_ilvl = self._from_style(
                paragraph.style.style_id if paragraph.style is not None else None
            )
            num_id = num_id or style_num
            # 样式里只写 numId 不写 ilvl 时，Word 按第 0 级画。
            ilvl = ilvl if ilvl is not None else (style_ilvl or 0)
        if num_id is None or num_id == "0":
            return False
        abstract_id = self._resolve(num_id)
        level = self._level(num_id, abstract_id, ilvl) if abstract_id is not None else None
        return level is not None and level.fmt == "bullet"

    def _start(self, abstract_id: str, num_id: str, ilvl: int) -> int:
        level = self._level(num_id, abstract_id, ilvl)
        return level.start if level else 1

    def _render(
        self, level: Level, counters: dict[int, int], abstract_id: str,
        num_id: str, ilvl: int,
    ) -> str | None:
        out = level.text
        for position in range(ilvl + 1):
            token = f"%{position + 1}"
            if token not in out:
                continue
            value = counters.get(position)
            if value is None:
                value = self._start(abstract_id, num_id, position)
            source = self._level(num_id, abstract_id, position)
            # isLgl 强制把所有层级渲染成十进制，这是 Word 的「法律编号」模式。
            fmt = "decimal" if level.is_legal else (source.fmt if source else "decimal")
            rendered = format_counter(value, fmt)
            if rendered is None:
                return None
            out = out.replace(token, rendered)
        # `lvlText="%1."` 画出来是「1.」，那个点是排版，不是编号的一部分——
        # 留着的话 `1.1` 接不上 `1.`，条款号也与正文里的引用写法对不上。
        return out.strip().rstrip(".") or None
