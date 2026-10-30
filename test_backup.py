import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch
import zipfile

from backup import backup_plan, export_bundle, fetch_workshop_titles, verify_bundle, localized_workshop_title, query_chinese_workshop_title


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workshop = self.root / "workshop"
        self.package = self.workshop / "123"
        self.package.mkdir(parents=True)
        (self.package / "a.jar").write_bytes(b"not executed")
        (self.package / "lib").mkdir()
        (self.package / "lib/child.jar").write_bytes(b"nested library preserved")
        (self.package / "extra.png").write_bytes(b"sidecar resource")
        self.config = self.root / "settings.save"
        self.config.write_bytes(b'{"original":true}')
        self.game = self.root / "game"
        disabled = self.game / "mods disabled"
        disabled.mkdir(parents=True)
        (disabled / "disabled.dll").write_bytes(b"disabled mod")
        self.specs = {"sts1": {"game_dir": str(self.game), "workshop_roots": [str(self.workshop)],
                                "configs": [str(self.config)]}}

    def tearDown(self):
        self.temp.cleanup()

    def test_archive_preserves_package_sidecars_disabled_configs_and_hashes(self):
        target = self.root / "backup.zip"
        result = export_bundle(target, self.specs, {"tags": ["test"]})
        self.assertEqual(result["files"], 4)
        self.assertEqual(verify_bundle(target)["checked"], 5)
        with zipfile.ZipFile(target) as archive:
            self.assertIsNone(archive.testzip())
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["manager_data"], {"tags": ["test"]})
            for item in manifest["files"]:
                self.assertEqual(hashlib.sha256(archive.read(item["path"])).hexdigest(), item["sha256"])
            self.assertEqual(archive.read("payload/sts1/workshop/library-0/123/lib/child.jar"), b"nested library preserved")
            self.assertEqual(archive.read("configs/sts1/0-settings.save"), self.config.read_bytes())
            self.assertIn("恢复说明.txt", archive.namelist())

    def test_changed_file_aborts_and_retains_old_archive(self):
        target = self.root / "backup.zip"
        target.write_bytes(b"previous backup")
        changed = False
        def progress(*_):
            nonlocal changed
            if not changed:
                changed = True
                (self.package / "a.jar").write_bytes(b"replacement with new size")
        with self.assertRaisesRegex(ValueError, "变化"):
            export_bundle(target, self.specs, {}, progress=progress)
        self.assertEqual(target.read_bytes(), b"previous backup")
        self.assertFalse(list(self.root.glob("*.partial")))

    def test_archive_inside_package_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "不能放在"):
            export_bundle(self.package / "backup.zip", self.specs, {})

    def test_file_added_during_backup_is_detected(self):
        def progress(*_):
            (self.package / "new.txt").write_text("new", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "变化"):
            export_bundle(self.root / "backup.zip", self.specs, {}, progress=progress)

    def response(self, page, item="3747573916"):
        response = io.BytesIO(page.encode())
        response.geturl = lambda: f"https://steamcommunity.com/sharedfiles/filedetails/?id={item}&l=schinese"
        response.status = 200
        return response

    def test_lookup_accepts_titles_or_removal_and_reports_every_confirmation(self):
        def query(item):
            return {"id": item, "title": "控制台" if item == "123" else "",
                    "status": "success" if item == "123" else "removed", "error": "条目不存在"}
        events = []
        now = [1000]
        def wait(seconds):
            now[0] += seconds
            return False
        with patch("backup.query_chinese_workshop_title", side_effect=query), patch("backup.urllib.request.urlopen") as network:
            titles = fetch_workshop_titles("646570", ["123", "456", "123"], lambda *args: events.append(args), clock=lambda: now[0], wait=wait)
        self.assertEqual(titles, {"123": "控制台"})
        network.assert_not_called()
        self.assertEqual([e[0] for e in events if e[2]["status"] in {"success", "removed"}], [1, 2])
        self.assertTrue(all(e[1] == 2 for e in events))
        self.assertEqual({e[2]["id"] for e in events}, {"123", "456"})

    def test_localized_page_title_with_entities(self):
        page = '<div class="workshopItemTitle">控制台 &amp; 配装</div><div>描述</div>'
        with patch("backup.urllib.request.urlopen", return_value=self.response(page)) as request:
            self.assertEqual(localized_workshop_title("3747573916"), ("3747573916", "控制台 & 配装"))
            self.assertIn("l=schinese", request.call_args.args[0].full_url)

    def test_english_title_on_chinese_page_is_a_valid_confirmation(self):
        with patch("backup.urllib.request.urlopen", return_value=self.response('<div class="workshopItemTitle">Free Loadout</div>', "123")):
            entry = query_chinese_workshop_title("123")
        self.assertEqual((entry["status"], entry["title"], entry["page_title"]), ("success", "Free Loadout", "Free Loadout"))
        self.assertEqual(entry["error"], "")

    def test_missing_heading_and_network_errors_are_diagnosable(self):
        with patch("backup.urllib.request.urlopen", return_value=self.response('<html>login</html>', '123')):
            entry = query_chinese_workshop_title("123")
        self.assertEqual(entry["status"], "failed")
        self.assertIn("未取得标题", entry["error"])
        with patch("backup.urllib.request.urlopen", side_effect=OSError("offline")) as request:
            entry = query_chinese_workshop_title("123")
        self.assertEqual(request.call_count, 1)
        self.assertEqual(entry["status"], "failed")
        self.assertIn("offline", entry["error"])
        self.assertEqual(entry["attempts"], 1)

    def test_redirect_to_different_workshop_id_is_rejected(self):
        def response(*args, **kwargs):
            return self.response('<div class="workshopItemTitle">错误条目</div>', "999")
        with patch("backup.urllib.request.urlopen", side_effect=response):
            entry = query_chinese_workshop_title("123")
        self.assertEqual((entry["status"], entry["title"]), ("failed", ""))
        self.assertIn("工坊号不匹配", entry["error"])

    def test_removal_requires_explicit_evidence_and_no_valid_title(self):
        removed = "That item does not exist. It may have been removed by the author."
        with patch("backup.urllib.request.urlopen", return_value=self.response(removed, "123")):
            self.assertEqual(query_chinese_workshop_title("123")["status"], "removed")
        with patch("backup.urllib.request.urlopen", return_value=self.response('<div class="workshopItemTitle">BaseMod</div>' + removed, "123")):
            self.assertEqual(query_chinese_workshop_title("123")["status"], "success")
        with patch("backup.urllib.request.urlopen", return_value=self.response('<script>var message="' + removed + '"</script>Verification required', "123")):
            self.assertEqual(query_chinese_workshop_title("123")["status"], "failed")

    def test_single_attempt_reports_rate_limit_to_queue_and_cache_needs_no_network(self):
        with patch("backup.urllib.request.urlopen", side_effect=urllib.error.HTTPError("https://steamcommunity.com", 429, "limit", {}, None)) as request:
            entry = query_chinese_workshop_title("123")
        request.assert_called_once()
        self.assertEqual(entry["http_status"], 429)
        with patch("backup.query_chinese_workshop_title") as query:
            self.assertEqual(fetch_workshop_titles("646570", ["123"], cached_titles={"123": "本地中文名"}), {"123": "本地中文名"})
        query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
