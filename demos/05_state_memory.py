from __future__ import annotations

import argparse

from agentscope.message import UserMsg
from agentscope.state import AgentState

from demos.common import PROJECT_ROOT, build_agent, print_result, run

STATE_FILE = PROJECT_ROOT / ".runtime" / "state.json"


def make_agent(state: AgentState | None = None):
    return build_agent(
        name="记忆助手",
        system_prompt="你是一个可靠的助手。准确使用对话历史中的项目名称、预算和约束。",
        stream=False,
        state=state,
    )


async def save() -> None:
    agent = make_agent()
    result = await agent.reply(
        UserMsg(
            name="user",
            content="请记住：我的项目代号是“灯塔”，总预算是 8 万元，最晚上线日期是 12 月 20 日。",
        ),
    )
    print_result(result, state=agent.state)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(agent.state.model_dump_json(indent=2), encoding="utf-8")
    print(f"状态已写入：{STATE_FILE}")
    print(f"上下文消息数：{len(agent.state.context)}")


async def resume() -> None:
    if not STATE_FILE.exists():
        raise SystemExit("没有找到状态文件，请先运行：python run_demo.py 05 save")
    state = AgentState.model_validate_json(STATE_FILE.read_text(encoding="utf-8"))
    print(f"已恢复会话：{state.session_id}，历史消息数：{len(state.context)}")
    agent = make_agent(state)
    result = await agent.reply(
        UserMsg(name="user", content="我的项目代号、总预算和最晚上线日期分别是什么？"),
    )
    print_result(result, state=agent.state)


async def main() -> None:
    parser = argparse.ArgumentParser(description="跨进程保存并恢复 Agent 状态")
    parser.add_argument("mode", choices=["save", "resume"], nargs="?", default="save")
    args = parser.parse_args()
    if args.mode == "save":
        await save()
    else:
        await resume()


if __name__ == "__main__":
    run(main)
