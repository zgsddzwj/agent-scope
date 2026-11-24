from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
_MODELS: list[Any] = []


def configure_console() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")


def provider_name() -> str:
    return os.getenv("AGENT_PROVIDER", "dashscope").strip().lower()


def model_name() -> str:
    if provider_name() == "dashscope":
        return os.getenv("DASHSCOPE_MODEL", "qwen-plus")
    if provider_name() == "openai":
        return os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    raise SystemExit("AGENT_PROVIDER 仅支持 dashscope 或 openai。")


def create_model(*, stream: bool = True, context_size: int | None = None, track: bool = True):
    provider = provider_name()
    temperature = float(os.getenv("TEMPERATURE", "0.3"))
    kwargs: dict[str, Any] = {
        "stream": stream,
        "max_retries": 2,
    }
    if context_size is not None:
        kwargs["context_size"] = context_size

    if provider == "dashscope":
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise SystemExit(
                "缺少 DASHSCOPE_API_KEY。请复制 .env.example 为 .env 并填写百炼 API Key。"
            )
        from agentscope.credential import DashScopeCredential
        from agentscope.model import DashScopeChatModel

        credential_kwargs: dict[str, Any] = {"api_key": api_key}
        base_url = os.getenv("DASHSCOPE_BASE_URL")
        if base_url:
            credential_kwargs["base_url"] = base_url
        kwargs["parameters"] = DashScopeChatModel.Parameters(temperature=temperature)
        model = DashScopeChatModel(
            credential=DashScopeCredential(**credential_kwargs),
            model=model_name(),
            **kwargs,
        )
        if track:
            _MODELS.append(model)
        return model

    if provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise SystemExit(
                "缺少 OPENAI_API_KEY。请复制 .env.example 为 .env 并填写 API Key。"
            )
        from agentscope.credential import OpenAICredential
        from agentscope.model import OpenAIChatModel

        credential_kwargs = {"api_key": api_key}
        base_url = os.getenv("OPENAI_BASE_URL")
        if base_url:
            credential_kwargs["base_url"] = base_url
        kwargs["parameters"] = OpenAIChatModel.Parameters(temperature=temperature)
        model = OpenAIChatModel(
            credential=OpenAICredential(**credential_kwargs),
            model=model_name(),
            **kwargs,
        )
        if track:
            _MODELS.append(model)
        return model

    raise SystemExit("AGENT_PROVIDER 仅支持 dashscope 或 openai。")


def build_agent(
    *,
    name: str,
    system_prompt: str,
    tools: Sequence[Any] | None = None,
    stream: bool = True,
    context_size: int | None = None,
    state: Any = None,
    middlewares: Sequence[Any] | None = None,
    context_config: Any = None,
    react_config: Any = None,
    multi_agent: bool = False,
    model: Any = None,
):
    from agentscope.agent import Agent, ReActConfig
    from agentscope.tool import FunctionTool, Toolkit, ToolBase

    model = model or create_model(stream=stream, context_size=context_size)
    if multi_agent:
        if provider_name() == "dashscope":
            from agentscope.formatter import DashScopeMultiAgentFormatter

            model.formatter = DashScopeMultiAgentFormatter()
        else:
            from agentscope.formatter import OpenAIMultiAgentFormatter

            model.formatter = OpenAIMultiAgentFormatter()

    toolkit = (
        tools
        if isinstance(tools, Toolkit)
        else Toolkit(
            tools=[
                tool if isinstance(tool, ToolBase) else FunctionTool(tool)
                for tool in tools or []
            ],
        )
    )
    kwargs: dict[str, Any] = {
        "name": name,
        "system_prompt": system_prompt,
        "model": model,
        "toolkit": toolkit,
        "react_config": react_config
        or ReActConfig(max_iters=int(os.getenv("MAX_ITERS", "12"))),
    }
    if state is not None:
        kwargs["state"] = state
    if middlewares:
        kwargs["middlewares"] = list(middlewares)
    if context_config is not None:
        kwargs["context_config"] = context_config
    return Agent(**kwargs)


