from __future__ import annotations

from agentscope.agent import ContextConfig
from agentscope.message import HintBlock, TextBlock, UserMsg

from demos.common import build_agent, print_result, run


def context_size(state) -> int:
    return sum(len(message.model_dump_json()) for message in state.context)


def summary_text(state) -> str:
    if isinstance(state.summary, str):
        return state.summary
    return "\n".join(
        block.text for block in state.summary if isinstance(block, TextBlock)
    )


async def main() -> None:
    agent = build_agent(
        name="长任务助手",
        system_prompt=(
            "你正在协助“灯塔”项目。准确保留项目代号、预算、决策、阻塞和下一步行动。"
        ),
        context_size=4096,
        context_config=ContextConfig(
            trigger_ratio=0.3,
            reserve_ratio=0.05,
            context_buffer_ratio=0.1,
            tool_result_limit=200,
            compression_fallback_to_truncation=False,
        ),
    )

    records = []
    for index in range(1, 25):
        records.append(
            UserMsg(
                name="user",
                content=(
                    f"灯塔项目第 {index} 次进度记录：当前阶段是接口联调，"
                    f"预算仍为 8 万元。已完成模块 M{index % 5 + 1}，"
                    "测试通过率为 96%。主要阻塞是支付沙箱回调偶发超时，"
                    "下一步是补充重试与幂等键，并通知测试组复测。"
                ),
            ),
        )
    await agent.observe(records)
    print(f"压缩前：{len(agent.state.context)} 条消息，约 {context_size(agent.state)} 字符")

    await agent.compress_context(
        ContextConfig(
            trigger_ratio=0.3,
            reserve_ratio=0.05,
            context_buffer_ratio=0.1,
            compression_fallback_to_truncation=False,
        ),
        instructions=HintBlock(
            hint="摘要必须保留项目代号“灯塔”、8 万元预算、支付回调超时阻塞和下一步重试方案。",
        ),
    )

    print(f"压缩后：{len(agent.state.context)} 条消息")
    print("摘要：")
    print(summary_text(agent.state))

    result = await agent.reply(
        UserMsg(name="user", content="根据项目历史，当前阻塞是什么？下一步应该做什么？"),
    )
    print_result(result, state=agent.state)


if __name__ == "__main__":
    run(main)
