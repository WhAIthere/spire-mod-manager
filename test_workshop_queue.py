import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from core import Mod, atomic_json, read_json, workshop_nickname
from workshop_queue import run_title_queue, retry_delay


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "queue.json"
        self.now = 1000
        self.events = []

    def wait(self, seconds):
        self.now += seconds
        return False

    def run_queue(self, query, ids=("1", "2"), **kwargs):
        return run_title_queue("646570", ids, lambda *e: self.events.append(e), checkpoint=self.path,
                               query=query, clock=lambda: self.now, wait=self.wait, interval=lambda: 5, **kwargs)

    def entry(self, item, status="success", **kwargs):
        return dict(id=item, status=status, title="Workshop title" if status == "success" else "",
                    url=f"https://steamcommunity.com/sharedfiles/filedetails/?id={item}&l=schinese", **kwargs)

    def test_timeout_retries_and_all_confirmations_survive_restart(self):
        calls = []
        def query(item):
            calls.append(item)
            return self.entry(item, "failed", error="timeout") if len(calls) == 1 else self.entry(item)
        result = self.run_queue(query)
        self.assertEqual(result, {"1": "Workshop title", "2": "Workshop title"})
        self.assertEqual(calls.count("1"), 2)
        self.assertEqual(self.now, 1015)
        query = Mock()
        self.assertEqual(self.run_queue(query), result)
        query.assert_not_called()
        self.assertEqual(read_json(self.path)["items"]["1"]["attempts"], 2)

    def test_rate_limit_waits_retry_after_and_then_continues(self):
        attempts = []
        def query(item):
            attempts.append(item)
            if item == "1" and attempts.count("1") == 1:
                return self.entry("1", "failed", http_status=429, retry_after=250)
            return self.entry(item) if item == "1" else self.entry(item, "removed", error="explicit removal")
        result = self.run_queue(query)
        self.assertEqual(result, {"1": "Workshop title"})
        self.assertGreaterEqual(self.now, 1250)
        self.assertEqual(read_json(self.path)["items"]["2"]["status"], "removed")
        self.assertTrue(any(e[2]["status"] == "waiting" for e in self.events))

    def test_stop_preserves_partial_results_and_resumes_only_pending(self):
        stop = threading.Event()
        def progress(done, total, entry):
            if entry["status"] == "success":
                stop.set()
        first = Mock(return_value=self.entry("1"))
        result = run_title_queue("646570", ["1", "2"], progress, checkpoint=self.path, stop_event=stop,
                                 query=first, clock=lambda: self.now, wait=self.wait)
        self.assertEqual(result, {"1": "Workshop title"})
        query = Mock(return_value=self.entry("2"))
        result = self.run_queue(query)
        query.assert_called_once_with("2")
        self.assertEqual(len(result), 2)

    def test_stop_during_rate_limit_persists_cooldown_across_restart(self):
        stop = threading.Event()
        def progress(done, total, entry):
            if entry["status"] == "retry":
                stop.set()
        run_title_queue("646570", ["1"], progress, checkpoint=self.path, stop_event=stop,
                        query=lambda item: self.entry(item, "failed", http_status=429), clock=lambda: self.now, wait=self.wait)
        due = read_json(self.path)["cooldown_until"]
        request_times = []
        self.run_queue(lambda item: (request_times.append(self.now) or self.entry(item)), ids=("1",))
        self.assertGreaterEqual(request_times[0], due)

    def test_unconfirmed_page_is_deferred_instead_of_stopping_queue(self):
        self.run_queue(lambda item: self.entry(item, "failed", http_status=403), ids=("1",))
        self.assertEqual(self.events[-1][2]["status"], "deferred")
        self.assertEqual(read_json(self.path)["items"]["1"]["status"], "retry")
        self.assertEqual(self.now, 1015)
        self.assertNotIn("cooldown_until", read_json(self.path))

    def test_failures_are_retried_only_after_later_items_and_resume(self):
        calls = []
        def query(item):
            calls.append(item)
            return self.entry(item, "failed", http_status=200, api_result=15) if item in ("1","2") else self.entry(item)
        result = self.run_queue(query, ids=("1","2","3"))
        self.assertEqual(calls,["1","2","3","1","2","1","2"])
        self.assertEqual(result,{"3":"Workshop title"})
        calls.clear()
        self.assertEqual(self.run_queue(lambda item:(calls.append(item) or self.entry(item)),ids=("1","2","3")),
                         {"1":"Workshop title","2":"Workshop title","3":"Workshop title"})
        self.assertEqual(calls,["1","2"])

    def test_api_has_no_artificial_wait_and_short_rate_backoff(self):
        times=[]
        run_title_queue('646570',['1','2'],checkpoint=self.path,transport='account_api',
                        query=lambda item:(times.append(self.now) or self.entry(item)),clock=lambda:self.now,wait=self.wait)
        self.assertEqual(times,[1000,1000])
        self.assertEqual(retry_delay({'http_status':429},1,'account_api'),2)
        self.assertEqual(retry_delay({'http_status':429,'retry_after':50},1,'account_api'),50)

    def test_suspect_is_only_rechecked_once_and_can_recover(self):
        query=Mock(side_effect=lambda item:self.entry(item,'failed',http_status=200,api_result=15))
        self.run_queue(query,ids=('1',))
        self.assertTrue(read_json(self.path)['items']['1']['suspected'])
        self.assertEqual(query.call_count,3)
        query.reset_mock()
        self.run_queue(query,ids=('1',),force=True)
        self.assertEqual(query.call_count,1)
        self.run_queue(lambda item:self.entry(item),ids=('1',),force=True)
        self.assertFalse(read_json(self.path)['items']['1'].get('suspected',False))

    def test_force_refresh_preserves_cooldown_and_pending_attempts(self):
        self.run_queue(lambda item: self.entry(item), ids=("1",))
        query = Mock(return_value=self.entry("1", title_override="unused"))
        self.run_queue(query, ids=("1",), force=True)
        query.assert_called_once_with("1")

    def test_nickname_preference_and_explicit_alias(self):
        self.assertEqual(workshop_nickname("中文工坊", "中文模组", "中文.jar"), "中文工坊")
        self.assertEqual(workshop_nickname("Workshop", "中文模组", "中文.jar"), "中文模组")
        self.assertEqual(workshop_nickname("Workshop", "Mod", "中文.jar"), "中文")
        self.assertEqual(workshop_nickname("Workshop", "Mod", "plain.jar"), "Workshop")
        self.assertEqual(workshop_nickname("Workshop", "Mod", "中文.json"), "Workshop")
        mod = Mod("sts1", "mod", "中文模组", "1", Path("mod.jar"), "steam_workshop", "1", package_title="Workshop")
        self.assertEqual(mod.nickname, "中文模组")
        mod.alias = "手动昵称"
        self.assertEqual(mod.nickname, "手动昵称")

    def test_old_cached_english_confirmation_is_migrated_and_refetched(self):
        old = self.entry("1", source="local_cache")
        atomic_json(self.path, {"appid": "646570", "items": {"1": old}, "cooldown_until": 1100})
        times = []
        def query(item):
            times.append(self.now)
            entry = self.entry(item, source="web_schinese", page_language="zh-cn")
            entry["title"] = "控制台"
            return entry
        self.assertEqual(self.run_queue(query, ids=("1",)), {"1": "控制台"})
        self.assertGreaterEqual(times[0], 1100)
        self.assertTrue(any(e[2]["status"] == "refresh" for e in self.events))
        self.assertTrue(read_json(self.path)["items"]["1"]["locale_verified"])

    def test_unverified_english_library_cache_does_not_skip_query(self):
        query = Mock(return_value=self.entry("1"))
        self.run_queue(query, ids=("1",), cached_titles={"1": "Free Loadout"})
        query.assert_called_once_with("1")

    def test_english_really_read_from_chinese_page_is_reused(self):
        saved = self.entry("1", source="web_schinese", page_language="zh-cn")
        atomic_json(self.path, {"appid": "646570", "items": {"1": saved}})
        query = Mock()
        self.assertEqual(self.run_queue(query, ids=("1",)), {"1": "Workshop title"})
        query.assert_not_called()

    def test_request_spacing_includes_first_request_and_is_persisted(self):
        times = []
        delays = iter([6, 9, 8])
        run_title_queue("646570", ["1", "2"], checkpoint=self.path,
                        query=lambda item: (times.append(self.now) or self.entry(item)),
                        clock=lambda: self.now, wait=self.wait, interval=lambda: next(delays))
        self.assertEqual(times, [1006, 1015])
        self.assertEqual(read_json(self.path)["next_request_at"], 1023)
        self.run_queue(lambda item: (times.append(self.now) or self.entry(item)), ids=("3",))
        self.assertEqual(times[-1], 1023)

    def test_retry_delay_respects_server_header(self):
        self.assertEqual(retry_delay({"http_status": 429}, 1), 120)
        self.assertEqual(retry_delay({}, 1), 0)
        self.assertEqual(retry_delay({"http_status": 403}, 8), 0)
        self.assertEqual(retry_delay({"http_status": 200}, 8), 0)
        self.assertEqual(retry_delay({"http_status": 503, "retry_after": 60}, 1), 60)
        self.assertEqual(retry_delay({"http_status": 429, "retry_after": 3600}, 9), 3600)

    def test_legacy_non_rate_limit_waits_are_removed_and_wait_reason_is_clear(self):
        old = self.entry("1", "retry", http_status=403, failures=5, next_attempt=9000)
        atomic_json(self.path, {"appid": "646570", "items": {"1": old}, "cooldown_until": 9000})
        times = []
        self.run_queue(lambda item: (times.append(self.now) or self.entry(item)), ids=("1",))
        self.assertEqual(times, [1005])
        waits = [e[2] for e in self.events if e[2]["status"] == "waiting"]
        self.assertTrue(waits)
        self.assertTrue(all(e["wait_kind"] == "request_interval" for e in waits))

    def test_unparsed_pages_do_not_inflate_later_rate_limit_backoff(self):
        old = self.entry("1", "retry", http_status=200, failures=6, next_attempt=9000)
        atomic_json(self.path, {"appid": "646570", "items": {"1": old}})
        query = Mock(side_effect=[self.entry("1", "failed", http_status=429), self.entry("1")])
        self.run_queue(query, ids=("1",))
        retry = next(e[2] for e in self.events if e[2]["status"] == "retry")
        self.assertEqual(retry["wait_seconds"], 120)
        self.assertEqual(retry["rate_failures"], 1)


if __name__ == "__main__":
    unittest.main()
