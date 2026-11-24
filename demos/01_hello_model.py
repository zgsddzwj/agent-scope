from __future__ import annotations

import argparse

from agentscope.message import TextBlock, UserMsg

from demos.common import create_model, model_name, provider_name, run


async def main() -> None:
    parser = argparse.ArgumentParser(description="直接调用 AgentScope 聊天模型")
    parser.add_argument("prompt", nargs="?", default="用三句话解释 AgentScope 是什么。")
    parser.add_argument("--no-stream", action="store_true")
    args = parser.parse_args()

    print(f"提供商：{provider_name()} | 模型：{model_name()}")
    model = create_model(stream=not args.no_stream)
    messages = [UserMsg(name="user", content=args.prompt)]

    if args.no_stream:
        response = await model(messages)
        print("\n".join(block.text for block in response.content if isinstance(block, TextBlock)))
        print("用量：", response.usage)
        return

    final = None
    async for chunk in await model(messages):
        if chunk.is_last:
            final = chunk
        else:
            for block in chunk.content:
                if isinstance(block, TextBlock):
                    print(block.text, end="", flush=True)
    if final is None:
        raise RuntimeError("模型没有返回最终响应。")
    print("\n\n最终完整响应：")
    print("\n".join(block.text for block in final.content if isinstance(block, TextBlock)))
    print("用量：", final.usage)


if __name__ == "__main__":
    run(main)
