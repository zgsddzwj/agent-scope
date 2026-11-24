from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import pytest
from fastapi.testclient import TestClient

from agentscope.message import AssistantMsg, UserMsg
from agentscope.state import AgentState

from demos.common import build_agent, close_models, create_model
from demos.tools import calculate_trip_budget, get_weather, make_travel_toolkit

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def mock_provider():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env["MOCK_PORT"] = str(port)
    env["OPENAI_MODEL"] = "mock-model"
    env["MOCK_DEBUG"] = "1"
    env["PYTHONUTF8"] = "1"
    log_path = ROOT / ".runtime" / "mock-provider.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "tests" / "mock_provider.py")],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=log,
        )
        base_url = f"http://127.0.0.1:{port}/v1"
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("模拟模型服务启动失败")
                try:
                    with urlopen(f"http://127.0.0.1:{port}/health", timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                raise RuntimeError("模拟模型服务启动超时")
            yield base_url
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


@pytest.fixture
def provider_env(mock_provider, monkeypatch):
    monkeypatch.setenv("AGENT_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "mock-key")
    monkeypatch.setenv("OPENAI_BASE_URL", mock_provider)
    monkeypatch.setenv("OPENAI_MODEL", "mock-model")


def test_travel_tools_are_deterministic():
    assert "26°C" in get_weather("北京")
    result = calculate_trip_budget(3, 2, 700, 500, 400)
    assert "4800" in result
    with pytest.raises(ValueError):
        calculate_trip_budget(0, 2, 700, 500, 400)


def test_state_roundtrip_preserves_context():
    state = AgentState()
    state.context.extend(
        [
            UserMsg(name="user", content="记住预算为 8 万元"),
            AssistantMsg(name="助手", content="已记录"),
        ],
    )
    restored = AgentState.model_validate_json(state.model_dump_json())
    assert restored.context[0].get_text_content() == "记住预算为 8 万元"
    assert restored.context[1].get_text_content() == "已记录"


def test_structured_output_with_openai_compatible_provider(provider_env):
    from importlib import import_module

    structured_demo = import_module("demos.02_structured_output")
    TravelBrief = structured_demo.TravelBrief

    async def invoke():
        try:
            model = create_model(stream=False)
            response = await model.generate_structured_output(
                messages=[UserMsg(name="user", content="制定杭州三日游计划")],
                structured_model=TravelBrief,
            )
            return TravelBrief.model_validate(response.content)
        finally:
            await close_models()

    brief = asyncio.run(invoke())
    assert brief.destination == "杭州"
    assert brief.days == 3
    assert len(brief.activities) == 3


def test_agent_executes_tools_then_answers(provider_env):
    async def invoke():
        try:
            agent = build_agent(
                name="测试旅行助手",
                system_prompt="使用工具回答旅行问题。",
                tools=make_travel_toolkit(),
                stream=False,
            )
            result = await agent.reply(
                UserMsg(name="user", content="查询杭州天气并计算两人三天预算"),
            )
            reply = agent.state.context[-1]
            return result, reply
        finally:
            await close_models()

    result, reply = asyncio.run(invoke())
    calls = reply.get_content_blocks("tool_call")
    assert {call.name for call in calls} >= {
        "get_weather",
        "calculate_trip_budget",
    }
    assert "杭州" in (result.get_text_content() or "")
    assert "4800" in (result.get_text_content() or "")


def test_team_pipeline_delegates_to_multiple_members(provider_env):
    from agentscope.event import ToolCallStartEvent
    from agentscope.pipeline import TeamMember, TeamPipeline
    from agentscope.tool import FunctionTool

    async def invoke():
        leader = build_agent(name="总监", system_prompt="拆分并汇总任务。")
        researcher = build_agent(
            name="研究员",
            system_prompt="查询天气与攻略。",
            tools=[FunctionTool(get_weather, is_read_only=True)],
        )
        budget = build_agent(
            name="预算员",
            system_prompt="计算预算。",
            tools=[FunctionTool(calculate_trip_budget, is_read_only=True)],
        )
        reviewer = build_agent(name="审查员", system_prompt="审查方案。")
        pipeline = TeamPipeline(
            leader,
            [
                TeamMember(agent=researcher, description="目的地研究"),
                TeamMember(agent=budget, description="预算计算"),
                TeamMember(agent=reviewer, description="方案审查"),
            ],
        )
        names = []
        text = ""
        async for event in pipeline.reply_stream(
            UserMsg(name="user", content="制定杭州两日游方案"),
        ):
            if isinstance(event, ToolCallStartEvent):
                names.append(event.tool_call_name)
            from agentscope.event import TextBlockDeltaEvent

            if isinstance(event, TextBlockDeltaEvent):
                text += event.delta
        await close_models()
        return names, text

    names, text = asyncio.run(invoke())
    assert names.count("TeamAssign") == 3
    assert "get_weather" in names
    assert "calculate_trip_budget" in names
    assert text


@pytest.fixture
def web_client(provider_env, monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "business.sqlite3"))
    monkeypatch.setenv("SEED_DEMO_DATA", "true")
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    monkeypatch.setenv("ACCESS_TOKEN_MINUTES", "60")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "Admin-test-password-123")
    monkeypatch.setenv("DEMO_USERNAME", "demo")
    monkeypatch.setenv("DEMO_USER_PASSWORD", "Demo-test-password-123")
    import web_app

    with TestClient(web_app.app) as client:
        yield client


