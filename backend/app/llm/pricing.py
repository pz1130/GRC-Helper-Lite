"""模型价格表（美元 / 百万 token）。

价格会变，运维应定期核对。未知模型返回 0 而不是抛异常——
成本统计不准可以接受，让整条流水线挂掉不可接受。

但"返回 0"不能是唯一的痕迹：未计价和真的零成本长得一模一样，
预算卡会照常显示 $0.00 / $350.00，闸门形同虚设。`is_priced()` 就是
给调用方留的那个信号，`LLMCall.cost_unknown` 记住它。

**不要靠往这张表里加模型来解决未计价**——价格会变，下一家机构又换模型，
那是会过期的数据。要让"没有价"这件事本身可见。
"""

_PRICES: dict[str, tuple[float, float]] = {
    # model: (input_per_mtok, output_per_mtok)
    "claude-opus-5": (15.0, 75.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    "deepseek-chat": (0.27, 1.1),
    "qwen-max": (1.6, 6.4),
    "text-embedding-3-large": (0.13, 0.0),
}


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    price = _PRICES.get(model)
    if price is None:
        return 0.0
    in_rate, out_rate = price
    return tokens_in / 1_000_000 * in_rate + tokens_out / 1_000_000 * out_rate


def is_priced(model: str) -> bool:
    """这个模型在价格表里吗。False 意味着它的 cost 恒为 0 且不可信。"""
    return model in _PRICES
