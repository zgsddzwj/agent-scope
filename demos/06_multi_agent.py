from __future__ import annotations

from agentscope.message import AssistantMsg, UserMsg

from demos.common import build_agent, print_result, run
from demos.tools import make_travel_toolkit

TASK = (
    "一个 6 人团队计划去杭州团建两天，总预算 12000 元。"
    "请分别从体验、成本和风险角度给出建议，每人不超过 150 字。"
)


async def main() -> None:
    experts = [
        build_agent(
            name="用户体验专家",
            system_prompt="你关注行程趣味性、参与感和不同体力的兼容性。只输出你的专业建议。",
        ),
        build_agent(
            name="成本控制专家",
            system_prompt="你是成本专家。需要计算时调用预算工具，明确假设，不编造价格。",
            tools=make_travel_toolkit(),
            stream=False,
        ),
        build_agent(
            name="风险管理专家",
            system_prompt="你关注天气、交通、人员安全和备选方案。只输出关键风险与缓解措施。",
        ),
    ]

    proposals: list[AssistantMsg] = []
    for expert in experts:
        print(f"\n===== {expert.name} =====")
        result = await expert.reply(UserMsg(name="user", content=TASK))
        print_result(result, state=expert.state)
        proposals.append(
            AssistantMsg(name=expert.name, content=result.get_text_content() or "无建议"),
        )

    judge = build_agent(
        name="决策主持人",
        system_prompt=(
            "你是决策主持人。综合多位专家的意见，指出共识与分歧，"
            "给出一个可执行的最终方案。用中文回答。"
        ),
        stream=False,
        multi_agent=True,
    )
    await judge.observe(proposals)
    final = await judge.reply(
        UserMsg(name="user", content="请汇总专家意见，形成最终团建方案和三条执行建议。"),
    )
    print("\n===== 最终决策 =====")
    print_result(final, state=judge.state)


if __name__ == "__main__":
    run(main)
