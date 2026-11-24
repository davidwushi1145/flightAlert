from dataclasses import replace
from datetime import datetime
from queue import Queue
from threading import Event, Thread
import unittest
from unittest.mock import Mock, patch

import requests

from flight_api import FlightAPIError, MonitorCancelled, RetryableFlightError
from monitoring import MonitorService, run_monitor
from notifications import NotificationResult
from settings import CHINA
from tests.helpers import calendar, clock, config


class MonitoringTests(unittest.TestCase):
    def service(self, **kwargs):
        options = dict(fetch=Mock(return_value=calendar()),
                       notify=Mock(return_value=NotificationResult(True)), clock=clock)
        options.update(kwargs)
        return MonitorService(config(), **options)

    def test_failed_notification_is_retried_then_acknowledged(self):
        notify = Mock(side_effect=[NotificationResult(True), NotificationResult(False, "timeout"),
                                   NotificationResult(True)])
        fetch = Mock(side_effect=[calendar(), calendar({"20991105": 580}),
                                  calendar({"20991105": 580}), calendar({"20991105": 580})])
        service = self.service(fetch=fetch, notify=notify)
        event = Event()
        service.check_once(event)
        service.check_once(event)
        self.assertEqual(service.tracker.baselines["20991105"], 630)
        self.assertEqual(service.tracker.latest_prices["20991105"], 580)
        self.assertIn("20991105", service.tracker.pending)
        service.check_once(event)
        service.check_once(event)
        self.assertEqual(notify.call_count, 3)
        self.assertEqual(service.tracker.baselines["20991105"], 580)
        self.assertFalse(service.tracker.pending)

    def test_initial_notification_failure_is_retried_on_missing_quote(self):
        service = self.service(fetch=Mock(side_effect=[calendar(), calendar({})]),
                               notify=Mock(side_effect=[NotificationResult(False, "offline"),
                                                        NotificationResult(True)]))
        service.check_once(Event())
        service.check_once(Event())
        self.assertEqual(service.notify.call_count, 2)
        self.assertIn("2026-09-29T12:00:00+08:00", service.notify.call_args.args[0])

    def test_batch_failure_preserves_old_and_new_changes_until_retry_succeeds(self):
        days = ["20991106", "20991105"]
        old = calendar({"20991106": 650}, days=days)
        new = replace(calendar({"20991105": 630}, days=days),
                      observed_at="2026-09-29T12:10:00+08:00")
        failure = NotificationResult(False, "offline")
        notify = Mock(side_effect=[failure, failure, NotificationResult(True)])
        service = MonitorService(config(dateToGo=days), clock=clock, notify=notify,
                                 fetch=Mock(side_effect=[old, new, calendar({}, days=days)]))
        stop = Event()
        service.check_once(stop)

        result = service.check_once(stop)
        self.assertEqual(notify.call_count, 2)
        self.assertEqual(len(result.notifications), 1)
        attempt = result.notifications[0]
        self.assertEqual(attempt.outcome, failure)
        self.assertEqual(attempt.message.split("\n\n"), [
            f"首次提醒: KMG → TNA，出发 2099-11-05，含税日历价 CNY 630\n报价采集时间: {new.observed_at}",
            f"首次提醒: KMG → TNA，出发 2099-11-06，含税日历价 CNY 650\n报价采集时间: {old.observed_at}",
        ])
        self.assertEqual(set(service.tracker.pending), set(days))
        self.assertFalse(service.tracker.baselines)

        result = service.check_once(stop)
        self.assertEqual(notify.call_count, 3)
        self.assertEqual(len(result.notifications), 1)
        self.assertTrue(result.notifications[0].outcome.success)
        self.assertEqual(result.notifications[0].message, attempt.message)
        self.assertEqual(service.tracker.baselines, {"20991105": 630, "20991106": 650})
        self.assertFalse(service.tracker.pending)

    def test_rebound_cancels_pending_drop_instead_of_sending_stale_price(self):
        service = self.service(fetch=Mock(side_effect=[calendar(), calendar({"20991105": 580}),
                                                      calendar({"20991105": 620})]),
                               notify=Mock(side_effect=[NotificationResult(True),
                                                        NotificationResult(False, "offline")]))
        for _ in range(3):
            service.check_once(Event())
        self.assertEqual(service.notify.call_count, 2)
        self.assertFalse(service.tracker.pending)
        self.assertEqual(service.tracker.baselines["20991105"], 630)

    def test_small_changes_accumulate_against_confirmed_baseline(self):
        service = self.service(fetch=Mock(side_effect=[calendar(), calendar({"20991105": 610}),
                                                      calendar({"20991105": 580})]))
        for _ in range(3):
            service.check_once(Event())
        self.assertEqual(service.notify.call_count, 2)
        self.assertIn("630 → CNY 580", service.notify.call_args.args[0])

    def test_no_token_records_prices_without_pending_notifications(self):
        prices = {"20991105": 630, "20991106": 650}
        service = MonitorService(config(dateToGo=list(prices), SCKEY=""),
                                 fetch=Mock(return_value=calendar(prices)),
                                 notify=Mock(), clock=clock)
        result = service.check_once(Event())
        service.notify.assert_not_called()
        self.assertFalse(result.notifications)
        self.assertEqual(service.tracker.baselines, prices)
        self.assertFalse(service.tracker.pending)

    def test_midnight_expires_only_old_dates_and_finishes_when_all_expired(self):
        moments = [datetime(2026, 9, day, 12, tzinfo=CHINA) for day in (29, 30)]
        moments.append(datetime(2026, 10, 1, tzinfo=CHINA))
        fetch = Mock(side_effect=[calendar({"20260929": 630, "20260930": 650}),
                                  calendar({"20260930": 640})])
        service = MonitorService(config(dateToGo=["20260929", "20260930"], SCKEY=""),
                                 clock=Mock(side_effect=moments), fetch=fetch)
        service.check_once(Event())
        second = service.check_once(Event())
        self.assertEqual(second.expired_dates, ["20260929"])
        self.assertEqual(fetch.call_args.args[0]["dateToGo"], ["20260930"])
        self.assertNotIn("20260929", service.tracker.baselines)
        self.assertTrue(service.check_once(Event()).finished)
        self.assertEqual(fetch.call_count, 2)

    def test_stop_during_fetch_prevents_notifications(self):
        stop = Event()

        def fetch(*args, **kwargs):
            stop.set()
            return calendar()

        service = self.service(fetch=fetch)
        with self.assertRaises(MonitorCancelled):
            service.check_once(stop)
        service.notify.assert_not_called()

    def test_stop_during_notification_records_and_acknowledges_batch(self):
        stop = Event()

        def notify(*args):
            stop.set()
            return NotificationResult(True)

        service = MonitorService(config(dateToGo=["20991105", "20991106"]), clock=clock,
                                 fetch=Mock(return_value=calendar({"20991105": 630, "20991106": 650})),
                                 notify=Mock(side_effect=notify))
        events = []
        self.assertEqual(run_monitor(service, stop, events.append), 0)
        self.assertEqual(service.notify.call_count, 1)
        result, = [event.data for event in events if event.kind == "result"]
        attempt, = result.notifications
        self.assertTrue(attempt.outcome.success)
        self.assertEqual(attempt.message, service.notify.call_args.args[0])
        self.assertTrue(any(event.kind == "log" and event.data.startswith("通知已发送")
                            for event in events))
        self.assertFalse(any(event.kind == "waiting" for event in events))
        self.assertEqual(service.tracker.baselines, {"20991105": 630, "20991106": 650})
        self.assertFalse(service.tracker.pending)

    def test_retryable_errors_retry_and_wait_is_interruptible(self):
        for error, delay in (
            (requests.Timeout("timeout"), 30),
            (requests.ConnectionError("offline"), 30),
            (RetryableFlightError("HTTP 503", status_code=503), 30),
            (RetryableFlightError("temporary error"), 30),
            (RetryableFlightError("HTTP 429", status_code=429, retry_after=75), 75),
            (RetryableFlightError("HTTP 429", status_code=429), config()["sleepTime"]),
            (RetryableFlightError("HTTP 429", status_code=429, retry_after=0),
             config()["sleepTime"]),
        ):
            with self.subTest(error=error):
                service = self.service(fetch=Mock(side_effect=error))
                stop = Event()
                events = []

                def emit(event):
                    events.append(event)
                    if event.kind == "waiting":
                        stop.set()

                with patch("monitoring.time.monotonic", return_value=0):
                    self.assertEqual(run_monitor(service, stop, emit), 0)
                self.assertEqual(service.fetch.call_count, 1)
                self.assertEqual([e.data for e in events if e.kind == "waiting"], [(delay, True)])
                self.assertEqual([e.data for e in events if e.kind == "error"],
                                 [f"查询暂时失败: {error}；{delay} 秒后重试"])
                self.assertEqual(events[-1].kind, "finished")

    def test_once_reports_retryable_error_without_waiting(self):
        for error in (requests.Timeout("timeout"), requests.ConnectionError("offline"),
                      RetryableFlightError("HTTP 503", status_code=503),
                      RetryableFlightError("HTTP 429", status_code=429, retry_after=75)):
            with self.subTest(error=error):
                service = self.service(fetch=Mock(side_effect=error))
                events = []
                self.assertEqual(run_monitor(service, Event(), events.append, once=True), 1)
                self.assertEqual(service.fetch.call_count, 1)
                self.assertFalse(any(e.kind == "waiting" for e in events))
                self.assertEqual([e.data for e in events if e.kind == "error"],
                                 [f"查询暂时失败: {error}"])
                self.assertEqual((events[-1].kind, events[-1].data), ("finished", "本轮查询失败"))

    def test_business_format_and_programming_errors_stop_without_retry(self):
        for error in (FlightAPIError("invalid priceList"), ValueError("bad config"), KeyError("bug")):
            with self.subTest(error=error):
                service = self.service(fetch=Mock(side_effect=error))
                events = []
                self.assertEqual(run_monitor(service, Event(), events.append), 1)
                self.assertFalse(any(e.kind == "waiting" for e in events))
                self.assertEqual(events[-1].kind, "finished")

    def test_once_reports_missing_quotes_or_failed_notifications(self):
        for service in (self.service(fetch=Mock(return_value=calendar({}))),
                        self.service(notify=Mock(return_value=NotificationResult(False, "failed")))):
            self.assertEqual(run_monitor(service, Event(), lambda e: None, once=True), 1)

    def test_worker_can_publish_to_queue_without_gui(self):
        events = Queue()
        worker = Thread(target=run_monitor, args=(self.service(), Event(), events.put),
                        kwargs={"once": True})
        worker.start()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        received = list(events.queue)
        self.assertIn("result", [event.kind for event in received])
        self.assertEqual(received[-1].kind, "finished")
