from __future__ import annotations

from agentscope.agent import ContextConfig
from agentscope.message import UserMsg

from demos.common import build_agent, print_result, run
from demos.tools import make_travel_toolkit


async def main() -> None:
    agent = build_agent(
        name="旅行助手",
        system_prompt=(
            "你是旅行规划助手。涉及天气、预算和推荐时，必须调用提供的工具，"
            "不得编造工具结果。收到工具结果后，用中文给出清晰结论。"
        ),
        tools=make_travel_toolkit(),
        stream=False,
        context_config=ContextConfig(tool_result_limit=1200),
    )
    result = await agent.reply(
        UserMsg(
            name="user",
            content=(
                "规划两人杭州两日游：先查杭州模拟天气，再查询适合自然风光的模拟攻略，"
                "并计算预算。住宿每晚 600 元，每人往返交通 400 元，"
                "每人每天餐饮门票和市内交通 350 元。"
            ),
        ),
    )
    print_result(result, state=agent.state)


if __name__ == "__main__":
    run(main)
