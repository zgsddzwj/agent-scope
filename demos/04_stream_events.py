from __future__ import annotations

import argparse

from agentscope.message import UserMsg

from demos.common import build_agent, print_result, render_stream, run
from demos.tools import make_travel_toolkit


async def main() -> None:
    parser = argparse.ArgumentParser(description="观察 Agent 的流式事件")
    parser.add_argument(
        "prompt",
        nargs="?",
        default="帮我比较杭州和成都的两日游预算，两人出行，住宿均为每晚 500 元。",
    )
    args = parser.parse_args()

    agent = build_agent(
        name="流式旅行助手",
        system_prompt=(
            "你是旅行助手。需要天气、攻略或计算时调用工具。"
            "每次工具调用都会以事件形式展示，最终用中文总结。"
        ),
        tools=make_travel_toolkit(),
        stream=True,
    )
    result = await render_stream(agent, UserMsg(name="user", content=args.prompt))
    print_result(result, state=agent.state)


if __name__ == "__main__":
    run(main)
