"""标准答案的两路自动标注：MiniMax 大模型逐段标注 + 确定性规则标注。

    set -a; . /home/box/.secrets/minimax_cn_key; set +a   # 只在 shell 里 source，不落盘
    python -m evals.real.label [doc_id ...]

输出 work/labels/<id>.llm.json 与 <id>.rule.json，每项 {line, level, number, heading}。
大模型回复按分段缓存在 work/llm/，重跑不重复计费；遇 429/5xx 按 1、2、4、10 分钟退避，
之后每 10 分钟重试，不放弃。
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from evals.real.extract import HERE, lines

WORK = HERE / "work"
# v2（2026-10-29）：从零重标，缓存与标注放在 work/v2/，与 v1 不共用任何文件。
VWORK = WORK / os.environ.get("GOLD_VERSION", "v2")
BASE = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1")
MODEL = os.environ.get("MINIMAX_MODEL", "MiniMax-M3")
CHUNK, OVERLAP = 220, 15

PROMPT = """你是制度文档结构标注员。下面是一份制度/法规/政策文档抽取出的文本行，每行前有行号 L<n>。
请找出所有**条款标题行**（文档的结构单元起点），按出现顺序逐行输出，格式严格为：
L<行号>|<层级>|<编号>|<标题>

什么算条款（结构单元）：
- 中文：第X编/第X章/第X节/第X条；没有「第X条」的公文体文档里的 一、 （一） 1. 标题。
- 英文：Part/Chapter/Section/Article/Regulation/Schedule/Annex 等单元；1. / 1.1 / 1.1.1 这类编号章节标题；
  政策模板里独占一行的短标题（如 Purpose、Scope、Policy、Enforcement）；英国法规里每条 regulation（"1.—(1) ..." 或 "12. ..."）。
什么不算：文档总标题、发文信息、目录(TOC)行、页眉页脚、表格内容、表单字段、修订记录；
「第X条」内部的列项（一）(1) 1. (a)，英文条文内部的段落号 (1) (2) (a)，项目符号列表，序言/鉴于条款(recitals) 的编号段落，正文里引用「第三条规定的…」。

字段规则：
- 层级：1 为最外层，子单元 +1。同一类单元层级必须全文一致（如所有「第X条」同级）。
- 编号：原文编号（如 第一章、第十二条、1.2、Article 5、PART 3、Schedule 1），没有编号留空。
- 标题：单元自己的标题文字，不含编号；「第一条 为了……」这种没有标题的条，标题写编号本身（第一条）；
  「第一章 总则」标题为 总则；「Article 5」下一行是标题时取那一行；英国法规条文的标题是它上方的旁注标题行（如 Citation and commencement），没有就写编号本身。
