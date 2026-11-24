"""携程 15380 日历请求与含税报价解析。"""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import re
from typing import Dict, Optional

import requests

from ctrip_headers import HEADERS
from settings import CHINA, display_date, normalize_config, normalize_date


BASE_URL = ("https://m.ctrip.com/restapi/soa2/15380/bjjson/"
            "FlightIntlAndInlandLowestPriceSearch")
REQUEST_TIMEOUT = 25


class FlightAPIError(Exception):
    """业务错误或响应格式变化，需要人工检查后重新启动。"""


class RetryableFlightError(FlightAPIError):
    """服务器限流或临时不可用，可以稍后重试。"""

    def __init__(self, message: str, *, status_code: Optional[int] = None,
                 retry_after: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class MonitorCancelled(Exception):
    """用户停止监控，不作为接口错误处理。"""


def build_payload(config: dict, departure: Optional[str] = None) -> dict:
    """输入为已规范化配置；不传递旧 API 的任何筛选字段。"""
    international = config["searchType"] == 2
    roundtrip = config["flightWay"] == "Roundtrip"
    selections = [{"selectionType": 8, "selectionContent": [str(config["grade"])]}]
    if international:
        selections.append({"selectionType": 2, "selectionContent": ["1"]})
    payload = {
        "searchType": config["searchType"],
        "departNewCityCode": config["placeFrom"],
        "arriveNewCityCode": config["placeTo"],
        "startDate": display_date(departure or min(config["dateToGo"])),
        "passengerList": deepcopy(config["passengerList"]),
        "grade": config["grade"], "calendarSelections": selections,
        "flag": (4 if roundtrip else 0) + int(international),
        "channelName": "MobileH5", "lowPriceExtendInfo": [],
    }
    if roundtrip:
        payload["returnDate"] = display_date(config["returnDate"])
    return payload


def parse_api_date(value) -> str:
    match = re.fullmatch(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/", str(value))
    try:
        if match:
            return datetime.fromtimestamp(int(match[1]) / 1000, CHINA).strftime("%Y%m%d")
        if isinstance(value, str):
            # 接受 ISO 日期或 ISO 日期时间，不接受任意尾缀。
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                return normalize_date(value)
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if timestamp.tzinfo:
                timestamp = timestamp.astimezone(CHINA)
            return timestamp.strftime("%Y%m%d")
    except (ValueError, OverflowError, OSError) as exc:
        raise FlightAPIError("接口返回无效日期") from exc
    raise FlightAPIError("接口缺少有效日期")


def parse_prices(data, config: dict) -> Dict[str, float]:
    if not isinstance(data, dict):
        raise FlightAPIError("接口响应应为 JSON 对象")
    status = data.get("responseStatus", data.get("ResponseStatus"))
    if not isinstance(status, dict) or status.get("Ack") != "Success" or status.get("Errors"):
        raise FlightAPIError("携程日历接口返回业务错误或缺少成功状态")
    rows = data.get("priceList")
    if not isinstance(rows, list):
        raise FlightAPIError("接口响应缺少 priceList 列表")
    targets = set(config["dateToGo"])
    prices = {}
    for row in rows:
        if not isinstance(row, dict):
            raise FlightAPIError("priceList 中存在无效记录")
        day = parse_api_date(row.get("departDate"))
        if day not in targets:
            continue
        if config["flightWay"] == "Roundtrip":
            # 无法确认返程配对时绝不把单程价当作往返价。
            if not row.get("returnDate") or parse_api_date(row["returnDate"]) != config["returnDate"]:
                continue
        total = row.get("totalPrice")
        if total is None or (type(total) in (int, float) and total == 0):
            continue
        if type(total) not in (int, float) or not math.isfinite(total) or total < 0:
            raise FlightAPIError("接口返回无效 totalPrice，已停止本轮价格比较")
        prices[day] = min(prices.get(day, total), total)
    return prices


@dataclass
class CalendarResult:
    prices: Dict[str, float]
    missing_dates: list
    observed_at: str
    source: str = "ctrip_15380"
    currency: str = "CNY"
    price_kind: str = "tax_inclusive"


def _parse_retry_after(headers, clock) -> Optional[int]:
    if not isinstance(headers, Mapping):
        return None
    value = next((value for name, value in headers.items()
                  if isinstance(name, str) and name.lower() == "retry-after"), None)
    if not isinstance(value, str):
        return None
    value = value.strip()
    try:
        if re.fullmatch(r"[0-9]+", value):
            return int(value) or None
        retry_at = parsedate_to_datetime(value)
    except (ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    now = clock()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    seconds = (retry_at - now).total_seconds()
    return math.ceil(seconds) if seconds > 0 else None


def fetch_flight_prices(config: dict, stop_event=None, *, post=None, clock=None) -> CalendarResult:
    # 日期的动态过期由监控服务处理，避免一次查询跨午夜时被重复校验拒绝。
    config = normalize_config(config, allow_expired=True)
    post = post or requests.post
    clock = clock or (lambda: datetime.now(CHINA))
    departures = config["dateToGo"] if config["flightWay"] == "Roundtrip" else [None]
    prices = {}
    for departure in departures:
        if stop_event is not None and stop_event.is_set():
            raise MonitorCancelled("查询已停止")
        response = post(BASE_URL, json=build_payload(config, departure),
                        headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if stop_event is not None and stop_event.is_set():
            raise MonitorCancelled("查询已停止")
        if response.status_code == 429:
            retry_after = _parse_retry_after(getattr(response, "headers", None), clock)
            raise RetryableFlightError(f"携程暂时不可用（HTTP {response.status_code}）",
                                       status_code=429, retry_after=retry_after)
        if 500 <= response.status_code < 600:
            raise RetryableFlightError(f"携程暂时不可用（HTTP {response.status_code}）",
                                       status_code=response.status_code)
        if response.status_code != 200:
            raise FlightAPIError(f"携程日历请求失败（HTTP {response.status_code}）")
        try:
            data = response.json()
        except ValueError as exc:
            raise FlightAPIError("携程返回非 JSON 内容，可能是访问拦截") from exc
        # 往返每个请求只取本次去程对应的配对，避免响应之间互相覆盖。
        request_config = dict(config, dateToGo=[departure]) if departure else config
        prices.update(parse_prices(data, request_config))
    return CalendarResult(prices=prices,
                          missing_dates=[day for day in config["dateToGo"] if day not in prices],
                          observed_at=clock().isoformat(timespec="seconds"))
