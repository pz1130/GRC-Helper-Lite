import re

import pytest
from sqlalchemy import select

from app.llm.models import RedactionRule, RulesetName
from app.llm.redaction import RedactionEngine


def _rule(pattern: str, prefix: str, *, pattern_type: str = "regex", order: int = 10):
    return RedactionRule(
        ruleset=RulesetName.GENERATION,
        pattern_type=pattern_type,
        pattern=pattern,
        replacement_prefix=prefix,
        enabled=True,
        order_index=order,
    )


IP_RULE = _rule(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "IP")
DICT_RULE = _rule("示例集团\n集团", "ORG", pattern_type="dictionary", order=5)
# 形如 SVC_88 的系统账号号——企业里极常见，而且长得和占位符内部一模一样
ACCT_RULE = _rule(r"\b[A-Z]{2,}_\d+\b", "ACCT", order=20)


def test_redact_replaces_match_with_placeholder():
    engine = RedactionEngine([IP_RULE])
    result = engine.redact("跳板机地址是 10.20.30.40，请勿外传。")
    assert "10.20.30.40" not in result.text
    assert "[[IP_1]]" in result.text


def test_roundtrip_restores_exactly():
    engine = RedactionEngine([IP_RULE])
    original = "主机 10.0.0.1 与备机 10.0.0.2 双活。"
    result = engine.redact(original)
    assert engine.restore(result.text, result.mapping) == original


def test_same_value_maps_to_same_placeholder():
    """同一实体必须同一代号，否则模型会以为是两个不同实体。"""
    engine = RedactionEngine([IP_RULE])
    result = engine.redact("10.0.0.1 与 10.0.0.1 是同一台机器")
    assert result.text.count("[[IP_1]]") == 2
    assert len(result.mapping) == 1


def test_placeholder_numbering_does_not_collide_on_prefix():
    """IP_1 与 IP_10 不能互相污染——方括号定界保证这一点。"""
    engine = RedactionEngine([IP_RULE])
    text = " ".join(f"10.0.0.{i}" for i in range(1, 12))
    result = engine.redact(text)
    assert engine.restore(result.text, result.mapping) == text


def test_dictionary_rule_prefers_longer_term():
    engine = RedactionEngine([DICT_RULE])
    result = engine.redact("示例集团的制度")
    assert result.text == "[[ORG_1]]的制度"
    assert result.mapping["[[ORG_1]]"] == "示例集团"


def test_disabled_rule_is_ignored():
    rule = _rule(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "IP")
    rule.enabled = False
    engine = RedactionEngine([rule])
    assert engine.redact("10.0.0.1").text == "10.0.0.1"


def test_hits_are_counted_per_prefix():
    engine = RedactionEngine([IP_RULE])
    result = engine.redact("10.0.0.1 10.0.0.2 10.0.0.1")
    assert result.hits == {"IP": 2}   # 两个不同实体


def test_restore_handles_llm_output_containing_placeholders():
    """模型返回的文本里带占位符，还原后必须是真实内容。"""
    engine = RedactionEngine([IP_RULE])
    result = engine.redact("请检查 10.0.0.1")
    llm_output = f"建议核查 {list(result.mapping)[0]} 的访问日志。"
    assert engine.restore(llm_output, result.mapping) == "建议核查 10.0.0.1 的访问日志。"


def test_text_without_matches_is_unchanged():
    engine = RedactionEngine([IP_RULE])
    result = engine.redact("这段话里没有任何敏感信息")
    assert result.text == "这段话里没有任何敏感信息"
    assert result.mapping == {}


@pytest.mark.parametrize(
    "original",
    [
        "",
        "纯中文没有敏感信息",
        "10.0.0.1",
        "前 10.0.0.1 中 192.168.1.1 后",
        "重复 10.0.0.1 重复 10.0.0.1 重复 10.0.0.1",
        "边界情况 10.0.0.1，紧跟标点。",
    ],
)
def test_property_redact_then_restore_is_identity(original):
    """spec §6.2 硬约束：脱敏必须可逆。这条测试不许删。"""
    engine = RedactionEngine([IP_RULE, DICT_RULE])
    result = engine.redact(original)
    assert engine.restore(result.text, result.mapping) == original


# ── 不变量 1：一次调用里编号全局唯一，多段文本不得撞号 ──────────


def test_redact_many_shares_numbering_across_texts():
    """分两次 redact 会让两边都从 IP_1 开始，合并映射表时直接撞掉一个。"""
    engine = RedactionEngine([IP_RULE])
    batch = engine.redact_many(["堡垒机 192.168.1.1", "请核查 10.20.30.40"])

    assert batch.texts == ["堡垒机 [[IP_1]]", "请核查 [[IP_2]]"]
    assert batch.mapping == {"[[IP_1]]": "192.168.1.1", "[[IP_2]]": "10.20.30.40"}


