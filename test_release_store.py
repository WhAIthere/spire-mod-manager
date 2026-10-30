import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from core import Mod
from versions import VersionStore
from release_store import export_release, register_part, pending, info

class ReleaseTest(unittest.TestCase):
 def setup_store(self,root):
  pkg=root/'123';pkg.mkdir();(pkg/'mod.dll').write_bytes(b'first-version')
  m=Mod('sts2','m','Name','1',pkg/'mod.dll','steam_workshop','123',package_root=pkg)
  store=VersionStore(root/'data');store.capture('sts2',[m]);return store,pkg,m
 def test_incremental_offline_restore(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);store,pkg,m=self.setup_store(root);old=store.versions('sts2')[0]
   result=export_release(store,root/'exports');self.assertEqual(result['versions'],1)
   self.assertEqual(result['batch'],'V1');self.assertEqual({p.name for p in Path(result['directory']).iterdir()},{'V1.1.zip'})
   self.assertTrue((store.root/'releases/backup-catalog.json').is_file())
   self.assertFalse(Path(old['archive']).exists());self.assertEqual(len(pending(store)),0)
   self.assertEqual(export_release(store,root/'exports')['versions'],0)
   store.capture('sts2',[m]);self.assertFalse(Path(old['archive']).exists())
   (pkg/'mod.dll').write_bytes(b'second-version');m.version='2';store.capture('sts2',[m])
   self.assertEqual(len(pending(store)),1)
   result2=export_release(store,root/'exports');self.assertEqual(result2['versions'],1);self.assertEqual(result2['batch'],'V2')
   self.assertNotEqual(result['batch'],result2['batch'])
   part=next(Path(result['directory']).glob('*.zip'));moved=root/'downloaded.zip';part.rename(moved)
   with self.assertRaisesRegex(FileNotFoundError,'请找回模组包'):store.restore(old['package'],old['digest'])
   self.assertEqual((pkg/'mod.dll').read_bytes(),b'second-version')
   self.assertEqual(register_part(store,moved)['versions'],1)
   extracted=Path(store.extract(old['package'],old['digest'],root/'extracted'));self.assertEqual((extracted/'mod.dll').read_bytes(),b'first-version')
   store.restore(old['package'],old['digest']);self.assertEqual((pkg/'mod.dll').read_bytes(),b'first-version')
   reloaded=VersionStore(root/'data');self.assertEqual(len(pending(reloaded)),0)
   self.assertEqual(len(info(reloaded)['releases']),2)
   with zipfile.ZipFile(moved) as z:self.assertIn('manifest.json',z.namelist());self.assertEqual(len(z.namelist()),2)
   moved.unlink();store.capture('sts2',[m]);self.assertEqual(len(pending(store)),0)
   self.assertFalse(Path(old['archive']).exists())
 def test_lost_index_rebuild_and_extract_without_old_paths(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);source,pkg,m=self.setup_store(root);r=export_release(source,root/'exports');part=next(Path(r['directory']).glob('*.zip'))
   fresh=VersionStore(root/'new-data');self.assertEqual(register_part(fresh,part)['added'],1)
   v=fresh.versions('sts2')[0];self.assertTrue(fresh.index['packages'][v['package']]['external'])
   self.assertFalse(Path(fresh.index['packages'][v['package']]['source']).is_absolute())
   output=Path(fresh.extract(v['package'],v['digest'],root/'output'));self.assertEqual((output/'mod.dll').read_bytes(),b'first-version')
   with self.assertRaises(ValueError):fresh.restore(v['package'],v['digest'])
   self.assertEqual(register_part(fresh,part)['added'],0);self.assertEqual(len(fresh.versions('sts2')),1)
   self.assertEqual(len(pending(fresh)),0);self.assertEqual(fresh.index['release_sequence'],1)
 def test_many_packs_skip_damage_and_duplicates(self):
  from release_store import register_many
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);source,pkg,m=self.setup_store(root);r=export_release(source,root/'exports');part=next(Path(r['directory']).glob('*.zip'));bad=root/'bad.zip';bad.write_bytes(b'bad')
   fresh=VersionStore(root/'new-data');result=register_many(fresh,[bad,part,part])
   self.assertEqual(result['packs'],1);self.assertEqual(result['added'],1);self.assertEqual(len(result['errors']),1)
 def test_colliding_pack_names_preserve_both(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);a=root/'a';a.mkdir();source,pkg,m=self.setup_store(a);r=export_release(source,a/'exports');p1=next(Path(r['directory']).glob('*.zip'))
   b=root/'b';b.mkdir();other,pkg,m=self.setup_store(b);other.index['packages'].clear();other.index['inventory'].clear();(pkg/'mod.dll').write_bytes(b'different');other.capture('sts2',[m]);r2=export_release(other,b/'exports');p2=next(Path(r2['directory']).glob('*.zip'))
   fresh=VersionStore(root/'fresh');register_part(fresh,p1);r2=register_part(fresh,p2)
   self.assertNotEqual(r2['part'],'V1.1.zip');self.assertEqual(len(fresh.versions('sts2')),2)
 def test_legacy_embedded_name_alias(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);store,pkg,m=self.setup_store(root);r=export_release(store,root/'exports');part=next(Path(r['directory']).glob('*.zip'))
   with zipfile.ZipFile(part) as z:
    manifest=json.loads(z.read('manifest.json'));entry=manifest['entries'][0]['entry'];payload=z.read(entry)
   manifest['part']='old-long-name-part001.zip';store.index['part_aliases']={manifest['part']:'V1.1.zip'}
   with zipfile.ZipFile(part,'w') as z:z.writestr('manifest.json',json.dumps(manifest));z.writestr(entry,payload)
   self.assertEqual(register_part(store,part)['part'],'V1.1.zip')
 def test_split_and_corrupt_export_is_not_recorded(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);store,pkg,m=self.setup_store(root)
   (pkg/'mod.dll').write_bytes(b'other');store.capture('sts2',[m])
   result=export_release(store,root/'exports',limit=1);self.assertEqual(result['parts'],2)
   for part in Path(result['directory']).glob('*.zip'):
    with zipfile.ZipFile(part) as z:self.assertEqual(len(json.loads(z.read('manifest.json'))['entries']),1)
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);store,pkg,m=self.setup_store(root);v=store.versions('sts2')[0];Path(v['archive']).write_bytes(b'bad')
   with self.assertRaises(zipfile.BadZipFile):export_release(store,root/'exports')
   self.assertEqual(len(pending(store)),1);self.assertTrue(Path(v['archive']).exists());self.assertFalse(store.index.get('releases'))
 def test_import_tampered_pack_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);store,pkg,m=self.setup_store(root);result=export_release(store,root/'exports');part=next(Path(result['directory']).glob('*.zip'))
   with zipfile.ZipFile(part) as z:manifest=z.read('manifest.json')
   fake=root/'bad.zip'
   with zipfile.ZipFile(fake,'w') as z:z.writestr('manifest.json',manifest);z.writestr(json.loads(manifest)['entries'][0]['entry'],b'bad')
   with self.assertRaises(ValueError):register_part(store,fake)
   self.assertEqual(Path(store.index['release_locations'][part.name]),part)
if __name__=='__main__':unittest.main()
