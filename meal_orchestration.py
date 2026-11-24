from __future__ import annotations

import asyncio
import os
from typing import Any, Literal

from pydantic import BaseModel, Field

from agentscope.message import UserMsg

from demos.common import build_agent, create_model


class MealRoute(BaseModel):
    intent: Literal["meal_planning", "general"] = Field(description="请求意图")
    reason: str = Field(description="路由判断依据")


class MealStage(BaseModel):
    name: str
    agent: str
    status: Literal["completed", "failed"]
    output: str


class MealPlanResult(BaseModel):
    tenant_name: str
    route: MealRoute
    stages: list[MealStage]
    final: str


STAGE_OUTPUT_RULE = "只输出本阶段结果，不向用户追问，不表示等待确认。"


async def close_model(model: Any) -> None:
    client = getattr(model, "client", None)
    if client is not None:
        await client.close()


async def run_meal_plan(message: str, tenant_name: str = "默认租户") -> MealPlanResult:
    timeout = int(os.getenv("SUBAGENT_STAGE_TIMEOUT", "90"))
    router_model = create_model(stream=False, track=False)
    worker_model = create_model(stream=True, track=False)
    stages: list[MealStage] = []

    async def stage(name: str, agent: Any, operation: Any) -> Any:
        try:
            result = await asyncio.wait_for(operation, timeout=timeout)
        except Exception as error:
            stages.append(
                MealStage(
                    name=name,
                    agent=agent.name,
                    status="failed",
                    output="阶段执行失败。",
                ),
            )
            raise RuntimeError(f"子 Agent 阶段失败：{name}") from error
        stages.append(
            MealStage(
                name=name,
                agent=agent.name,
                status="completed",
                output=result.get_text_content() or "",
            ),
        )
        return result

    try:
        router = build_agent(
            name="需求路由",
            system_prompt=(
                "你负责判断请求是否属于食谱或用餐规划。只做路由，不回答业务问题，"
                "也不要访问任何租户数据。"
            ),
            stream=False,
            model=router_model,
        )
        route_message = await stage(
            "route",
            router,
            router.reply(
                UserMsg(name="user", content=message),
                structured_schema=MealRoute,
            ),
        )
        try:
            route = MealRoute.model_validate(route_message.structured_output)
        except (TypeError, ValueError) as error:
            raise RuntimeError("需求路由未返回有效的结构化结果。") from error
        if route.intent == "general":
            return MealPlanResult(
                tenant_name=tenant_name,
                route=route,
                stages=stages,
                final=route_message.get_text_content() or "该请求不属于食谱规划。",
            )

        requirements_agent = build_agent(
            name="需求整理",
            system_prompt=(
                "你负责提炼人数、天数、预算、口味、过敏和忌口等约束。"
                "只整理明确信息，缺失项标记为待确认，不编造食谱。"
                + STAGE_OUTPUT_RULE
            ),
            stream=False,
            model=worker_model,
        )
        planner_agent = build_agent(
            name="菜单草案",
            system_prompt=(
                "你负责生成餐次和菜品结构草案，不实现营养计算或食谱算法。"
                "必须遵守需求约束，并明确哪些内容需要后续算法或人工补充。"
                + STAGE_OUTPUT_RULE
            ),
            stream=False,
            model=worker_model,
            multi_agent=True,
        )
        reviewer_agent = build_agent(
            name="方案审查",
            system_prompt=(
                "你负责检查草案是否覆盖约束、是否存在过敏冲突和明显缺项。"
                "只输出需要修正的问题，不替用户做最后决定。"
                + STAGE_OUTPUT_RULE
            ),
            stream=False,
            model=worker_model,
            multi_agent=True,
        )
        final_agent = build_agent(
            name="汇总输出",
            system_prompt=(
                "你负责汇总需求、草案和审查意见，给出清晰的下一步计划。"
                "说明当前仅验证 Agent 调用关系，具体食谱算法尚未实现。"
                "只输出文本，不得声称已创建文件、附件或外部链接。"
                + STAGE_OUTPUT_RULE
            ),
            stream=False,
            model=worker_model,
            multi_agent=True,
        )

        requirements = await stage(
            "requirements",
            requirements_agent,
            requirements_agent.reply(
                UserMsg(
                    name="user",
                    content=f"租户：{tenant_name}\n请整理以下请求的约束：\n{message}",
                ),
            ),
        )
        await planner_agent.observe(requirements)
        draft = await stage(
            "draft",
            planner_agent,
            planner_agent.reply(
                UserMsg(name="user", content="根据需求整理结果生成菜单结构草案。"),
            ),
        )
        await reviewer_agent.observe(draft)
        review = await stage(
            "review",
            reviewer_agent,
            reviewer_agent.reply(
                UserMsg(name="user", content="审查菜单结构草案并列出修正项。"),
            ),
        )
        await final_agent.observe([requirements, draft, review])
        final_message = await stage(
            "final",
            final_agent,
            final_agent.reply(
                UserMsg(name="user", content="汇总各阶段结果并给出最终输出。"),
            ),
        )
        return MealPlanResult(
            tenant_name=tenant_name,
            route=route,
            stages=stages,
            final=final_message.get_text_content() or "未生成最终输出。",
        )
    finally:
        await close_model(router_model)
        await close_model(worker_model)
