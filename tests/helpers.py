from copy import deepcopy
from datetime import datetime
from unittest.mock import Mock

from flight_api import CalendarResult
from settings import CHINA


def config(**overrides):
    data = {"dateToGo": ["20991105"], "placeFrom": "KMG", "placeTo": "TNA",
            "flightWay": "Oneway", "searchType": 1, "grade": 1,
            "sleepTime": 600, "priceStep": 50, "SCKEY": "test-token"}
    data.update(overrides)
    return deepcopy(data)


def calendar(prices=None, days=None):
    prices = {"20991105": 630} if prices is None else prices
    days = ["20991105"] if days is None else days
    return CalendarResult(prices, [day for day in days if day not in prices],
                          "2026-09-29T12:00:00+08:00")


def clock():
    return datetime(2026, 9, 29, 12, tzinfo=CHINA)


def response(data=None, status=200, headers=None):
    return Mock(status_code=status, json=Mock(return_value=data),
                headers={} if headers is None else headers)
