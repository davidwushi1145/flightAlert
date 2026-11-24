"""与界面无关的价格比较、通知确认及监控调度。"""

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
import logging
import math
import time
from typing import Optional

import requests

from flight_api import (CalendarResult, FlightAPIError, MonitorCancelled,
                        RetryableFlightError, fetch_flight_prices)
from notifications import NotificationResult, send_notification
from settings import CHINA, display_date, normalize_config

RETRY_DELAY = 30
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PriceChange:
    day: str
    price: float
    previous: Optional[float]
    observed_at: str


def format_notification(change: PriceChange, config: dict) -> str:
    label = (f"{config['placeFrom']} → {config['placeTo']}，"
             f"出发 {display_date(change.day)}")
    if config["flightWay"] == "Roundtrip":
        label += f"，返程 {display_date(config['returnDate'])}"
    if change.previous is None:
        message = f"首次提醒: {label}，含税日历价 CNY {change.price:g}"
    else:
        delta = change.price - change.previous
        direction = "上涨" if delta > 0 else "下降"
        message = (f"{label}，含税日历价{direction} CNY {abs(delta):g}"
                   f"（CNY {change.previous:g} → CNY {change.price:g}）")
    return f"{message}\n报价采集时间: {change.observed_at}"


class PriceTracker:
    """通知基准仅在确认后更新；待发送事件随最新有效报价替换或取消。"""

    def __init__(self, config: dict):
        self.config = deepcopy(config)
        self.baselines: dict[str, float] = {}
        self.latest_prices: dict[str, float] = {}
        self.pending: dict[str, PriceChange] = {}

    def observe(self, day: str, price: Optional[float], observed_at: str):
        if day not in self.config["dateToGo"] or type(price) not in (int, float):
            return
        if not math.isfinite(price) or price <= 0:
            return
        self.latest_prices[day] = price
        previous = self.baselines.get(day)
        if previous is not None and abs(price - previous) < self.config["priceStep"]:
            self.pending.pop(day, None)
            return
        self.pending[day] = PriceChange(day, price, previous, observed_at)

    def acknowledge(self, change: PriceChange):
        self.baselines[change.day] = change.price
        if self.pending.get(change.day) == change:
            del self.pending[change.day]

    def expire(self, days):
        for day in days:
            self.pending.pop(day, None)
            self.latest_prices.pop(day, None)
            self.baselines.pop(day, None)


@dataclass(frozen=True)
class NotificationAttempt:
    message: str
    outcome: NotificationResult


@dataclass
class CycleResult:
    calendar: Optional[CalendarResult]
    active_dates: list[str]
    expired_dates: list[str]
    notifications: list[NotificationAttempt] = field(default_factory=list)
    finished: bool = False


class MonitorService:
    def __init__(self, config: dict, *, fetch=None, notify=None, clock=None):
        self.clock = clock or (lambda: datetime.now(CHINA))
        self.config = normalize_config(config, allow_expired=True)
        self.fetch = fetch or fetch_flight_prices
        self.notify = notify or send_notification
        self.tracker = PriceTracker(self.config)
        self.expired_dates = set()

    def check_once(self, stop_event) -> CycleResult:
        if stop_event.is_set():
            raise MonitorCancelled()
        today = self.clock().astimezone(CHINA).strftime("%Y%m%d")
        active = [day for day in self.config["dateToGo"] if day >= today]
        expired = set(self.config["dateToGo"]) - set(active)
        newly_expired = sorted(expired - self.expired_dates)
        self.expired_dates = expired
        self.tracker.expire(expired)
        if not active:
            return CycleResult(None, [], newly_expired, finished=True)
        config = dict(self.config, dateToGo=active)
        calendar = self.fetch(config, stop_event=stop_event)
        if stop_event.is_set():
            raise MonitorCancelled()
        result = CycleResult(calendar, active, newly_expired)
        for day in active:
            self.tracker.observe(day, calendar.prices.get(day), calendar.observed_at)
        # 缺失报价不清除待发送消息；其中保留原始采集时间。
        changes = sorted(self.tracker.pending.values(), key=lambda change: change.day)
        if not changes:
            return result
        if stop_event.is_set():
            raise MonitorCancelled()
        if config["SCKEY"]:
            message = "\n\n".join(format_notification(change, config) for change in changes)
            outcome = self.notify(message, config["SCKEY"])
            result.notifications.append(NotificationAttempt(message, outcome))
            if not outcome.success:
                return result
        for change in changes:
            self.tracker.acknowledge(change)
        return result


@dataclass(frozen=True)
class MonitorEvent:
    kind: str
    data: object


def run_monitor(service: MonitorService, stop_event, emit, *, once=False) -> int:
    """CLI 和 GUI 共用调度；仅网络故障、限流和服务器暂时故障自动重试。"""
    finish_message = "监控已停止"

    def report(kind, data):
        if kind == "log":
            logger.info(data)
        elif kind == "error":
            logger.error(data)
        emit(MonitorEvent(kind, data))

    try:
        while not stop_event.is_set():
            report("phase", "正在查询含税日历价格")
            delay = service.config["sleepTime"]
            retrying = False
            try:
                result = service.check_once(stop_event)
                report("result", result)
                for day in result.expired_dates:
                    report("log", f"{display_date(day)} 已过期，停止查询该日期")
                if result.finished:
                    finish_message = "全部监控日期已过期，监控结束"
                    return 0
                for day in result.active_dates:
                    price = result.calendar.prices.get(day)
                    text = (f"CNY {price:g}" if price is not None
                            else "暂无有效含税报价（不代表无航班）")
                    report("log", f"{display_date(day)}: {text}")
                for attempt in result.notifications:
                    if attempt.outcome.success:
                        report("log", f"通知已发送: {attempt.message}")
                    else:
                        report("error", f"通知发送失败，保留待发送状态: {attempt.outcome.error}")
                if once:
                    failed = result.calendar.missing_dates or any(
                        not attempt.outcome.success for attempt in result.notifications)
                    return 1 if failed else 0
            except MonitorCancelled:
                return 0
            except (requests.Timeout, requests.ConnectionError, RetryableFlightError) as exc:
                if once:
                    report("error", f"查询暂时失败: {exc}")
                    finish_message = "本轮查询失败"
                    return 1
                delay = RETRY_DELAY
                if isinstance(exc, RetryableFlightError) and exc.status_code == 429:
                    delay = service.config["sleepTime"]
                    if exc.retry_after is not None and exc.retry_after > 0:
                        delay = exc.retry_after
                report("error", f"查询暂时失败: {exc}；{delay} 秒后重试")
                retrying = True
            deadline = time.monotonic() + delay
            while not stop_event.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                report("waiting", (math.ceil(remaining), retrying))
                if stop_event.wait(min(1, remaining)):
                    break
        return 0
    except (ValueError, FlightAPIError, requests.RequestException) as exc:
        finish_message = f"监控停止，请检查配置或接口: {exc}"
        report("error", finish_message)
        return 1
    except Exception as exc:
        finish_message = f"监控因程序错误停止: {exc}"
        logger.exception("监控发生未预期异常")
        report("error", finish_message)
        return 1
    finally:
        report("finished", finish_message)