def login(client, username="demo", password="Demo-test-password-123"):
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create_web_session(client, headers):
    response = client.post("/api/sessions", json={"title": "测试会话"}, headers=headers)
    assert response.status_code == 200
    return response.json()["id"]


def event_data(body: str, event_name: str) -> list[dict]:
    result = []
    for block in body.split("\n\n"):
        lines = block.splitlines()
        if f"event: {event_name}" not in lines:
            continue
        data_line = next((line for line in lines if line.startswith("data:")), None)
        if data_line:
            result.append(json.loads(data_line[5:].strip()))
    return result


def test_password_hashing_and_token_validation(monkeypatch):
    from auth import create_access_token, decode_access_token, hash_password, verify_password

    monkeypatch.setenv("AUTH_JWT_SECRET", "token-test-secret-" + "x" * 40)
    encoded = hash_password("correct-password")
    assert verify_password("correct-password", encoded)
    assert not verify_password("wrong-password", encoded)
    token, _ = create_access_token({"id": "u1", "username": "demo", "role": "user"})
    assert decode_access_token(token)["sub"] == "u1"


def test_web_app_requires_auth_and_streams_business_tools(web_client):
    assert web_client.get("/api/health").json()["auth_required"] is True
    assert web_client.post(
        "/api/chat",
        json={"session_id": "a" * 24, "message": "查询订单"},
    ).status_code == 401
    headers = login(web_client)
    session_id = create_web_session(web_client, headers)
    response = web_client.post(
        "/api/chat",
        headers=headers,
        json={"session_id": session_id, "message": "查询我的全部订单"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: tool_start" in body
    assert "list_my_orders" in body
    assert "event: final" in body
    assert "SO-DEMO-1001" in body

    import web_app

    web_app.AGENTS.clear()
    restored = web_client.get(f"/api/sessions/{session_id}", headers=headers)
    assert restored.status_code == 200
    assert any("SO-DEMO-1001" in item["text"] for item in restored.json()["messages"])
    follow_up = web_client.post(
        "/api/chat",
        headers=headers,
        json={"session_id": session_id, "message": "查看我的未关闭工单"},
    )
    assert follow_up.status_code == 200
    assert "list_my_tickets" in follow_up.text
    assert "event: final" in follow_up.text


def test_sessions_are_isolated_between_users(web_client):
    demo_headers = login(web_client)
    session_id = create_web_session(web_client, demo_headers)
    admin_headers = login(web_client, "admin", "Admin-test-password-123")
    assert web_client.get(f"/api/sessions/{session_id}", headers=admin_headers).status_code == 404
    assert web_client.delete(f"/api/sessions/{session_id}", headers=admin_headers).status_code == 404
    assert web_client.get("/api/admin/audit", headers=demo_headers).status_code == 403
    assert web_client.get("/api/admin/audit", headers=admin_headers).status_code == 200


def test_business_tools_are_persistent_and_user_scoped(web_client):
    from business import (
        close_ticket,
        create_ticket,
        get_user_by_username,
        list_orders,
        list_tickets,
    )

    async def invoke():
        demo = await get_user_by_username("demo")
        admin = await get_user_by_username("admin")
        assert len(await list_orders(demo["id"])) == 3
        assert await list_orders(admin["id"]) == []
        ticket = await create_ticket(demo["id"], "测试工单", "验证数据隔离", "normal")
        with pytest.raises(LookupError):
            await close_ticket(admin["id"], ticket["ticket_id"])
        assert len(await list_tickets(demo["id"])) == 2
        return demo

    asyncio.run(invoke())


def test_write_tool_waits_for_confirmation_and_is_audited(web_client):
    from business import list_audit_logs, list_tickets, get_user_by_username

    headers = login(web_client)
    session_id = create_web_session(web_client, headers)
    paused = web_client.post(
        "/api/chat",
        headers=headers,
        json={"session_id": session_id, "message": "为账号登录问题创建一个高优先级工单"},
    )
    assert paused.status_code == 200
    approvals = event_data(paused.text, "approval_required")
    assert len(approvals) == 1
    call = approvals[0]["tool_calls"][0]
    assert call["name"] == "create_support_ticket"
    paused_final = event_data(paused.text, "final")
    assert paused_final[0]["finished_reason"] == "paused"

    async def ticket_count():
        demo = await get_user_by_username("demo")
        return len(await list_tickets(demo["id"]))

    assert asyncio.run(ticket_count()) == 1
    resumed = web_client.post(
        "/api/confirm",
        headers=headers,
        json={
            "session_id": session_id,
            "reply_id": approvals[0]["reply_id"],
            "decisions": [{"tool_call_id": call["id"], "confirmed": True}],
        },
    )
    assert resumed.status_code == 200
    assert "event: final" in resumed.text
    assert "ticket_id" in resumed.text
    assert asyncio.run(ticket_count()) == 2

    async def actions():
        return {item["action"] for item in await list_audit_logs()}

    assert {"confirm_tools", "create_ticket"} <= asyncio.run(actions())


def test_web_app_returns_clear_configuration_error(web_client, monkeypatch):
    monkeypatch.setenv("AGENT_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    headers = login(web_client)
    session_id = create_web_session(web_client, headers)
    response = web_client.post(
        "/api/chat",
        headers=headers,
        json={"session_id": session_id, "message": "你好"},
    )
    assert response.status_code == 503
    assert "API Key" in response.json()["detail"]