def test_redact_many_restores_each_text_to_its_own_entity():
    engine = RedactionEngine([IP_RULE])
    batch = engine.redact_many(["堡垒机 192.168.1.1", "请核查 10.20.30.40"])

    assert engine.restore("查 [[IP_2]] 的日志", batch.mapping) == "查 10.20.30.40 的日志"
    assert engine.restore("查 [[IP_1]] 的日志", batch.mapping) == "查 192.168.1.1 的日志"


def test_same_entity_in_two_texts_gets_one_placeholder():
    engine = RedactionEngine([IP_RULE])
    batch = engine.redact_many(["主机 10.0.0.1", "还是 10.0.0.1"])

    assert batch.texts == ["主机 [[IP_1]]", "还是 [[IP_1]]"]
    assert len(batch.mapping) == 1
    assert batch.hits == {"IP": 1}


# ── 不变量 2：后续规则不得扫进已生成的占位符 ────────────────────


def test_later_rule_does_not_eat_earlier_placeholder():
    """[[IP_1]] 内部的 IP_1 长得就像系统账号号，规则必须扫不进去。"""
    engine = RedactionEngine([IP_RULE, ACCT_RULE])
    result = engine.redact("主机 10.0.0.1 由账号 SVC_88 管理")

    assert result.text == "主机 [[IP_1]] 由账号 [[ACCT_1]] 管理"
    assert "[[[[" not in result.text
    assert result.mapping == {"[[IP_1]]": "10.0.0.1", "[[ACCT_1]]": "SVC_88"}


def test_reversible_even_when_a_rule_could_match_placeholders():
    engine = RedactionEngine([IP_RULE, ACCT_RULE])
    original = "主机 10.0.0.1 由账号 SVC_88 管理，备机 10.0.0.2"
    result = engine.redact(original)

    assert engine.restore(result.text, result.mapping) == original


@pytest.mark.parametrize(
    "texts",
    [
        ["主机 10.0.0.1 账号 SVC_88"],
        ["堡垒机 192.168.1.1", "跳板机 10.20.30.40"],
        ["示例集团的 10.0.0.1", "账号 ADM_7 归 集团 管"],
        ["", "10.0.0.1", "没有敏感信息"],
        ["重复 SVC_88 重复 SVC_88", "别处也有 SVC_88"],
    ],
)
def test_property_redact_many_then_restore_is_identity(texts):
    """spec §6.2 硬约束的加强版：规则集里含有能匹配占位符形状的规则时也必须可逆。"""
    engine = RedactionEngine([DICT_RULE, IP_RULE, ACCT_RULE])
    batch = engine.redact_many(texts)

    for original, redacted in zip(texts, batch.texts, strict=True):
        assert engine.restore(redacted, batch.mapping) == original


# ── 国内常见 PII：手机号与身份证号 ──────────────────────────────────
#
# 制度正文、工单摘录、审计答复里出现个人手机号和身份证号是常态，而默认规则集
# 原本只有 IPv4 和邮箱两条，这两类直接原样发给第三方模型。
#
# 这里同时验两件事，缺一不可：
#   1. 正则本身对不对（下面几条）；
#   2. **默认规则集里确实种了这两条**（test_default_ruleset_seeds_cn_pii_rules）。
# 只验正则的话，规则没进库在生产上照样漏——本次生产栈实测就是这么发现的。

PHONE_CN_RULE = _rule(r"\b1[3-9]\d{9}\b", "PHONE", order=30)
IDCARD_CN_RULE = _rule(
    r"\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b",
    "IDCARD",
    order=40,
)


def test_cn_mobile_number_is_redacted():
    engine = RedactionEngine([PHONE_CN_RULE])
    result = engine.redact("运维主管张三电话 13800138000，故障时直接联系。")
    assert "13800138000" not in result.text
    assert "[[PHONE_1]]" in result.text
    assert engine.restore(result.text, result.mapping) == "运维主管张三电话 13800138000，故障时直接联系。"


def test_cn_id_card_is_redacted():
    engine = RedactionEngine([IDCARD_CN_RULE])
    result = engine.redact("经办人身份证 110101199003074471 已核验。")
    assert "110101199003074471" not in result.text
    assert "[[IDCARD_1]]" in result.text


def test_id_card_ending_in_x_is_redacted():
    """校验位是 X 的身份证同样要脱敏——只认数字会漏掉约十分之一的号。"""
    engine = RedactionEngine([IDCARD_CN_RULE])
    result = engine.redact("身份证 11010119900307447X")
    assert "11010119900307447X" not in result.text


def test_bank_account_is_not_mistaken_for_an_id_card():
    """19 位卡号不该被当成 18 位身份证。

    脱敏的假阳性不是"多脱一点更安全"：被替换掉的内容模型再也看不见，
    把卡号、流水号打成 [[IDCARD_1]] 会让抽取出来的控制点丢掉关键事实。
    """
    engine = RedactionEngine([IDCARD_CN_RULE])
    result = engine.redact("对公账户 6222020000000000123 每季度对账。")
    assert "6222020000000000123" in result.text
    assert "IDCARD" not in result.text


