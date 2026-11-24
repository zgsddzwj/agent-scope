from __future__ import annotations

from agentscope.tool import FunctionTool, Toolkit

WEATHER = {
    "北京": "晴，26°C，东北风 2 级",
    "上海": "多云，24°C，东风 3 级",
    "杭州": "阵雨，22°C，东南风 2 级",
    "成都": "阴，20°C，微风",
    "深圳": "雷阵雨，29°C，南风 3 级",
}

GUIDES = {
    "杭州": ["西湖徒步", "龙井茶村", "运河夜游"],
    "北京": ["故宫", "长城", "胡同骑行"],
    "上海": ["外滩", "豫园", "浦东美术馆"],
    "成都": ["大熊猫基地", "宽窄巷子", "都江堰"],
    "深圳": ["大鹏半岛", "华侨城", "深圳湾公园"],
}


def get_weather(city: str) -> str:
    """查询模拟天气。返回指定城市的天气，仅用于演示，不是实时天气。

    Args:
        city: 城市中文名，例如杭州、北京、上海。
    """
    return WEATHER.get(city, f"{city} 暂无模拟天气数据")


def calculate_trip_budget(
    days: int,
    travelers: int,
    hotel_per_night: int,
    transport_per_person: int,
    daily_per_person: int,
) -> str:
    """计算旅行预算并返回费用明细。所有金额单位均为人民币元。

    Args:
        days: 旅行天数，必须大于 0。
        travelers: 出行人数，必须大于 0。
        hotel_per_night: 每晚住宿总价。
        transport_per_person: 每人大交通费用。
        daily_per_person: 每人每天餐饮、门票和市内交通预算。
    """
    values = [days, travelers, hotel_per_night, transport_per_person, daily_per_person]
    if any(value < 0 for value in values) or days == 0 or travelers == 0:
        raise ValueError("days/travelers 必须大于 0，所有金额不能为负数。")
    hotel = hotel_per_night * max(days - 1, 0)
    transport = transport_per_person * travelers
    daily = daily_per_person * days * travelers
    total = hotel + transport + daily
    return (
        f"住宿 {hotel} 元 + 大交通 {transport} 元 + 每日开销 {daily} 元 "
        f"= 总计 {total} 元；人均 {total / travelers:.0f} 元"
    )


def search_local_guide(city: str, interest: str) -> str:
    """搜索本地模拟旅行攻略，不会访问互联网。

    Args:
        city: 目的地城市中文名。
        interest: 兴趣偏好，例如历史文化、自然风光、美食。
    """
    suggestions = GUIDES.get(city, [])
    if not suggestions:
        return f"{city} 暂无模拟攻略。"
    return f"{city}适合{interest}的模拟推荐：{'、'.join(suggestions)}"


def make_travel_toolkit() -> Toolkit:
    return Toolkit(
        tools=[
            FunctionTool(get_weather, is_read_only=True),
            FunctionTool(calculate_trip_budget, is_read_only=True),
            FunctionTool(search_local_guide, is_read_only=True),
        ],
    )
