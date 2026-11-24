from __future__ import annotations

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse


def structured_arguments(schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties", {})
    if "task_overview" in properties:
        text = (
            "灯塔项目处于接口联调阶段，预算 8 万元。主要阻塞是支付沙箱回调偶发超时，"
            "下一步补充重试与幂等键，并通知测试组复测。"
        )
        return {name: text for name in properties}
    if "destination" in properties:
        return {
            "destination": "杭州",
            "days": 3,
            "budget_cny": 5000,
            "activities": ["西湖漫步", "龙井茶园", "运河夜游"],
            "reason": "兼顾自然风景、文化体验与步行强度。",
        }
    return {
        name: value.get("default", "")
        for name, value in properties.items()
    }


def content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict)
        )
    return str(content or "")


def choose_response(body: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    messages = body.get("messages", [])
    tools = body.get("tools") or []
    tool_by_name = {
        item["function"]["name"]: item["function"] for item in tools
    }
    tool_choice = body.get("tool_choice")
    if (
        isinstance(tool_choice, dict)
        and tool_choice.get("function", {}).get("name")
        == "generate_structured_output"
    ):
        function = tool_by_name["generate_structured_output"]
        return "tool_calls", [
            {
                "id": "call_structured",
                "name": "generate_structured_output",
                "arguments": structured_arguments(function["parameters"]),
            },
        ]

    user_indexes = [
        index
        for index, message in enumerate(messages)
        if message.get("role") == "user"
        and "<system-reminder>" not in content_text(message.get("content", ""))
    ]
    latest_user_index = user_indexes[-1] if user_indexes else -1
    turn_messages = messages[latest_user_index + 1 :]
    user_text = (
        content_text(messages[latest_user_index].get("content", ""))
        if latest_user_index >= 0
        else ""
    )
    assistant_calls = [
        call
        for message in turn_messages
        if message.get("role") == "assistant"
        for call in message.get("tool_calls") or []
    ]
    tool_results = [
        message for message in turn_messages if message.get("role") == "tool"
    ]
    if "TeamAssign" in tool_by_name:
        parameters = tool_by_name["TeamAssign"]["parameters"]
        members = parameters.get("properties", {}).get("member", {}).get("enum", [])
        assigned = set()
        for call in assistant_calls:
            if call.get("function", {}).get("name") == "TeamAssign":
                try:
                    arguments = json.loads(call["function"].get("arguments", "{}"))
                    assigned.add(arguments.get("member"))
                except (TypeError, ValueError):
                    pass
        remaining = [name for name in members if name not in assigned]
        if remaining:
            return "tool_calls", [
                {
                    "id": f"call_assign_{remaining[0]}",
                    "name": "TeamAssign",
                    "arguments": {
                        "member": remaining[0],
                        "prompt": "请完成行程中你负责的部分，并给出简洁结论。",
                    },
                },
            ]

    if assistant_calls and tool_results:
        outputs = []
        for message in tool_results:
            content = message.get("content", "")
            if isinstance(content, str):
                outputs.append(content)
            else:
                outputs.append(json.dumps(content, ensure_ascii=False))
        return "stop", [
            {
                "text": "根据工具结果，建议如下：\n"
                + "\n".join(outputs[:5])[:1200],
            },
        ]

    if tools and not assistant_calls:
        business_call = None
        if "create_support_ticket" in tool_by_name and "创建" in user_text and "工单" in user_text:
            business_call = {
                "id": "call_create_support_ticket",
                "name": "create_support_ticket",
                "arguments": {
                    "subject": "账号登录问题",
                    "description": "用户希望协助排查账号登录问题。",
                    "priority": "high",
                },
            }
        elif "list_all_tickets" in tool_by_name and "所有用户" in user_text:
            business_call = {
                "id": "call_list_all_tickets",
                "name": "list_all_tickets",
                "arguments": {"status": "all"},
            }
        elif "list_my_orders" in tool_by_name and "订单" in user_text:
            business_call = {
                "id": "call_list_my_orders",
                "name": "list_my_orders",
                "arguments": {},
            }
        elif "list_my_tickets" in tool_by_name and "工单" in user_text:
            business_call = {
                "id": "call_list_my_tickets",
                "name": "list_my_tickets",
                "arguments": {"status": "open"},
            }
        if business_call is not None:
            return "tool_calls", [business_call]

    if tools:
        names = [
            name
            for name in (
                "get_weather",
                "search_local_guide",
                "calculate_trip_budget",
            )
            if name in tool_by_name
        ]
        arguments = {
            "get_weather": {"city": "杭州"},
            "search_local_guide": {"city": "杭州", "interest": "两日游"},
            "calculate_trip_budget": {
                "days": 3,
                "travelers": 2,
                "hotel_per_night": 700,
                "transport_per_person": 500,
                "daily_per_person": 400,
            },
        }
        return "tool_calls", [
            {"id": f"call_{name}", "name": name, "arguments": arguments[name]}
            for name in names
        ]

    return "stop", [{"text": f"离线模拟模型已收到：{user_text or '你好'}"}]


class MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/health", "/v1/health"):
            self.send_json(200, {"status": "ok"})
        elif path in ("/models", "/v1/models"):
            self.send_json(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": os.getenv("OPENAI_MODEL", "mock-model"),
                            "object": "model",
                            "created": int(time.time()),
                            "owned_by": "local-test",
                        },
                    ],
                },
            )
        else:
            self.send_json(404, {"error": {"message": "Not found"}})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/v1/chat/completions":
            self.send_json(404, {"error": {"message": "Not found"}})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self.send_json(400, {"error": {"message": "Invalid JSON"}})
            return

        if os.getenv("MOCK_DEBUG"):
            print(json.dumps(body, ensure_ascii=False), file=sys.stderr, flush=True)

        finish_reason, parts = choose_response(body)
        model = body.get("model", "mock-model")
        usage = {
            "prompt_tokens": max(len(json.dumps(body.get("messages", []))) // 4, 1),
            "completion_tokens": 12,
            "total_tokens": 20,
        }
        completion_id = "chatcmpl-mock"
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            def chunk(delta: dict[str, Any], reason: str | None = None) -> None:
                payload = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [
                        {"index": 0, "delta": delta, "finish_reason": reason},
                    ],
                }
                self.wfile.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()

            chunk({"role": "assistant", "content": ""})
            if finish_reason == "tool_calls":
                for index, part in enumerate(parts):
                    chunk(
                        {
                            "tool_calls": [
                                {
                                    "index": index,
                                    "id": part["id"],
                                    "type": "function",
                                    "function": {
                                        "name": part["name"],
                                        "arguments": "",
                                    },
                                },
                            ],
                        },
                    )
                    arguments = json.dumps(part["arguments"], ensure_ascii=False)
                    midpoint = max(len(arguments) // 2, 1)
                    chunk(
                        {
                            "tool_calls": [
                                {
                                    "index": index,
                                    "function": {
                                        "arguments": arguments[:midpoint],
                                    },
                                },
                            ],
                        },
                    )
                    chunk(
                        {
                            "tool_calls": [
                                {
                                    "index": index,
                                    "function": {
                                        "arguments": arguments[midpoint:],
                                    },
                                },
                            ],
                        },
                    )
            else:
                text = "\n".join(part["text"] for part in parts)
                for start in range(0, len(text), 18):
                    chunk({"content": text[start : start + 18]})
            chunk({}, finish_reason)
            final = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [],
                "usage": usage,
            }
            self.wfile.write(f"data: {json.dumps(final)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return

        if finish_reason == "tool_calls":
            message: dict[str, Any] = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": part["id"],
                        "type": "function",
                        "function": {
                            "name": part["name"],
                            "arguments": json.dumps(
                                part["arguments"],
                                ensure_ascii=False,
                            ),
                        },
                    }
                    for part in parts
                ],
            }
        else:
            message = {
                "role": "assistant",
                "content": "\n".join(part["text"] for part in parts),
            }
        self.send_json(
            200,
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": finish_reason,
                    },
                ],
                "usage": usage,
            },
        )


def main() -> None:
    port = int(os.getenv("MOCK_PORT", "18080"))
    server = ThreadingHTTPServer(("127.0.0.1", port), MockHandler)
    print(f"Mock OpenAI provider listening on http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