- 标题跨多行时合并成一行写出，行号取第一行。
不要输出任何解释，只输出这些行；本段没有条款标题就输出 NONE。
{context}
文本：
{body}"""


def _call(prompt: str) -> str:
    key = os.environ["MINIMAX_CN_API_KEY"]
    body = json.dumps({"model": MODEL, "temperature": 0.1, "max_tokens": 16000,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    waits = [60, 120, 240, 600]
    attempt = 0
    while True:
        req = urllib.request.Request(f"{BASE}/chat/completions", data=body, headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = json.loads(resp.read())
            base = (data.get("base_resp") or {}).get("status_code", 0)
            if base in (1002, 1039):  # MiniMax 的限流码
                raise urllib.error.HTTPError(req.full_url, 429, "rate", None, None)
            if "choices" not in data:
                raise RuntimeError(f"bad response: {str(data)[:200]}")
            text = data["choices"][0]["message"]["content"] or ""
            if not text.strip():
                raise RuntimeError("empty completion")
            answer = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
            if not answer or answer.startswith("<think>"):
                raise RuntimeError("no answer after reasoning")
            return answer
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, RuntimeError, OSError) as exc:
            code = getattr(exc, "code", None)
            if code in (400, 401, 403, 404):
                raise
            wait = waits[min(attempt, len(waits) - 1)]
            print(f"  ! {type(exc).__name__} {code}; sleep {wait}s", flush=True)
            time.sleep(wait)
            attempt += 1


_ROW = re.compile(r"^L(\d+)\s*\|\s*(\d+)\s*\|([^|]*)\|(.*)$")


def llm_label(doc_id: str, ls: list[str]) -> list[dict]:
    cache = VWORK / "llm" / doc_id
    cache.mkdir(parents=True, exist_ok=True)
    found: dict[int, dict] = {}
    start = 0
    while start < len(ls):
        end = min(len(ls), start + CHUNK)
        f = cache / f"{start:05d}.txt"
        if not f.exists() or not f.read_text(encoding="utf-8").strip():
            recent = [found[k] for k in sorted(found)][-10:]
            ctx = ""
            if recent:
                ctx = "\n此前已标出的最后几个条款（保持层级一致）：\n" + "\n".join(
                    f"L{r['line']}|{r['level']}|{r['number']}|{r['heading']}" for r in recent)
            body = "\n".join(f"L{i}: {ls[i]}" for i in range(start, end))
            f.write_text(_call(PROMPT.format(context=ctx, body=body)), encoding="utf-8")
        for raw in f.read_text(encoding="utf-8").splitlines():
            m = _ROW.match(raw.strip())
            if m and start <= int(m[1]) < end:
                found[int(m[1])] = {"line": int(m[1]), "level": int(m[2]),
                                    "number": m[3].strip(), "heading": m[4].strip()}
        if end == len(ls):
            break
        start = end - OVERLAP
    return [found[k] for k in sorted(found)]


# ---------------- 确定性规则 ----------------
_CN = r"[〇零一二两三四五六七八九十百千\d]+"
_ZH = [("bian", rf"^第\s*({_CN})\s*编\s*(.*)$"), ("zhang", rf"^第\s*({_CN})\s*章\s*(.*)$"),
       ("jie", rf"^第\s*({_CN})\s*节\s*(.*)$"), ("tiao", rf"^第\s*({_CN})\s*条\s*(.*)$")]
_ZH_OUT = [("yi", r"^([一二三四五六七八九十]+)[、]\s*(.*)$"), ("paren", r"^[（(]([一二三四五六七八九十]+)[）)]\s*(.*)$")]
_EN_UNIT = re.compile(r"^(PART|Part|CHAPTER|Chapter|SECTION|Section|ARTICLE|Article|SCHEDULE|Schedule|ANNEX|Annex)\s+([0-9IVXLC]+[A-Z]?)\b\.?\s*(.*)$")
_EN_NUM = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+([A-Z][^.;:]{1,80})$")


_DIGIT = {c: i for i, c in enumerate("〇一二三四五六七八九")} | {"零": 0, "两": 2}


def cn_to_int(text: str) -> int | None:
    """本地实现，不引用项目代码（标准答案与解析器保持独立）。"""
    if text.isdigit():
        return int(text)
    total, section, digit = 0, 0, 0
    for ch in text:
        if ch in _DIGIT:
            digit = _DIGIT[ch]
        elif ch in "十百千":
            unit = {"十": 10, "百": 100, "千": 1000}[ch]
            section += (digit or 1) * unit
            digit = 0
        else:
            return None
    return total + section + digit


def rule_label(ls: list[str]) -> list[dict]:
    out = []
    has_tiao = sum(bool(re.match(_ZH[3][1], s)) for s in ls) >= 2
    pats = _ZH if has_tiao else _ZH_OUT
    kinds_seen: list[str] = []
    prev_tiao = 0
    for i, s in enumerate(ls):
        for kind, pat in pats:
            m = re.match(pat, s)
            if not m:
                continue
            if kind == "tiao":
                n = cn_to_int(m[1]) or 0
                if n != prev_tiao + 1:
                    break
                prev_tiao = n
            if kind not in kinds_seen:
                kinds_seen.append(kind)
            num = s[: m.start(2)].strip() if m[2] else s.strip()
            title = m[2].strip() if kind != "tiao" else num
            out.append({"line": i, "kind": kind, "number": re.sub(r"\s+", "", num), "heading": title or num})
            break
        else:
            m = _EN_UNIT.match(s)
            if m:
                out.append({"line": i, "kind": m[1].lower(), "number": f"{m[1]} {m[2]}", "heading": m[3] or f"{m[1]} {m[2]}"})
                continue
            m = _EN_NUM.match(s)
            if m and len(s) < 90:
                out.append({"line": i, "kind": f"d{m[1].count('.')}", "number": m[1], "heading": m[2].strip()})
    order = ["bian", "zhang", "jie", "tiao", "yi", "paren", "part", "chapter", "section", "article",
             "schedule", "annex", "d0", "d1", "d2", "d3"]
    present = [k for k in order if any(o["kind"] == k for o in out)]
    for o in out:
        o["level"] = present.index(o["kind"]) + 1
    return out


def manifest() -> list[dict]:
    with open(HERE / "manifest.csv", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main(ids: list[str]) -> None:
    rows = [r for r in manifest() if not ids or r["id"] in ids]
    (VWORK / "labels").mkdir(parents=True, exist_ok=True)

    def one(row):
        ls = lines(row)
        (VWORK / "labels" / f"{row['id']}.rule.json").write_text(
            json.dumps(rule_label(ls), ensure_ascii=False, indent=0), encoding="utf-8")
        out = VWORK / "labels" / f"{row['id']}.llm.json"
        if not out.exists():
            out.write_text(json.dumps(llm_label(row["id"], ls), ensure_ascii=False, indent=0), encoding="utf-8")
        print("done", row["id"], len(ls), "lines", flush=True)

    with ThreadPoolExecutor(4) as pool:
        list(pool.map(one, rows))


if __name__ == "__main__":
    main(sys.argv[1:])
