from threading import Event
import unittest
from unittest.mock import Mock

from flight_api import (FlightAPIError, MonitorCancelled, RetryableFlightError,
                        build_payload, fetch_flight_prices, parse_api_date, parse_prices)
from settings import normalize_config
from tests.helpers import clock, config, response


def data(rows):
    return {"responseStatus": {"Ack": "Success", "Errors": []}, "priceList": rows}


class FlightAPITests(unittest.TestCase):
    def test_payload_combinations_and_cabin(self):
        for region, way, flag in ((1, "Oneway", 0), (2, "Oneway", 1),
                                  (1, "Roundtrip", 4), (2, "Roundtrip", 5)):
            with self.subTest(region=region, way=way):
                cfg = normalize_config(config(searchType=region, flightWay=way,
                                              returnDate="20991112", grade=4))
                payload = build_payload(cfg)
                self.assertEqual(payload["flag"], flag)
                self.assertEqual(payload["calendarSelections"][0],
                                 {"selectionType": 8, "selectionContent": ["4"]})
                self.assertEqual("returnDate" in payload, way == "Roundtrip")
                self.assertNotIn("direct", payload)

    def test_total_price_only_and_missing_values_are_not_zero(self):
        cfg = normalize_config(config(dateToGo=["20991105", "20991106", "20991107"]))
        prices = parse_prices(data([
            {"departDate": "2099-11-05", "price": 510, "totalPrice": 630},
            {"departDate": "2099-11-05", "price": 500, "totalPrice": 620},
            {"departDate": "2099-11-06", "price": 100, "totalPrice": 0},
            {"departDate": "2099-11-07", "price": 100},
            {"departDate": "2099-11-08", "totalPrice": 50},
        ]), cfg)
        self.assertEqual(prices, {"20991105": 620})

    def test_invalid_total_prices_and_business_failure_are_rejected(self):
        cfg = normalize_config(config())
        for price in (-1, True, "630", float("nan"), float("inf")):
            with self.subTest(price=price), self.assertRaises(FlightAPIError):
                parse_prices(data([{"departDate": "2099-11-05", "totalPrice": price}]), cfg)
        with self.assertRaises(FlightAPIError):
            parse_prices({"responseStatus": {"Ack": "Failure"}, "priceList": []}, cfg)

    def test_roundtrip_requires_exact_return_pair(self):
        cfg = normalize_config(config(flightWay="Roundtrip", returnDate="20991112"))
        prices = parse_prices(data([
            {"departDate": "2099-11-05", "totalPrice": 100},
            {"departDate": "2099-11-05", "returnDate": "2099-11-11", "totalPrice": 200},
            {"departDate": "2099-11-05", "returnDate": "2099-11-12", "totalPrice": 900},
        ]), cfg)
        self.assertEqual(prices, {"20991105": 900})

    def test_dotnet_and_iso_dates_use_china_timezone(self):
        self.assertEqual(parse_api_date("/Date(1793808000000+0800)/"), "20261105")
        self.assertEqual(parse_api_date("2026-11-04T16:00:00Z"), "20261105")

    def test_http_errors_have_explicit_retry_policy(self):
        for status in (429, 500, 503):
            with self.subTest(status=status), self.assertRaises(RetryableFlightError):
                fetch_flight_prices(config(), post=Mock(return_value=response(status=status)))
        for status in (400, 403, 404):
            with self.subTest(status=status), self.assertRaises(FlightAPIError) as caught:
                fetch_flight_prices(config(), post=Mock(return_value=response(status=status)))
            self.assertNotIsInstance(caught.exception, RetryableFlightError)

    def test_retry_after_is_parsed_only_for_rate_limiting(self):
        for status, headers, expected in (
            (429, {"retry-after": " 45 "}, 45),
            (429, {"Retry-After": "Tue, 29 Sep 2026 04:01:00 GMT"}, 60),
            (429, {"Retry-After": "Tue, 29 Sep 2026 04:01:00"}, 60),
            (429, {}, None),
            (429, [], None),
            (429, {"Retry-After": "0"}, None),
            (429, {"Retry-After": "-1"}, None),
            (429, {"Retry-After": "1.5"}, None),
            (429, {"Retry-After": "Tue, 29 Sep 2026 03:59:00 GMT"}, None),
            (429, {"Retry-After": "invalid"}, None),
            (503, {"Retry-After": "45"}, None),
        ):
            with self.subTest(status=status, headers=headers):
                reply = response(status=status, headers=headers)
                with self.assertRaises(RetryableFlightError) as caught:
                    fetch_flight_prices(config(), post=Mock(return_value=reply),
                                        clock=lambda: clock().replace(microsecond=500000))
                self.assertEqual(caught.exception.status_code, status)
                self.assertEqual(caught.exception.retry_after, expected)

    def test_non_json_response_is_explicit_error(self):
        reply = response()
        reply.json.side_effect = ValueError("HTML")
        with self.assertRaises(FlightAPIError):
            fetch_flight_prices(config(), post=Mock(return_value=reply))

    def test_cancel_prevents_request_or_discards_response(self):
        stop = Event()
        stop.set()
        post = Mock()
        with self.assertRaises(MonitorCancelled):
            fetch_flight_prices(config(), stop, post=post)
        post.assert_not_called()
        stop.clear()

        def cancel(*args, **kwargs):
            stop.set()
            return response(data([]))

        with self.assertRaises(MonitorCancelled):
            fetch_flight_prices(config(), stop, post=cancel)

    def test_roundtrip_filters_each_departure_and_returns_missing_dates(self):
        cfg = config(dateToGo=["20991105", "20991106"], flightWay="Roundtrip", returnDate="20991112")
        post = Mock(side_effect=[response(data([
            {"departDate": "2099-11-05", "returnDate": "2099-11-12", "totalPrice": 900},
            {"departDate": "2099-11-06", "returnDate": "2099-11-12", "totalPrice": 100},
        ])), response(data([]))])
        result = fetch_flight_prices(cfg, post=post, clock=clock)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(result.prices, {"20991105": 900})
        self.assertEqual(result.missing_dates, ["20991106"])
        self.assertEqual(result.observed_at, clock().isoformat(timespec="seconds"))
