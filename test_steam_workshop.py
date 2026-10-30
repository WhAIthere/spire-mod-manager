import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from core import ConfigSession, atomic_json, scan
from steam_bridge import apply_switches
from steam_workshop import (bind_snapshot, build_plan, commit_plan, preview_lines,
                            validate_plan, verify_inventory)
from manager_model import Manager


class WorkshopPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / "game"
        self.workshop = self.root / "workshop"
        self.config = self.root / "steam/00000000000000000/settings.save"
        atomic_json(self.config, {"audio": 7, "mod_settings": {"mods_enabled": True, "mod_list": []}})
        self.snapshot = {"appid": "2868840", "account": "00000000000000000", "items": {}}

    def manifest(self, folder, ident, name=None, local=False):
        directory = self.game / "mods" / folder if local else self.workshop / folder
        directory.mkdir(parents=True, exist_ok=True)
        atomic_json(directory / ((name or ident) + ".json"), {"id": ident, "version": "0.0.9", "has_dll": False})
        if not local:
            self.snapshot["items"][folder] = {"disabled": False, "installed": True, "state": 5, "path": str(directory)}

    def mods(self):
        mods, issues = scan("sts2", self.game, [self.workshop])
        self.assertEqual(issues, [])
        return mods

    def plan(self, mods):
        return build_plan(mods, self.snapshot, self.config)

    def test_select_specific_same_id_version_and_render_one_id_switch(self):
        self.manifest("123", "same")
        self.manifest("456", "same")
        mods = self.mods()
        mods[1].enabled = True
        plan = self.plan(mods)
        self.assertEqual(plan["desired"], {"123": True, "456": False})
        data = ConfigSession("sts2", self.config).render(mods, workshop_plan=plan)
        self.assertEqual(data["mod_settings"]["mod_list"], [{"id": "same", "source": "steam_workshop", "is_enabled": True}])
        self.assertEqual(data["audio"], 7)
        self.assertTrue(any("123" in text for text in preview_lines(plan)))

    def test_scan_intersects_steam_switches_with_game_id_switch(self):
        self.manifest("123", "same")
        self.manifest("456", "same")
        mods = self.mods()
        for mod in mods:
            mod.enabled = True
        self.snapshot["items"]["456"]["disabled"] = True
        bind_snapshot(mods, self.snapshot)
        self.assertEqual([m.enabled for m in mods], [True, False])
        self.assertTrue(mods[1].loadable)

    def test_same_folder_distinct_ids_can_be_individually_disabled(self):
        self.manifest("123", "a")
        self.manifest("123", "b")
        mods = self.mods()
        mods[0].enabled = True
        plan = self.plan(mods)
        data = ConfigSession("sts2", self.config).render(mods, workshop_plan=plan)
        self.assertFalse(plan["desired"]["123"])
        self.assertEqual([entry["is_enabled"] for entry in data["mod_settings"]["mod_list"]], [True, False])

    def test_opening_a_folder_checks_unselected_same_id_siblings(self):
        self.manifest("123", "a")
        self.manifest("123", "b")
        self.manifest("456", "b")
        mods = self.mods()
        for mod in mods:
            mod.enabled = mod.id == "a" or mod.workshop_id == "456"
        with self.assertRaisesRegex(ValueError, "同时生效"):
            self.plan(mods)

    def test_two_copies_in_one_folder_cannot_be_expressed_by_id(self):
        self.manifest("123", "same", "one")
        self.manifest("123", "same", "two")
        mods = self.mods()
        mods[0].enabled = True
        with self.assertRaisesRegex(ValueError, "同时生效"):
            self.plan(mods)

    def test_local_copy_can_replace_workshop_but_cannot_select_two_local_copies(self):
        self.manifest("123", "a")
        self.manifest("one", "a", local=True)
        mods = self.mods()
        mods[0].enabled = True
        self.assertEqual(self.plan(mods)["desired"], {"123": True})
        self.manifest("two", "a", local=True)
        mods = self.mods()
        mods[0].enabled = True
        with self.assertRaisesRegex(ValueError, "同时生效"):
            self.plan(mods)

    def test_foreign_account_unsubscribed_install_path_and_update_guards(self):
        self.manifest("123", "a")
        mods = self.mods()
        mods[0].enabled = True
        original = copy.deepcopy(self.snapshot)
        cases = [("account", "0", "账号"), ("items", {}, "未被当前账号订阅")]
        for field, value, message in cases:
            self.snapshot = copy.deepcopy(original)
            self.snapshot[field] = value
            with self.assertRaisesRegex(ValueError, message):
                self.plan(mods)
        for field, value, message in [("path", str(self.root), "安装目录不同"), ("state", 21, "正在更新")]:
            self.snapshot = copy.deepcopy(original)
            self.snapshot["items"]["123"][field] = value
            with self.assertRaisesRegex(ValueError, message):
                self.plan(mods)

    def test_selection_manifest_and_inventory_changes_invalidate_preview(self):
        self.manifest("123", "a")
        mods = self.mods()
        plan = self.plan(mods)
        mods[0].enabled = True
        with self.assertRaisesRegex(ValueError, "选择已变化"):
            validate_plan(plan, mods)
        mods[0].enabled = False
        self.manifest("456", "b")
        with self.assertRaisesRegex(ValueError, "目录或清单已变化"):
            verify_inventory(mods, self.game, [self.workshop])
        mods[0].path.write_text('{"id":"changed"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "文件已变化"):
            validate_plan(plan, mods)

    def test_commit_backups_and_synchronizes_both_layers(self):
        self.manifest("123", "a")
        mods = self.mods()
        plan = self.plan(mods)  # no selection means the folder should close
        session = ConfigSession("sts2", self.config)
        original = self.config.read_bytes()
        data = session.render(mods, workshop_plan=plan)
        after = copy.deepcopy(self.snapshot)
        after["items"]["123"]["disabled"] = True
        api = Mock(side_effect=[copy.deepcopy(self.snapshot), after])
        backup, receipt, result = commit_plan(session, data, plan, mods, self.game,
                                            [self.workshop], self.root / "backups", api=api)
        self.assertEqual(backup.read_bytes(), original)
        self.assertTrue(receipt.exists())
        self.assertEqual(result, after)
        self.assertEqual(api.call_args.kwargs["changes"], {"123": True})

    def test_failed_steam_write_restores_exact_game_config_and_session(self):
        self.manifest("123", "a")
        mods = self.mods()
        plan = self.plan(mods)
        session = ConfigSession("sts2", self.config)
        original = self.config.read_bytes()
        original_hash = session.original_hash
        api = Mock(side_effect=[copy.deepcopy(self.snapshot), ValueError("模拟 Steam 写入失败")])
        with self.assertRaisesRegex(ValueError, "游戏配置已恢复"):
            commit_plan(session, session.render(mods, workshop_plan=plan), plan, mods,
                        self.game, [self.workshop], self.root / "backups", api=api)
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(session.original_hash, original_hash)

    def test_external_steam_change_aborts_before_game_write(self):
        self.manifest("123", "a")
        mods = self.mods()
        plan = self.plan(mods)
        session = ConfigSession("sts2", self.config)
        original = self.config.read_bytes()
        changed = copy.deepcopy(self.snapshot)
        changed["items"]["123"]["disabled"] = True
        api = Mock(return_value=changed)
        with self.assertRaisesRegex(ValueError, "已变化"):
            commit_plan(session, session.render(mods, workshop_plan=plan), plan, mods,
                        self.game, [self.workshop], self.root / "backups", api=api)
        self.assertEqual(api.call_count, 1)
        self.assertEqual(self.config.read_bytes(), original)

    def test_active_unscanned_steam_folder_cannot_bypass_conflict_checks(self):
        self.manifest("123", "a")
        mods = self.mods()
        self.manifest("456", "b")
        with self.assertRaisesRegex(ValueError, "未纳入扫描"):
            self.plan(mods)
        self.snapshot["items"]["456"]["disabled"] = True
        self.plan(mods)  # The unscanned folder is excluded by Steam and stays unchanged.

    def test_steam_failure_does_not_overwrite_an_external_game_config_edit(self):
        self.manifest("123", "a")
        mods = self.mods()
        plan = self.plan(mods)
        session = ConfigSession("sts2", self.config)
        external = b'{"external_change": true}'

        def api(game_dir, command="snapshot", **options):
            if command == "apply":
                self.config.write_bytes(external)
                raise ValueError("模拟 API 失败")
            return copy.deepcopy(self.snapshot)

        with self.assertRaisesRegex(ValueError, "未覆盖"):
            commit_plan(session, session.render(mods, workshop_plan=plan), plan, mods,
                        self.game, [self.workshop], self.root / "backups", api=api)
        self.assertEqual(self.config.read_bytes(), external)

    def test_single_version_click_replaces_sibling_and_records_one_edit(self):
        self.manifest("123", "a")
        self.manifest("456", "a")
        mods = self.mods()
        mods[0].enabled = True
        app = Manager.__new__(Manager)
        app.mods, app.history, app.editor_state, app.changed = mods, Mock(), Mock(), Mock()
        app.toggle([mods[1].key], True)
        self.assertEqual([m.enabled for m in mods], [False, True])
        app.history.record.assert_called_once()


class FakeClient:
    def __init__(self, fail=False):
        self.value = {"appid": "2868840", "account": "1", "items": {
            "123": {"disabled": False, "installed": True, "path": "one"},
            "456": {"disabled": True, "installed": True, "path": "two"}}}
        self.calls = []
        self.fail = fail

    def snapshot(self):
        return copy.deepcopy(self.value)

    def set_disabled(self, ids, disabled):
        if not ids:
            return
        self.calls.append((ids, disabled))
        for key in ids:
            self.value["items"][key]["disabled"] = disabled
        if self.fail:
            self.fail = False
            raise ValueError("partial failure")


class BridgeTransactionTests(unittest.TestCase):
    def test_disables_old_before_enabling_new_and_reads_back(self):
        client = FakeClient()
        result = apply_switches(client, client.snapshot(), {"123": True, "456": False})
        self.assertEqual(client.calls, [(["123"], True), (["456"], False)])
        self.assertTrue(result["items"]["123"]["disabled"])
        self.assertFalse(result["items"]["456"]["disabled"])

    def test_partial_native_failure_restores_both_folder_states(self):
        client = FakeClient(fail=True)
        before = client.snapshot()
        with self.assertRaisesRegex(ValueError, "已恢复"):
            apply_switches(client, before, {"123": True, "456": False})
        self.assertEqual(client.snapshot(), before)

    def test_foreign_changes_and_unknown_items_do_not_write(self):
        client = FakeClient()
        expected = client.snapshot()
        expected["account"] = "other"
        with self.assertRaisesRegex(ValueError, "已变化"):
            apply_switches(client, expected, {"123": True})
        with self.assertRaisesRegex(ValueError, "未订阅"):
            apply_switches(client, client.snapshot(), {"789": True})
        self.assertFalse(client.calls)


if __name__ == "__main__":
    unittest.main()
