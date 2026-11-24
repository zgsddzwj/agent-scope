from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

DEMOS = {
    "00": ("环境检查", "demos.00_doctor"),
    "01": ("直接调用模型", "demos.01_hello_model"),
    "02": ("结构化输出", "demos.02_structured_output"),
    "03": ("ReAct 与工具调用", "demos.03_tool_agent"),
    "04": ("流式事件", "demos.04_stream_events"),
    "05": ("多轮记忆与状态持久化", "demos.05_state_memory"),
    "06": ("多智能体协作", "demos.06_multi_agent"),
    "07": ("TeamPipeline 团队流水线", "demos.07_team_pipeline"),
    "08": ("上下文压缩", "demos.08_context_management"),
    "09": ("链路追踪与预算控制", "demos.09_observability"),
    "10": ("认证多租户业务 Web 应用", "web_app"),
    "11": ("子 Agent 串行编排", "demos.11_meal_subagents"),
}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help", "list"}:
        print("AgentScope 2.0.12 渐进式示例\n")
        print("用法：python run_demo.py <编号> [参数]\n")
        for number, (title, _) in DEMOS.items():
            print(f"  {number}  {title}")
        print("\n首次运行：python -m pip install -r requirements.txt")
        print("配置模型：复制 .env.example 为 .env，然后填写 API Key")
        return

    number = sys.argv[1]
    if number not in DEMOS:
        raise SystemExit(f"未知示例编号：{number}。使用 python run_demo.py list 查看。")
    module = DEMOS[number][1]
    result = subprocess.run([sys.executable, "-m", module, *sys.argv[2:]], cwd=ROOT)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
