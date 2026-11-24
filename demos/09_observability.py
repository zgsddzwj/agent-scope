from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

from agentscope.message import UserMsg
from agentscope.middleware import ReplyBudgetControlMiddleware, TracingMiddleware

from demos.common import build_agent, print_result, render_stream, run
from demos.tools import make_travel_toolkit


async def main() -> None:
    provider = TracerProvider()
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        print(f"追踪将发送到：{endpoint}")
    else:
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter

        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        print("未配置 OTLP 地址，追踪 span 将打印到控制台。")
    trace.set_tracer_provider(provider)

    agent = build_agent(
        name="可观测旅行助手",
        system_prompt="你是旅行助手。需要事实与计算时调用工具，最后给出有依据的总结。",
        tools=make_travel_toolkit(),
        stream=True,
        middlewares=[
            TracingMiddleware(),
            ReplyBudgetControlMiddleware(
                token_budget=8000,
                input_token_weight=1.0,
                output_token_weight=2.0,
            ),
        ],
    )
    result = await render_stream(
        agent,
        UserMsg(
            name="user",
            content="查询杭州模拟天气和攻略，并计算两人三天预算：住宿每晚 700 元，交通每人 500 元，每人每天 400 元。",
        ),
    )
    print_result(result, state=agent.state)
    provider.force_flush()
    provider.shutdown()


if __name__ == "__main__":
    run(main)
