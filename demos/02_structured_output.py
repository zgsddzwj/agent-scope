from __future__ import annotations

import argparse
import json

from pydantic import BaseModel, Field

from agentscope.message import UserMsg

from demos.common import create_model, run


class TravelBrief(BaseModel):
    destination: str = Field(description="目的地")
    days: int = Field(ge=1, le=14, description="行程天数")
    budget_cny: int = Field(ge=0, description="总预算，人民币元")
    activities: list[str] = Field(description="每日活动安排")
    reason: str = Field(description="安排理由")


async def main() -> None:
    parser = argparse.ArgumentParser(description="让模型返回经过校验的 JSON 结构")
    parser.add_argument("--destination", default="杭州")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--budget", type=int, default=5000)
    args = parser.parse_args()

    model = create_model(stream=False)
    response = await model.generate_structured_output(
        messages=[
            UserMsg(
                name="user",
                content=(
                    f"为{args.destination}制定 {args.days} 天旅行草案，总预算 {args.budget} 元。"
                    "只做规划，不要查询实时价格。"
                ),
            ),
        ],
        structured_model=TravelBrief,
    )
    validated = TravelBrief.model_validate(response.content)
    print(json.dumps(validated.model_dump(), ensure_ascii=False, indent=2))
    print("用量：", response.usage)


if __name__ == "__main__":
    run(main)