def test_short_order_number_is_not_mistaken_for_a_mobile():
    """11 位但不以 1[3-9] 开头的单号不该被脱敏。"""
    engine = RedactionEngine([PHONE_CN_RULE])
    result = engine.redact("工单号 12345678901 已关闭。")
    assert "12345678901" in result.text
    assert "PHONE" not in result.text


def test_cn_pii_rules_coexist_with_the_existing_ones():
    """四条规则一起跑，各自的占位符计数器不互相污染。"""
    engine = RedactionEngine([IP_RULE, PHONE_CN_RULE, IDCARD_CN_RULE])
    original = "跳板机 10.20.30.40 由张三（13800138000，110101199003074471）负责。"
    result = engine.redact(original)
    assert "[[IP_1]]" in result.text
    assert "[[PHONE_1]]" in result.text
    assert "[[IDCARD_1]]" in result.text
    assert engine.restore(result.text, result.mapping) == original


@pytest.mark.asyncio
async def test_default_ruleset_seeds_cn_pii_rules(db_session):
    """迁移种下的默认规则集必须覆盖手机号和身份证号，两个 ruleset 都要有。

    generation 和 embedding 分开种：embedding 那套是"宽松"的（保留业务术语），
    但 PII 不属于业务术语，两边都得脱。
    """
    rules = (await db_session.scalars(select(RedactionRule))).all()
    for ruleset in (RulesetName.GENERATION, RulesetName.EMBEDDING):
        prefixes = {r.replacement_prefix for r in rules if r.ruleset == ruleset and r.enabled}
        assert "PHONE" in prefixes, f"{ruleset} 缺少手机号脱敏规则"
        assert "IDCARD" in prefixes, f"{ruleset} 缺少身份证脱敏规则"


@pytest.mark.asyncio
async def test_seeded_cn_rules_actually_redact(db_session):
    """种进库的正则要真能用——迁移里写错一个反斜杠，上面那条断言照样过。"""
    rules = [
        r
        for r in (await db_session.scalars(select(RedactionRule))).all()
        if r.ruleset == RulesetName.GENERATION and r.enabled
    ]
    engine = RedactionEngine(rules)
    result = engine.redact("联系人 13800138000，证件 110101199003074471。")
    assert "13800138000" not in result.text
    assert "110101199003074471" not in result.text


def test_an_invalid_regex_is_rejected_when_the_rule_is_saved():
    """非法正则必须在保存时就被挡下，不能等到跑 AI 的时候才炸。

    `pattern` 原先只校验「非空字符串」。管理员在脱敏规则页打错一个字符（`[A-`），
    保存成功；此后每一次 `load_engine` 都在 `re.compile` 上抛 `re.error`——那不是
    AppError，没有 code，于是抽取／映射／冲突／关系／审计答复**全线失败**，而
    worker 里的批量任务是直接死掉、界面上什么都不显示的（见 budget 那条同款）。
    一个字符的笔误换整条 AI 管线，代价太不对等。
    """
    import pytest
    from pydantic import ValidationError

    from app.llm.models import RulesetName
    from app.llm.schemas import RedactionRuleIn

    with pytest.raises(ValidationError) as caught:
        RedactionRuleIn(
            ruleset=RulesetName.GENERATION,
            pattern_type="regex",
            pattern="[A-",
            replacement_prefix="ORG",
        )
    assert "not a valid regular expression" in str(caught.value)

    # 合法的照常通过
    ok = RedactionRuleIn(
        ruleset=RulesetName.GENERATION,
        pattern_type="regex",
        pattern=r"\b\d{1,3}(?:\.\d{1,3}){3}\b",
        replacement_prefix="IP",
    )
    assert ok.pattern_type == "regex"

    # dictionary 类型不是正则，不该被当成正则校验
    literal = RedactionRuleIn(
        ruleset=RulesetName.GENERATION,
        pattern_type="dictionary",
        pattern="示例集团\n集团",
        replacement_prefix="ORG",
    )
    assert literal.pattern_type == "dictionary"


def test_restore_does_not_re_substitute_what_it_just_inserted():
    """还原是一次性的：刚填回去的原文不能再被后面的键吃一次。

    `restore` 逐条 `str.replace`，所以当某条原文本身长得像另一个占位符时，
    先填回去的内容会被后一轮再替换掉一次，还原出来是第三个东西——静默且看起来
    合理，正是 spec §6.2 那两条不变量要防的同一类事故。规则是管理员在界面上自己
    写的正则（`pattern_type="regex"`），所以「原文里出现占位符形状」不是纯理论。
    """
    engine = RedactionEngine([])
    mapping = {"[[ORG_1]]": "[[IP_2]]", "[[IP_2]]": "10.0.0.1"}

    assert engine.restore("[[ORG_1]]", mapping) == "[[IP_2]]"
    assert engine.restore("[[IP_2]]", mapping) == "10.0.0.1"
    assert engine.restore("a [[ORG_1]] b [[IP_2]] c", mapping) == "a [[IP_2]] b 10.0.0.1 c"
