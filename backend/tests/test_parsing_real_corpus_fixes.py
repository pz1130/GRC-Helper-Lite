"""真实制度语料（evals/real）暴露问题的回归测试。"""
import importlib.util
import sys
from pathlib import Path

from app.parsing.contract import ClauseNode
from app.parsing.pdf_parser import _zero_point_chapters
from app.parsing.restructure import nest_chapters

_spec = importlib.util.spec_from_file_location(
    "seg_score", Path(__file__).resolve().parents[1] / "evals" / "segmentation" / "score.py")
seg = importlib.util.module_from_spec(_spec)
sys.modules["seg_score"] = seg
_spec.loader.exec_module(seg)


def _n(text, number=None, heading="h"):
    return ClauseNode(heading=heading, text=text, level=1, number=number)


def test_zero_point_chapters_become_top_level_numbers():
    out = _zero_point_chapters(["1.0 Purpose", "body", "2.0 Scope", "3.0 Policy"])
    assert out[0] == "1. Purpose" and out[2] == "2. Scope" and out[1] == "body"


def test_single_zero_point_line_is_left_alone():
    assert _zero_point_chapters(["1.0 Purpose", "body"]) == ["1.0 Purpose", "body"]


def test_chapter_references_inside_sentences_do_not_nest():
    # 章标记落在上一节点正文末尾；句中引用（「Chapter VII.」「Chapter IX (」）不能当章。
    nodes = [_n("intro\nCHAPTER I\nGeneral"), _n("as laid down in Chapter VII.", "1"),
             _n("x\nCHAPTER II\nPrinciples", "2"), _n("see Chapter IX (cooperation)", "3")]
    out = nest_chapters(nodes)
    chapters = [o for o in out if o.number and o.number.startswith("Chapter")]
    assert [c.number for c in chapters] == ["Chapter I", "Chapter II"]
    assert [c.number for c in chapters[0].children] == ["1", "2"]
    assert [c.number for c in chapters[1].children] == ["3"]


def test_part_then_chapter_nesting():
    nodes = [_n("intro\nPART 1\nGeneral"), _n("x", "1"), _n("y\nPART 2\nDue diligence\nCHAPTER 1\nGeneral", "2"),
             _n("z\nCHAPTER 2\nEnhanced", "3"), _n("w", "4")]
    out = nest_chapters(nodes)
    parts = [o for o in out if o.number and o.number.startswith("Part")]
    assert [p.number for p in parts] == ["Part 1", "Part 2"]
    assert [c.number for c in parts[1].children] == ["Chapter 1", "Chapter 2"]


def test_scorer_matches_numbered_paragraph_by_number():
    gold = {"heading": "Employees must report incidents", "number": "4.1.1"}
    assert seg._heading_matches(gold, ClauseNode(heading="4.1.1", text="", level=3, number="4.1.1"))
    assert not seg._heading_matches(gold, ClauseNode(heading="4.1.2", text="", level=3, number="4.1.2"))


def test_gold_nests_articles_under_sections():
    from evals.real.gold import nest_articles_in_sections
    labels = [{"number": "第三章", "level": 1}, {"number": "第一节", "level": 2},
              {"number": "第十二条", "level": 2}, {"number": "第四章", "level": 1}, {"number": "第十三条", "level": 2}]
    assert [x["level"] for x in nest_articles_in_sections(labels)] == [1, 2, 3, 1, 2]
