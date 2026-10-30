"""Migration parity tests against real temporary game configs; no live Steam changes."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import zipfile

from core import Mod, atomic_json, read_json
from manager_model import Manager
from web_api import Api


class WebManagerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.one = self.root / 'one/mod_lists.json'
        self.two = self.root / 'steam/00000000000000000/settings.save'
        atomic_json(self.one, {'defaultList': 'old', 'lists': {'old': ['a.jar']}})
        atomic_json(self.two, {'audio': 7, 'mod_settings': {'mods_enabled': True, 'mod_list': []}})
        self.catalog = {g: {'game_dir': str(self.root / g), 'configs': [str(config)],
                            'workshop_roots': [str(self.root / (g + '-workshop'))]}
                        for g, config in (('sts1', self.one), ('sts2', self.two))}
        (self.root / 'sts1-workshop').mkdir()
        for name in ('a', 'b'):
            jar = self.root / 'sts1/mods' / (name + '.jar')
            jar.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(jar, 'w') as archive:
                archive.writestr('ModTheSpire.json', json.dumps({'modid': name, 'name': name,
                                                               'version': '1.0', 'dependencies': []}))
        self.steam_snapshot = {'appid': '2868840', 'account': '00000000000000000', 'items': {}}
        for folder in ('123', '456'):
            path = self.root / 'sts2-workshop' / folder
            atomic_json(path / 'same.json', {'id': 'same', 'version': '1.0', 'has_dll': False})
            self.steam_snapshot['items'][folder] = {'disabled': False, 'installed': True, 'state': 5, 'path': str(path)}
        self.steam = Mock(side_effect=lambda *a, **k: copy.deepcopy(self.steam_snapshot))
        self.model = Manager(self.root / 'records', self.catalog, self.steam)
        self.api = Api(self.model)
        self.assertTrue(self.api.request('init')['ok'])

    def edit(self, action, **payload):
        result = self.api.request(action, payload)
        self.assertTrue(result['ok'], result)
        if action == 'scan':
            deadline = time.monotonic() + 10
            while self.api._jobs.value['running'] and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertFalse(self.api._jobs.value['running'])
        return result

    def test_api_introduction_dismissal_persists_without_credentials(self):
        self.assertFalse(self.model.snapshot()['api_intro_seen'])
        self.edit('api_intro_seen')
        self.assertTrue(self.model.snapshot()['api_intro_seen'])
        self.assertTrue(read_json(self.model.store_path)['web_ui']['api_intro_seen'])
        self.assertFalse((self.model.directory/'credentials/steam-api-key.bin').exists())

    def test_suspected_tag_changes_no_enabled_states_and_recovers(self):
        mod=self.model.mods[0];mod.workshop_id='123'
        enabled=mod.enabled
        self.model.workshop_health({'id':'123','status':'deferred','suspected':True})
        self.assertIn('已下架',mod.tags);self.assertEqual(mod.enabled,enabled)
        self.model.workshop_health({'id':'123','status':'success'})
        self.assertNotIn('已下架',mod.tags);self.assertEqual(mod.enabled,enabled)
        mod.tags.append('已下架')
        self.model.workshop_health({'id':'123','status':'deferred','suspected':True})
        self.model.workshop_health({'id':'123','status':'success'})
        self.assertIn('已下架',mod.tags)

    def test_group_order_persists_independently_of_child_sort(self):
        self.edit('game',game='sts2')
        self.edit('view',mode='文件夹分组')
        keys=[s['key'] for s in self.model.snapshot()['sections']]
        self.edit('view',group_order=list(reversed(keys)),sort_column='name')
        self.assertEqual([s['key'] for s in self.model.snapshot()['sections']],list(reversed(keys)))
        self.model.rescan(discard=True)
        self.assertEqual([s['key'] for s in self.model.snapshot()['sections']],list(reversed(keys)))
        self.edit('view',group_order=[])
        self.assertEqual([s['key'] for s in self.model.snapshot()['sections']],keys)

    def test_rename_custom_group_updates_all_members_and_undo(self):
        keys=[m.key for m in self.model.mods]
        self.edit('group',keys=keys,value='Before')
        self.edit('view',query='a',group='Before',mode='自定义分组')
        self.edit('group_rename',old='Before',value='After')
        self.assertTrue(all(m.group=='After' for m in self.model.mods))
        self.assertEqual(self.model.group,'After')
        self.edit('undo')
        self.assertTrue(all(m.group=='Before' for m in self.model.mods))

    def test_editor_undo_redo_and_new_edit_clear_future(self):
        key = self.model.mods[0].key
        original = self.one.read_bytes()
        self.edit('toggle', keys=[key])
        self.edit('tags_add', keys=[key], value='辅助，美化')
        self.edit('alias', keys=[key], value='昵称')
        self.edit('group', keys=[key], value='我的分组')
        for _ in range(4):
            self.edit('undo')
        mod = self.model.mods[0]
        self.assertTrue(mod.enabled)
        self.assertEqual((mod.alias, mod.tags, mod.group), ('', [], ''))
        for _ in range(4):
            self.edit('redo')
        self.assertEqual((mod.alias, mod.tags, mod.group), ('昵称', ['美化', '辅助'], '我的分组'))
        self.edit('undo')
        self.edit('tags_add', keys=[key], value='新标签')
        self.assertFalse(self.model.snapshot()['redo'])
        self.assertEqual(self.one.read_bytes(), original)
        self.assertEqual(read_json(self.model.store_path)['games']['sts1']['metadata'][key]['alias'], '昵称')

    def test_profiles_restore_turns_off_unrecorded_mods_and_is_undoable(self):
        self.edit('profile_save', name='默认')
        keys = [m.key for m in self.model.mods]
        self.edit('toggle', keys=keys, value=True)
        self.edit('profile_restore', name='默认')
        self.assertEqual([m.enabled for m in self.model.mods], [True, False])
        self.edit('undo')
        self.assertTrue(all(m.enabled for m in self.model.mods))
        self.edit('profile_delete', name='默认')
        self.assertNotIn('默认', self.model.state['profiles'])
        self.edit('undo')
        self.assertIn('默认', self.model.state['profiles'])

    def test_tags_overlap_share_one_record_and_metadata_survives_rescan(self):
        keys = [m.key for m in self.model.mods]
        self.edit('tags_add', keys=keys, value='通用')
        self.edit('tags_add', keys=keys[:1], value='辅助')
        self.edit('view', mode='标签分组')
        state = self.model.snapshot()
        self.assertEqual(len(state['sections']), 2)
        self.assertEqual(sum(keys[0] in s['members'] for s in state['sections']), 2)
        self.edit('tags_rename', keys=keys, old='通用', value='常用')
        self.edit('tags_remove', keys=keys, tags=['辅助'])
        self.edit('scan', discard=True)
        self.assertEqual([m.tags for m in self.model.mods], [['常用'], ['常用']])

    def test_theme_default_and_persistence_no_secret_exposed(self):
        self.assertEqual(self.model.snapshot()['theme'], 'system')
        self.edit('theme', value='dark')
        reloaded = Manager(self.model.directory, self.catalog, self.steam)
        self.assertEqual(reloaded.snapshot()['theme'], 'dark')
        self.assertFalse(self.api.request('theme', {'value': 'bad'})['ok'])
        self.api._jobs.api_key = 'a' * 32
        info = self.edit('account_info')['result']
        self.assertTrue(info['session'])
        self.assertNotIn('a' * 32, json.dumps(self.api.request('poll')))
        self.assertNotIn('a' * 32, json.dumps(info))

    def test_empty_poll_is_small_and_unknown_actions_rejected(self):
        result = self.api.request('poll', {'revision': self.model.revision, 'job_version': 0})
        self.assertNotIn('state', result)
        self.assertNotIn('lines', result['job'])
        self.assertFalse(self.api.request('run_shell')['ok'])

    def test_background_preload_and_warm_switch_reuse_models_without_scanning(self):
        self.model._loading['sts2'].result(timeout=5)
        with patch('manager_model.scan', side_effect=AssertionError('Cache should not scan')), \
                patch.object(self.model, 'steam', side_effect=AssertionError('Cache should not call Steam')):
            for _ in range(3):
                self.edit('game', game='sts2')
                self.edit('game', game='sts1')
        self.assertEqual(self.steam.call_count, 1)

    def test_switch_preserves_both_drafts_history_filters_and_sorting(self):
        one_mods, one_history = self.model.mods, self.model.history
        one_key = one_mods[0].key
        self.edit('toggle', keys=[one_key])
        self.edit('view', query='a', mode='自定义分组', group_descending=True)
        self.edit('game', game='sts2')
        self.edit('toggle', keys=[self.model.mods[0].key], value=True)
        self.assertEqual(set(self.model.dirty_games()), {'sts1', 'sts2'})
        two_mods, two_history = self.model.mods, self.model.history
        self.edit('game', game='sts1')
        self.assertIs(self.model.mods, one_mods)
        self.assertIs(self.model.history, one_history)
        self.assertFalse(one_mods[0].enabled)
        self.assertEqual(self.model.query, 'a')
        self.assertTrue(self.model.view['group_descending'])
        self.edit('undo')
        self.assertTrue(one_mods[0].enabled)
        self.assertEqual(self.model.dirty_games(), ['sts2'])
        self.edit('game', game='sts2')
        self.assertIs(self.model.mods, two_mods)
        self.assertIs(self.model.history, two_history)
        self.assertTrue(two_mods[0].enabled)

    def test_refresh_modes_and_optional_cache(self):
        self.assertFalse(self.model.snapshot()['backup_cache_enabled'])
        self.edit('game',game='sts1')
        self.edit('toggle',keys=[self.model.mods[0].key],value=not self.model.mods[0].enabled)
        original=self.model.mods
        with patch.object(self.model,'rescan',side_effect=AssertionError('small refresh must not rescan')), patch('versions.VersionStore.capture',side_effect=AssertionError('small refresh must not cache')):
            r=self.api.request('refresh_small');self.assertTrue(r['ok'],r)
            deadline=time.monotonic()+5
            while self.api._jobs.snapshot()['running'] and time.monotonic()<deadline:time.sleep(.01)
            self.assertFalse(self.api._jobs.snapshot()['running']);self.assertNotIn('未完成',self.api._jobs.snapshot()['summary'])
        self.assertIs(self.model.mods,original);self.assertTrue(self.model.dirty())
        with patch('versions.VersionStore.capture',side_effect=AssertionError('cache disabled')):
            self.edit('scan',discard=True)
            self.assertNotIn('未完成',self.api._jobs.snapshot()['summary'])
        self.edit('cache_options',enabled=True)
        self.assertTrue(self.model.snapshot()['backup_cache_enabled'])
        with patch('versions.VersionStore.capture',return_value={}) as capture:
            self.edit('scan',discard=True);capture.assert_called_once()
        self.edit('cache_options',enabled=False)
        self.assertFalse(self.model.store['backup_cache_enabled'])

    def test_unified_log_catalog_and_safe_read(self):
        logs=self.model.directory/'logs';logs.mkdir(exist_ok=True);(logs/'mod-release-example.log').write_text('exported V1.1',encoding='utf-8')
        r=self.api.request('logs');self.assertTrue(r['ok']);self.assertIn('issues',r['result'])
        self.assertIn('mod-release-example.log',[f['name'] for f in r['result']['files']])
        self.assertEqual(self.api.request('log_read',{'name':'mod-release-example.log'})['result']['text'],'exported V1.1')
        self.assertFalse(self.api.request('log_read',{'name':'../library.json'})['ok'])

    def test_explicit_scan_refreshes_active_game_only(self):
        self.edit('game', game='sts2')
        two_mods = self.model.mods
        self.edit('game', game='sts1')
        original = self.model.mods
        self.edit('scan', discard=True)
        self.assertIsNot(self.model.mods, original)
        self.edit('game', game='sts2')
        self.assertIs(self.model.mods, two_mods)

    def test_cached_session_detects_external_changes_before_write(self):
        original = self.one.read_bytes()
        self.edit('game', game='sts2')
        self.one.write_bytes(original + b' ')
        self.edit('game', game='sts1')
        token = self.edit('apply_preview')['result']['token']
        with patch('manager_model.running_game_processes', return_value=[]):
            self.assertFalse(self.api.request('apply_commit', {'token': token})['ok'])
        self.assertEqual(self.one.read_bytes(), original + b' ')

    def test_close_checks_inactive_game_drafts(self):
        self.api._window = Mock()
        self.edit('toggle', keys=[self.model.mods[0].key])
        self.edit('game', game='sts2')
        self.assertFalse(self.model.dirty())
        self.assertFalse(self.api.close()['ok'])
        self.api._window.destroy.assert_not_called()

    def test_sts1_apply_preserves_other_lists_and_makes_byte_backup(self):
        original = self.one.read_bytes()
        self.edit('toggle', keys=[self.model.mods[1].key], value=True)
        preview = self.edit('apply_preview')['result']
        self.assertEqual(self.one.read_bytes(), original)
        with patch('manager_model.running_game_processes', return_value=[]):
            result = self.edit('apply_commit', token=preview['token'])['result']
        self.assertEqual(Path(result['backup']).read_bytes(), original)
        data = read_json(self.one)
        self.assertEqual(data['lists']['old'], ['a.jar'])
        self.assertEqual(data['lists']['Spire Manager'], ['a.jar', 'b.jar'])
        self.assertFalse(self.model.dirty())
        self.edit('undo')
        self.assertTrue(self.model.dirty())

    def test_stale_preview_and_running_game_block_writes(self):
        original = self.one.read_bytes()
        token = self.edit('apply_preview')['result']['token']
        self.edit('toggle', keys=[self.model.mods[0].key])
        self.assertFalse(self.api.request('apply_commit', {'token': token})['ok'])
        token = self.edit('apply_preview')['result']['token']
        with patch('manager_model.running_game_processes', return_value=['game.exe']):
            self.assertFalse(self.api.request('apply_commit', {'token': token})['ok'])
        self.assertEqual(self.one.read_bytes(), original)

    def test_sts2_specific_folder_selection_and_real_commit_adapter(self):
        self.edit('game', game='sts2', discard=True)
        first, second = self.model.mods
        self.edit('toggle', keys=[first.key], value=True)
        self.edit('toggle', keys=[second.key], value=True)
        self.assertEqual([m.enabled for m in self.model.mods], [False, True])
        original = self.two.read_bytes()
        preview = self.edit('apply_preview')['result']
        after = copy.deepcopy(self.steam_snapshot)
        after['items']['123']['disabled'] = True
        self.steam.side_effect = [copy.deepcopy(self.steam_snapshot), after]
        with patch('manager_model.running_game_processes', return_value=[]):
            result = self.edit('apply_commit', token=preview['token'])['result']
        self.assertEqual(Path(result['backup']).read_bytes(), original)
        self.assertTrue(Path(result['receipt']).exists())
        self.assertEqual(self.steam.call_args.kwargs['changes'], {'123': True, '456': False})
        self.assertEqual(read_json(self.two)['audio'], 7)
        self.assertEqual(read_json(self.two)['mod_settings']['mod_list'], [
            {'id': 'same', 'source': 'steam_workshop', 'is_enabled': True}])

    def test_sts2_batch_conflict_never_writes(self):
        self.edit('game', game='sts2', discard=True)
        original = self.two.read_bytes()
        self.edit('toggle', keys=[m.key for m in self.model.mods], value=True)
        self.assertFalse(self.api.request('apply_preview')['ok'])
        self.assertEqual(self.two.read_bytes(), original)

    def test_title_update_preserves_manual_alias_and_undo(self):
        self.edit('game', game='sts2', discard=True)
        first, second = self.model.mods
        first.package_title, first.alias = 'Old', 'Old'
        second.alias = '手动昵称'
        self.model.history.reset(self.model.editor_state())
        self.model.apply_titles({'123': '控制台', '456': '新标题'})
        self.assertEqual([m.nickname for m in self.model.mods], ['控制台', '手动昵称'])
        self.edit('undo')
        self.assertEqual(first.nickname, 'Old')

    def test_background_titles_keep_completed_results_after_stop(self):
        self.edit('game', game='sts2', discard=True)
        done = threading.Event()
        def fetch(appid, ids, progress, **kwargs):
            progress(1, 2, {'status': 'success', 'id': '123', 'title': '控制台'})
            done.set()
            kwargs['stop_event'].wait(3)
        with patch('web_jobs.fetch_workshop_titles', side_effect=fetch):
            self.edit('titles_start')
            self.assertTrue(done.wait(3))
            self.assertFalse(self.api.request('game', {'game': 'sts1', 'discard': True})['ok'])
            self.edit('job_stop')
            deadline = time.monotonic() + 3
            while self.api._jobs.value['running'] and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertFalse(self.api._jobs.value['running'])
        self.assertEqual(self.model.state['titles']['123'], '控制台')
        self.assertEqual(self.api._jobs.value['done'], 1)
        self.assertIn('待确认 1', self.api._jobs.value['summary'])
        self.assertTrue(self.api._jobs.log_path.exists())


if __name__ == '__main__':
    unittest.main()
