import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from core import (ConfigSession, Mod, atomic_json, dependency_issues, filter_mods,
                  read_json, restore_profile, scan, resolve_candidates, compare_versions)


def mod(id="a", game="sts2", source="steam_workshop", enabled=False):
    return Mod(game, id, id, "1.2.0", Path(id + (".jar" if game == "sts1" else ".json")), source, enabled=enabled)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "settings.save"

    def tearDown(self):
        self.temp.cleanup()

    def session(self, game, data):
        atomic_json(self.path, data)
        return ConfigSession(game, self.path)

    def test_sts2_preserves_order_sources_unknown_fields_and_backup(self):
        original = {"audio": {"volume": .4}, "mod_settings": {"mods_enabled": True, "future": 7,
                    "mod_list": [{"id": "a", "source": "steam_workshop", "is_enabled": True, "future": 3},
                                 {"id": "offline", "source": "mods_directory", "is_enabled": True}]}}
        session = self.session("sts2", original)
        before = self.path.read_bytes()
        mods = [mod("a"), mod("new")]
        session.load_states(mods)
        self.assertTrue(mods[0].enabled)
        self.assertFalse(mods[1].enabled)
        mods[0].enabled = False
        result = session.render(mods, False)
        backup = session.write(result, self.root / "backups")
        self.assertEqual(backup.read_bytes(), before)
        self.assertEqual(result["audio"], original["audio"])
        settings = result["mod_settings"]
        self.assertEqual(settings["future"], 7)
        self.assertEqual(settings["mod_list"][0]["future"], 3)
        self.assertFalse(settings["mod_list"][0]["is_enabled"])
        self.assertEqual(settings["mod_list"][1], original["mod_settings"]["mod_list"][1])
        self.assertFalse(settings["mod_list"][2]["is_enabled"])
        self.assertEqual(read_json(self.path), result)

    def test_concurrent_edit_refuses_to_overwrite(self):
        session = self.session("sts1", {"defaultList": "old", "lists": {"old": ["a.jar"]}})
        self.path.write_text('{"external": true}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "配置已被"):
            session.write({}, self.root / "backups")
        self.assertEqual(read_json(self.path), {"external": True})

    def test_sts1_legacy_chinese_encoding_roundtrip(self):
        original = {"defaultList": "中文列表", "lists": {"中文列表": ["中文模组.jar"]}}
        self.path.write_bytes(json.dumps(original, ensure_ascii=False).encode("gbk"))
        before = self.path.read_bytes()
        session = ConfigSession("sts1", self.path)
        item = mod("a", "sts1")
        item.path = Path("中文模组.jar")
        session.load_states([item])
        self.assertTrue(item.enabled)
        backup = session.write(session.render([item]), self.root / "backups")
        self.assertEqual(backup.read_bytes(), before)
        self.path.read_bytes().decode("ascii")
        self.assertEqual(read_json(self.path)["lists"]["Spire Manager"], ["中文模组.jar"])

    def test_sts1_preserves_original_profile_and_load_order(self):
        original = {"defaultList": "old", "lists": {"old": ["b.jar", "a.jar"], "other": []}, "future": 9}
        session = self.session("sts1", original)
        mods = [mod("a", "sts1", enabled=True), mod("b", "sts1", enabled=True), mod("c", "sts1", enabled=True)]
        result = session.render(mods)
        self.assertEqual(result["lists"]["old"], ["b.jar", "a.jar"])
        self.assertEqual(result["lists"]["Spire Manager"], ["b.jar", "a.jar", "c.jar"])
        self.assertEqual(result["future"], 9)
        self.assertEqual(original["defaultList"], "old")

    def test_sts1_selects_newer_workshop_but_refuses_shadowed_path(self):
        session = self.session("sts1", {"defaultList": "old", "lists": {"old": ["a.jar", "variant.jar"]}})
        old, new = mod("a", "sts1"), mod("a", "sts1")
        old.workshop_id, new.workshop_id = "123", "456"
        new.path, new.version = Path("variant.jar"), "2.0.0"
        resolve_candidates([old, new])
        session.load_states([old, new])
        self.assertFalse(old.enabled)
        self.assertTrue(new.enabled)
        old.enabled = True
        with self.assertRaisesRegex(ValueError, "无法指定"):
            session.render([old, new])
        old.enabled = new.enabled = False
        self.assertEqual(session.render([old, new])["lists"]["Spire Manager"], [])

    def test_sts2_same_id_different_sources_are_separate(self):
        session = self.session("sts2", {"mod_settings": {"mods_enabled": True, "mod_list": [
            {"id": "a", "source": "mods_directory", "is_enabled": True},
            {"id": "a", "source": "steam_workshop", "is_enabled": False}]}})
        items = [mod("a"), mod("a", source="mods_directory")]
        session.load_states(items)
        self.assertEqual([m.enabled for m in items], [False, True])

    def test_profile_closes_new_mods_and_preserves_labels(self):
        items = [mod("a", enabled=False), mod("new", enabled=True)]
        items[0].tags, items[0].group = ["辅助", "联机"], "基础"
        restore_profile(items, {"enabled": [items[0].key]})
        self.assertEqual([m.enabled for m in items], [True, False])
        self.assertEqual(items[0].tags, ["辅助", "联机"])
        self.assertEqual(items[0].group, "基础")

    def test_combined_filter_and_exact_tag(self):
        a, b = mod("Example"), mod("Other")
        a.tags, a.group, a.enabled = ["辅助", "联机"], "基础", True
        b.tags = ["辅助扩展"]
        self.assertEqual(filter_mods([a, b], "tag:辅助 version:1.2 state:on", "基础"), [a])
        self.assertEqual(filter_mods([a, b], "name:example"), [a])
        self.assertEqual(filter_mods([a, b], "tag:辅助"), [a])

    def test_required_dependency_and_version(self):
        a, b = mod("a", enabled=True), mod("b")
        a.dependencies = [{"id": "b", "min_version": "1.3"}]
        self.assertTrue(dependency_issues([a, b]))
        b.enabled = True
        self.assertTrue(dependency_issues([a, b]))
        b.version = "1.3.0"
        self.assertEqual(dependency_issues([a, b]), [])

    def test_scanner_ignores_backup_folders_and_keeps_physical_copies(self):
        workshop = self.root / "2868840"
        for folder in ("123", "456", "789 backup"):
            directory = workshop / folder
            directory.mkdir(parents=True)
            atomic_json(directory / "a.json", {"id": "a", "name": "A", "version": "1", "has_dll": True})
            (directory / "a.dll").touch()
        items, issues = scan("sts2", "", [str(workshop)])
        self.assertEqual(len(items), 2)
        self.assertNotEqual(items[0].key, items[1].key)
        self.assertTrue(all(m.loadable for m in items))
        self.assertEqual(issues, [])

    def test_sts1_jar_metadata_and_local_priority(self):
        local = self.root / "game/mods"
        remote = self.root / "workshop/123"
        for directory in (local, remote):
            directory.mkdir(parents=True)
            with zipfile.ZipFile(directory / "test.jar", "w") as jar:
                jar.writestr("ModTheSpire.json", json.dumps({"modid": "a", "name": "A", "version": "1", "dependencies": ["BaseMod"]}))
        items, issues = scan("sts1", str(local.parent), [str(remote.parent)])
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].source, "mods_directory")
        self.assertTrue(items[0].loadable)
        self.assertFalse(items[1].loadable)
        self.assertEqual(items[0].dependencies, [{"id": "BaseMod"}])
        self.assertEqual(issues, [])

    def test_newer_workshop_replaces_local(self):
        local = mod("a", "sts1", source="mods_directory")
        remote = mod("a", "sts1")
        remote.version = "2.0.0"
        resolve_candidates([local, remote])
        self.assertFalse(local.loadable)
        self.assertTrue(remote.loadable)

    def test_semver_prerelease_order(self):
        self.assertGreater(compare_versions("1.0.0", "1.0.0-beta.10"), 0)
        self.assertGreater(compare_versions("1.0.0-beta.10", "1.0.0-beta.2"), 0)
        self.assertLess(compare_versions("1.0.0-alpha", "1.0.0-beta"), 0)

    def test_sts2_duplicate_files_disabled_write_one_native_entry(self):
        a, b = mod("same"), mod("same")
        a.workshop_id, b.workshop_id = "123", "456"
        resolve_candidates([a, b])
        session = self.session("sts2", {"mod_settings": {"mods_enabled": True, "mod_list": []}})
        result = session.render([a, b])
        self.assertEqual(len(result["mod_settings"]["mod_list"]), 1)
        a.enabled = True
        with self.assertRaisesRegex(ValueError, "无法指定"):
            session.render([a, b])


if __name__ == "__main__":
    unittest.main()
