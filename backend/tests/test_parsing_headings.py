"""仲裁：一份文档里带编号的那些行，到底是章节标题还是正文列表项。

这个判断只看文档内部结构；带点的单段编号不能全局当作列表项。
"""

import pytest

from app.parsing.headings import (
    PARENTHOOD_THRESHOLD,
    acts_as_headings,
    choose_heading_set,
    consistency_score,
    continues_the_heading,
    parenthood_ratio,
)


def test_a_clean_hierarchy_scores_high():
    assert consistency_score(["1", "1.1", "1.2", "2", "3", "3.1"]) > 0.9


def test_document_a_outline_scores_high():
    """层级连续且编号唯一时应得到高分。"""
    labels = ["1", "1.1", "1.2", "2", "3", "3.1", "3.2", "3.3", "3.4", "3.5", "3.6",
              "4", "5", "6", "7", "7.1", "7.2"]
    assert consistency_score(labels) > 0.9


def test_scattered_repeats_score_low():
    """文档C 的正文列表项：1. 2. 3. 反复出现，没有任何子号挂上去。"""
    assert consistency_score(["1", "2", "3", "1", "2", "1", "2", "3", "4"]) < 0.5


def test_orphans_drag_the_score_down():
    """子号找不到父亲，说明这批编号不是一棵树。"""
    assert consistency_score(["3.1", "4.2", "7.5"]) < 0.5


def test_going_backwards_costs():
    assert consistency_score(["1", "2", "3"]) > consistency_score(["1", "5", "2", "9"])


def test_an_empty_set_scores_zero():
    assert consistency_score([]) == 0.0


def test_the_arbiter_prefers_the_self_consistent_reading():
    as_headings = ["1", "1.1", "1.2", "2", "3", "3.1"]
    as_list_items = ["1", "2", "3", "1", "2", "1", "2", "3", "4"]
    assert choose_heading_set([as_list_items, as_headings]) == as_headings


def test_between_two_equally_consistent_readings_the_one_that_explains_more_wins():
    """一条标题的集合天然自洽，但它什么也没解释。"""
    tiny = ["1"]
    full = ["1", "1.1", "1.2", "2", "3", "3.1"]
    assert choose_heading_set([tiny, full]) == full


def test_the_arbiter_returns_empty_when_nothing_is_consistent():
    assert choose_heading_set([["3.1", "4.2", "7.5"], []]) == []


@pytest.mark.parametrize("labels", [["1"], ["1", "2"], ["1", "1.1"]])
def test_small_but_clean_sets_are_not_punished_for_being_small(labels):
    """短文档就是条款少，不该因此被判不自洽。"""
    assert consistency_score(labels) > 0.9


# ── `N.` 是标题还是列表项 ────────────────────────────────────
# 上面那个通用自洽度分在真实文档上不管用（救回 3 份、打坏 3 份），
# 换成「这些 `N.` 有没有子号挂上去」。数字取自 15 份 PDF 的实测。


def test_numbers_that_never_have_children_are_list_items():
    """文档C 的 147 个 `N.` 里一个子号都没有——操作步骤不会有下级。"""
    labels = [str(n) for n in range(1, 10)] * 3        # 1..9 反复出现，无子号
    assert parenthood_ratio(labels) == 0.0
    assert acts_as_headings(labels) is False


def test_numbers_that_all_have_children_are_headings():
    """文档A：7 个顶层编号，7 个都带子号。"""
    labels = ["1", "1.1", "2", "2.1", "3", "3.1", "4", "4.1", "5", "5.1", "6", "6.1", "7", "7.1"]
    assert parenthood_ratio(labels) == 1.0
    assert acts_as_headings(labels) is True


def test_a_minority_with_children_still_reads_as_list_items():
    """文档D：234 个单段编号里只有 5 个带子号，真正的标题在别处。"""
    # 真实文档里这 234 个编号是**带重复**的：正文列表在每一节里各起一遍。
    labels = [str(n) for n in range(1, 21)] + ["1", "2", "3"] + ["1.1", "2.1"]
    assert parenthood_ratio(labels) == 0.1
    assert acts_as_headings(labels) is False


def test_a_clear_majority_with_children_reads_as_headings():
    """文档E 39% / 文档J 43% / 文档G 50% / 文档B 86% 都在这一侧。"""
    labels = ["1", "1.1", "2", "2.1", "3", "3.1", "4", "5"]   # 5 个里 3 个有子号
    assert parenthood_ratio(labels) > PARENTHOOD_THRESHOLD
    assert acts_as_headings(labels) is True


def test_no_single_part_numbers_at_all_is_not_headings():
    assert parenthood_ratio(["3.1", "4.2"]) == 0.0
    assert acts_as_headings([]) is False


def test_a_flat_document_that_numbers_its_sections_once_reads_as_headings():
    """委员会章程：1. Purpose / 2. Composition / 3. Reporting，没有子节。
    靠"有没有子号"判不出来——它本来就没有。但它只升一次、不重复。"""
    assert acts_as_headings(["1", "2", "3", "4", "5"]) is True


def test_list_items_that_restart_in_each_section_are_still_list_items():
    """正文列表在每一节里各起一遍，这是它和扁平章节的区别。"""
    assert acts_as_headings(["1", "2", "3", "1", "2", "1", "2", "3"]) is False


def test_two_numbers_are_too_few_to_call_it_a_run():
    assert acts_as_headings(["1", "2"]) is False


# ── 标题装着半句话 ────────────────────────────────────────────
# 段落级编号的文档里，解析器把编号那一行当标题、剩下的当正文，一句话被劈成两半：
#
#   heading = "Under this standard, teams are required to keep a written technology"
#   text    = "and resilience plan proportionate to their size…"
#
# 代价不只是难看：`heading_path` 由它拼成，而 `rebuild_chunks` 会把 heading_path
# 当上下文前缀拼进**每一个 chunk**——半句话的前缀同时污染向量和全文检索。
# 确认队列里审核者看到的也是半句话。
#
# 判据：正文以小写字母开头，可能是标题那一行的续句。
# 按**每一条**判而不是按文档判——文档A 的 `1 Introduction`、`1.1 Background`
# 是真标题（正文为空，不受影响），只有 `1.1.1` 那一级是段落。


def test_a_body_starting_lower_case_means_the_heading_is_half_a_sentence():
    assert continues_the_heading("and resilience plan proportionate to their size…")
    assert continues_the_heading("the register with a view to raising their maturity")


def test_a_real_section_body_starts_a_new_sentence():
    assert not continues_the_heading("The purpose of this procedure is to establish…")
    assert not continues_the_heading("IT Division drafts it.")
    assert not continues_the_heading("")
