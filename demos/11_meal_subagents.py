from __future__ import annotations

import argparse
import json

from demos.common import run
from meal_orchestration import run_meal_plan


async def main() -> None:
    parser = argparse.ArgumentParser(description="串行子 Agent 编排示例")
    parser.add_argument(
        "message",
        nargs="?",
        default="为 4 位成人规划 3 天菜单，其中一人对花生过敏，每天餐饮预算 120 元。",
    )
    args = parser.parse_args()
    result = await run_meal_plan(args.message, tenant_name="学习演示租户")
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run(main())
