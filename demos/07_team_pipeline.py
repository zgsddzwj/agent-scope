from __future__ import annotations

from agentscope.message import UserMsg
from agentscope.pipeline import TeamMember, TeamPipeline
from agentscope.tool import FunctionTool

from demos.common import build_agent, render_events, run
from demos.tools import (
    calculate_trip_budget,
    get_weather,
    search_local_guide,
)


async def main() -> None:
    leader = build_agent(
        name="行程总监",
        system_prompt=(
            "你是行程总监。必须使用 TeamAssign 工具，把信息收集、预算核算和方案审查"
            "分别交给合适的成员。你可以并行分派不同成员，也可以在同一成员上追问。"
            "收到成员结果后，给出最终行程，不要自己替代成员工作。"
        ),
        stream=True,
    )
    researcher = build_agent(
        name="目的地研究员",
        system_prompt="你负责目的地信息。必须调用天气和攻略工具，并简洁汇报事实。",
        tools=[
            FunctionTool(get_weather, is_read_only=True),
            FunctionTool(search_local_guide, is_read_only=True),
        ],
    )
    budget_analyst = build_agent(
        name="预算分析师",
        system_prompt="你负责核算预算。必须调用预算工具，列出金额和人均成本。",
        tools=[
            FunctionTool(calculate_trip_budget, is_read_only=True),
        ],
        stream=False,
    )
    reviewer = build_agent(
        name="方案审查员",
        system_prompt="你审查行程的完整性、预算合理性和风险。只返回需要修正的问题。",
        stream=False,
    )

    pipeline = TeamPipeline(
        leader=leader,
        members=[
            TeamMember(
                agent=researcher,
                description="查询目的地天气与模拟攻略，返回信息摘要。",
            ),
            TeamMember(
                agent=budget_analyst,
                description="根据人数、天数与价格假设计算准确预算。",
            ),
            TeamMember(
                agent=reviewer,
                description="审查最终草案，指出遗漏、超支与风险。",
            ),
        ],
    )

    final_text = await render_events(
        pipeline.reply_stream(
            UserMsg(
                name="user",
                content=(
                    "为 4 人设计杭州两日团建行程。住宿每晚总计 800 元，"
                    "每人往返交通 300 元，每人每天餐饮门票和市内交通 300 元。"
                    "先研究目的地，再核算预算，最后审查并输出定稿。"
                ),
            ),
        ),
        expect_final=False,
    )
    print("===== 团队最终输出 =====")
    print(final_text)


if __name__ == "__main__":
    run(main)
