import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse

from core import read_json
from steam_account import AccountTitleQuery, api_open, load_key, save_key, validate_key
from workshop_queue import run_title_queue


KEY = "0123456789abcdef" * 2


class AccountTests(unittest.TestCase):
    def response(self, rows):
        response = io.BytesIO(json.dumps({"response": {"publishedfiledetails": rows}}).encode())
        response.status = 200
        return response

    def test_blocked_network_cancels_and_reports_before_response(self):
        import threading
        from steam_account import RequestCancelled
        entered=threading.Event();release=threading.Event();finished=threading.Event();stop=threading.Event();errors=[];events=[]
        query=AccountTitleQuery('2868840',['1'],KEY,stop_event=stop);query.request_progress=lambda size,seconds:events.append((size,seconds))
        def network(request):
            entered.set();release.wait(5);return self.response([{'publishedfileid':'1','result':1,'consumer_appid':2868840,'title':'Late'}])
        def run():
            try:query('1')
            except Exception as exc:errors.append(exc)
            finally:finished.set()
        with patch('steam_account.api_open',side_effect=network):
            worker=threading.Thread(target=run);worker.start()
            try:
                self.assertTrue(entered.wait(2));self.assertEqual(events[0],(1,0));stop.set()
                self.assertTrue(finished.wait(1.5));self.assertIsInstance(errors[0],RequestCancelled);self.assertFalse(query.buffer)
            finally:release.set();worker.join(2)

    def test_network_reason_classification_never_echoes_secret(self):
        import socket,ssl
        from steam_account import network_error_kind
        self.assertEqual(network_error_kind(urllib.error.URLError(TimeoutError()))[0],'timeout')
        self.assertEqual(network_error_kind(urllib.error.URLError(socket.gaierror()))[0],'dns')
        self.assertEqual(network_error_kind(urllib.error.URLError(ssl.SSLError()))[0],'tls')
        kind,description=network_error_kind(urllib.error.URLError(KEY));self.assertNotIn(KEY,description)

    def test_authenticated_bulk_queue_uses_two_requests_for_91_items(self):
        requests, events = [], []
        now = [1000]
        def wait(seconds):
            now[0] += seconds
            return False
        def open_request(request):
            requests.append(request)
            params = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
            self.assertEqual(params["language"], ["6"])
            self.assertNotIn(KEY, request.full_url)
            self.assertEqual(request.get_header("X-webapi-key"), KEY)
            ids = [values[0] for name, values in params.items() if name.startswith("publishedfileids[")]
            self.assertLessEqual(len(ids),50)
            return self.response([{"publishedfileid": item, "consumer_appid": 2868840, "result": 1,
                                   "title": "控制台" if item == "1" else "English title"} for item in ids])
        with tempfile.TemporaryDirectory() as temp, patch("steam_account.api_open", side_effect=open_request):
            query = AccountTitleQuery("2868840", [str(i) for i in range(1, 92)], KEY)
            checkpoint = Path(temp) / "queue.json"
            titles = run_title_queue("2868840", query.ids, lambda *e: events.append(e), query=query,
                                     checkpoint=checkpoint, clock=lambda: now[0], wait=wait, interval=lambda: 0,
                                     transport="account_api")
            self.assertEqual(len(titles), 91)
            self.assertEqual(titles["1"], "控制台")
            self.assertEqual(len(requests), 2)
            self.assertEqual(now[0], 1000)
            self.assertEqual(events[-1][0], 91)
            self.assertNotIn(KEY, checkpoint.read_text(encoding="utf-8"))
            self.assertNotIn(KEY, json.dumps(events))
            self.assertEqual(read_json(checkpoint)["transport"], "account_api")

    def test_bad_key_stops_immediately_without_anonymous_fallback_or_key_in_error(self):
        error = urllib.error.HTTPError("https://example.invalid/?key=" + KEY, 401, KEY, {}, io.BytesIO(KEY.encode()))
        with patch("steam_account.api_open", side_effect=error) as network:
            now = [1000]
            events = []
            def wait(seconds):
                now[0] += seconds
            query = AccountTitleQuery("2868840", ["1", "2"], KEY)
            result = run_title_queue("2868840", ["1", "2"], lambda *e: events.append(e), query=query,
                                     clock=lambda: now[0], wait=wait, interval=lambda: 5, transport="account_api")
        network.assert_called_once()
        self.assertEqual(result, {})
        self.assertEqual(events[-1][2]["status"], "halted")
        self.assertNotIn(KEY, json.dumps(events))

    def test_missing_or_other_game_results_are_not_called_removed_or_used(self):
        rows = [{"publishedfileid": "1", "result": 9},
                {"publishedfileid": "2", "result": 1, "consumer_appid": 1, "title": "Wrong game"}]
        query = AccountTitleQuery("2868840", ["1", "2"], KEY)
        with patch("steam_account.api_open", return_value=self.response(rows)) as network:
            first, second = query("1"), query("2")
        network.assert_called_once()
        self.assertEqual((first["status"], second["status"]), ("failed", "failed"))
        self.assertEqual(second["title"], "")

    @unittest.skipUnless(os.name == "nt", "Windows user encryption")
    def test_key_round_trip_is_encrypted_at_rest(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "key.bin"
            self.assertEqual(load_key(path), "")
            save_key(path, KEY)
            self.assertNotIn(KEY.encode(), path.read_bytes())
            self.assertEqual(load_key(path), KEY)

    def test_key_validation_and_no_forwarding_credentials_on_redirects(self):
        with self.assertRaises(ValueError):
            validate_key("password")
        with patch("steam_account.urllib.request.build_opener") as build:
            api_open(object())
            handler = build.call_args.args[0]
            with self.assertRaises(ValueError):
                handler.redirect_request(None, None, 302, "Found", {}, "https://example.invalid")


if __name__ == "__main__":
    unittest.main()
