from __future__ import annotations

import os
import platform
import sys
from importlib.metadata import version

from demos.common import model_name, provider_name


def main() -> None:
    print("AgentScope 环境检查")
    print("=" * 40)
    print(f"Python: {platform.python_version()} ({sys.executable})")
    print(f"AgentScope: {version('agentscope')}")
    print(f"平台: {platform.platform()}")
    print(f"提供商: {provider_name()}")
    print(f"模型: {model_name()}")

    if provider_name() == "dashscope":
        key_name = "DASHSCOPE_API_KEY"
        from agentscope.model import DashScopeChatModel

        cards = DashScopeChatModel.list_models()
    else:
        key_name = "OPENAI_API_KEY"
        from agentscope.model import OpenAIChatModel

        cards = OpenAIChatModel.list_models()

    print(f"{key_name}: {'已配置' if os.getenv(key_name) else '未配置'}")
    selected = next((card for card in cards if card.name == model_name()), None)
    if selected:
        print(f"模型卡片: 已识别，上下文 {selected.context_size} tokens")
    else:
        print("模型卡片: 未在内置目录中找到；自定义兼容服务仍可尝试使用。")

    if not os.getenv(key_name):
        print("\n提示：复制 .env.example 为 .env，填写 API Key 后即可运行 01～09。")
    else:
        print("\n环境配置完成。建议先运行 01，验证真实模型连接。")


if __name__ == "__main__":
    main()