def enum_text(value: Any) -> str:
    return str(getattr(value, "value", value))


def print_result(result: Any, *, state: Any = None) -> None:
    from agentscope.message import ToolCallBlock, ToolResultBlock

    reply = state.get_reply_message(result.name) if state is not None else None
    reply = reply or result
    print("\n===== 最终回复 =====")
    print(result.get_text_content() or "（没有文本输出）")
    if result.structured_output is not None:
        print("\n结构化输出：", result.structured_output)
    calls = reply.get_content_blocks("tool_call")
    results = reply.get_content_blocks("tool_result")
    if calls:
        print(f"\n工具调用：{len(calls)} 次")
        for call in calls:
            assert isinstance(call, ToolCallBlock)
            print(f"  - {call.name}({call.input})")
    if results:
        for item in results:
            assert isinstance(item, ToolResultBlock)
            print(f"  - {item.name}: {enum_text(item.state)}")
    print("结束原因：", enum_text(result.finished_reason) if result.finished_reason else "paused")
    if result.usage is not None:
        print("Token 用量：", result.usage.model_dump())
    if result.error is not None:
        print("错误：", result.error.model_dump())


async def render_events(events: Any, *, expect_final: bool = True) -> Any:
    from agentscope.event import (
        ModelCallEndEvent,
        ModelCallStartEvent,
        ReplyEndEvent,
        ReplyStartEvent,
        RequireExternalExecutionEvent,
        RequireUserConfirmEvent,
        TextBlockDeltaEvent,
        ToolCallDeltaEvent,
        ToolCallStartEvent,
        ToolResultEndEvent,
        ToolResultStartEvent,
    )
    from agentscope.message import Msg

    final_msg = None
    text_parts: list[str] = []
    async for event in events:
        if isinstance(event, Msg):
            final_msg = event
        elif isinstance(event, ReplyStartEvent):
            print(f"\n[{event.name}] 开始回复", flush=True)
        elif isinstance(event, TextBlockDeltaEvent):
            text_parts.append(event.delta)
            print(event.delta, end="", flush=True)
        elif isinstance(event, ToolCallStartEvent):
            print(f"\n  → 调用工具 {event.tool_call_name}", flush=True)
        elif isinstance(event, ToolCallDeltaEvent):
            print(event.delta, end="", flush=True)
        elif isinstance(event, ToolResultStartEvent):
            print(f"\n  ← 执行 {event.tool_call_name}", flush=True)
        elif isinstance(event, ToolResultEndEvent):
            print(f" [{enum_text(event.state)}]", flush=True)
        elif isinstance(event, ModelCallStartEvent):
            print(f"\n  [模型] {event.model_name}", flush=True)
        elif isinstance(event, ModelCallEndEvent):
            print(
                f"  [模型完成] 输入 {event.input_tokens} / 输出 {event.output_tokens}",
                flush=True,
            )
        elif isinstance(event, RequireUserConfirmEvent):
            print("\n  暂停：等待用户授权。请使用交互式终端示例。", flush=True)
        elif isinstance(event, RequireExternalExecutionEvent):
            print("\n  暂停：等待外部执行结果。", flush=True)
        elif isinstance(event, ReplyEndEvent):
            print("\n  回复结束。", flush=True)
    print()
    if final_msg is not None:
        return final_msg
    if expect_final:
        raise RuntimeError("事件流未返回最终消息。")
    return "".join(text_parts)


async def render_stream(agent: Any, inputs: Any) -> Any:
    return await render_events(agent.reply_stream(inputs, yield_final_msg=True))


async def close_models() -> None:
    for model in _MODELS:
        client = getattr(model, "client", None)
        if client is not None:
            await client.close()
    _MODELS.clear()


def run(async_main: Callable[[], Awaitable[None]]) -> None:
    configure_console()

    async def invoke() -> None:
        try:
            await async_main()
        finally:
            await close_models()

    try:
        asyncio.run(invoke())
    except KeyboardInterrupt:
        print("\n已取消。")
