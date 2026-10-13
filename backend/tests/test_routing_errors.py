"""任务路由的错误带具体 code：「没配 provider」是新部署第一个撞上的错误。"""

import pytest

from app.llm.routing import RoutingError, resolve


@pytest.mark.asyncio
async def test_an_unrouted_task_says_which_task_with_a_code(db_session):
    # 原来只有类级 code「routing_error」，词条表里没有它，英文界面显示中文。
    with pytest.raises(RoutingError) as caught:
        await resolve(db_session, "control_extract")
    assert caught.value.code == "task.not_routed"
    assert caught.value.params == {"task": "control_extract"}
