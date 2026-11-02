import copy
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from core import atomic_json
from manager_model import Manager
from mod_lists import export_list, import_list, normalize, preview


class ModListTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.game=self.root/'game'
        self.config=self.root/'private/config.json'
        atomic_json(self.config,{'defaultList':'old','lists':{'old':[]}})
        self.jar('a')
        self.catalog={g:{'game_dir':str(self.game),'configs':[str(self.config)] if g=='sts1' else [],'workshop_roots':[]} for g in ('sts1','sts2')}
        self.model=Manager(self.root/'data',self.catalog);self.model.rescan(True)

    def jar(self,ident):
        path=self.game/'mods'/(ident+'.jar');path.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(path,'w') as z:z.writestr('ModTheSpire.json',json.dumps({'modid':ident,'name':ident,'version':'1'}))

    def data(self):
        data=export_list(self.model)
        row=copy.deepcopy(data['mods'][0]);row.update(id='new',name='待获取模组',file='new.jar',enabled=True,group='推荐',tags=['功能'],alias='新模组')
        data['mods'].append(row)
        return data

    def test_portable_export_excludes_machine_information_and_backup_index(self):
        self.model.store['credentials']={'key':'DO_NOT_EXPORT'}
        self.model.state['release_index']={'private':'DO_NOT_EXPORT'}
        self.model.mods[0].tags=['我的标签'];self.model.edit('profile_save',{'name':'方案'})
        data=export_list(self.model);text=json.dumps(data)
        self.assertNotIn(str(self.root),text);self.assertNotIn('DO_NOT_EXPORT',text)
        self.assertNotIn('path',data['mods'][0]);self.assertIn('方案',data['profiles'])

    def test_missing_survives_restart_is_locked_and_resolves_after_download(self):
        data=self.data();self.assertEqual(preview(self.model,data)['missing'],1)
        original=self.config.read_bytes();import_list(self.model,data)
        missing=next(m for m in self.model.mods if m.missing)
        self.assertIn('缺失',missing.tags);self.assertFalse(missing.enabled);self.assertTrue(missing.wanted_enabled)
        with self.assertRaisesRegex(ValueError,'系统状态'):self.model.edit('tags_remove',{'keys':[missing.key],'tags':['缺失']})
        self.model.prepare_apply() # Missing file never participates in game configuration.
        self.assertEqual(original,self.config.read_bytes())
        other=Manager(self.root/'data',self.catalog);other.rescan(True)
        self.assertEqual(len([m for m in other.mods if m.missing]),1)
        self.jar('new');other.rescan(True)
        found=next(m for m in other.mods if m.id=='new')
        self.assertFalse(found.missing);self.assertNotIn('缺失',found.tags)
        self.assertEqual(found.alias,'新模组');self.assertTrue(found.enabled);self.assertTrue(other.dirty())
        self.assertEqual(original,self.config.read_bytes())

    def test_merge_replace_repeat_and_undo(self):
        self.model.mods[0].alias='本地昵称';self.model.persist_metadata()
        data=self.data();data['mods'][0].update(alias='分享昵称',tags=['分享标签'])
        import_list(self.model,data)
        self.assertEqual(self.model.mods[0].alias,'本地昵称')
        self.assertIn('分享标签',self.model.mods[0].tags)
        self.model.edit('undo',{})
        self.assertFalse(any(m.missing for m in self.model.mods))
        self.model.edit('redo',{})
        self.assertTrue(any(m.missing for m in self.model.mods))
        import_list(self.model,data,overwrite=True)
        import_list(self.model,data,overwrite=True)
        self.assertEqual(len(self.model.mods),2)
        self.assertEqual(self.model.mods[0].alias,'分享昵称')

    def test_bad_data_rejected_before_modifying_model(self):
        for field,value in [('file','../../private/key'),('file','C:/private/key'),('source','unknown'),('tags','not-a-list')]:
            data=self.data();data['mods'][0][field]=value
            with self.assertRaises(ValueError):import_list(self.model,data)
            self.assertEqual(len(self.model.mods),1)
        with self.assertRaises(ValueError):normalize(self.data(),'sts2')

    def test_missing_list_available_even_without_game_configuration(self):
        data=self.data();data['game']='sts2'
        model=Manager(self.root/'fresh',self.catalog);model.change_game('sts2')
        import_list(model,data)
        self.assertEqual(len(model.mods),2)
        model.rescan(True)
        self.assertEqual(model.snapshot()['inventory']['missing'],2)

    def test_unchecked_switch_import_does_not_enable_new_download(self):
        import_list(self.model,self.data(),switches=False)
        self.jar('new');self.model.rescan(True)
        self.assertFalse(next(m for m in self.model.mods if m.id=='new').enabled)

    def test_matching_respects_workshop_folder_and_ambiguous_local_versions(self):
        from core import Mod
        from mod_lists import match,record
        mods=[Mod('sts2','same','同名','1',Path(folder)/file,'steam_workshop',folder,package_root=Path(folder))
              for folder,file in [('123','one.json'),('123','two.json'),('456','one.json')]]
        row=record(mods[0]);self.assertIs(match(row,mods),mods[0])
        row['file']='unknown.json';self.assertIsNone(match(row,mods))
        row['workshop_id']='456';self.assertIs(match(row,mods),mods[2])

    def test_forget_missing_can_be_undone_and_does_not_change_local_files(self):
        from mod_lists import forget_missing
        import_list(self.model,self.data())
        missing=next(m for m in self.model.mods if m.missing)
        before=self.config.read_bytes()
        forget_missing(self.model,[missing.key])
        self.assertFalse(any(m.missing for m in self.model.mods))
        self.model.edit('undo',{})
        self.assertTrue(any(m.missing for m in self.model.mods))
        self.assertEqual(self.config.read_bytes(),before)
