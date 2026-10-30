import unittest,tempfile,json,zipfile
from pathlib import Path
from versions import VersionStore
from core import Mod
class VersionsTest(unittest.TestCase):
 def test_lifecycle(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d); pkg=root/'package';pkg.mkdir();(pkg/'x').write_text('old');(pkg/'resource').write_text('same')
   m=Mod('sts2','mod','Name','1',pkg/'x','steam_workshop','123',package_root=pkg)
   store=VersionStore(root/'data'); a=store.capture('sts2',[m]);old=store.versions('sts2','mod')[0]
   self.assertEqual(a['added'],1);self.assertEqual(store.capture('sts2',[m])['updated'],0)
   (pkg/'x').write_text('new');m.version='2';self.assertEqual(store.capture('sts2',[m])['updated'],1)
   self.assertFalse((store.root/'objects').exists())
   self.assertEqual(len(list((store.root/'sts2'/'123').glob('*.zip'))),2)
   with zipfile.ZipFile(store.versions('sts2','mod')[-1]['archive']) as z:
    self.assertEqual(z.read('x'),b'old')
   rollback=store.restore(old['package'],old['digest']);self.assertEqual((pkg/'x').read_text(),'old');self.assertEqual((Path(rollback)/'x').read_text(),'new')
   self.assertEqual(store.capture('sts2',[])['missing'],1);self.assertEqual(len(store.versions('sts2','mod')),2)
   # Corruption must fail before touching original.
   archive=Path(store.versions('sts2','mod')[-1]['archive'])
   with zipfile.ZipFile(archive,'w') as z:
    z.writestr('x','corrupt');z.writestr('resource','same')
   with self.assertRaises(ValueError):store.restore(old['package'],old['digest'])
   self.assertEqual((pkg/'x').read_text(),'old')
 def test_migrate_legacy_and_resume(self):
  import io,hashlib
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);pkg=root/'123';pkg.mkdir();(pkg/'x').write_bytes(b'current')
   store=VersionStore(root/'data');h=store.put(io.BytesIO(b'old'))
   store.add('sts2',pkg,{'sub/x.txt':h},[{'id':'m','name':'Name','version':'1','workshop_id':'123'}]);store.save()
   self.assertEqual(store.migrate(),1)
   self.assertFalse((store.root/'objects').exists())
   versions=store.versions('sts2');archive=Path(versions[0]['archive'])
   self.assertEqual(archive.parent.name,'123')
   with zipfile.ZipFile(archive) as z:self.assertEqual(z.read('sub/x.txt'),b'old')
   self.assertEqual(store.migrate(),1)
   store.restore(versions[0]['package'],versions[0]['digest'])
   self.assertEqual((pkg/'sub/x.txt').read_bytes(),b'old')
 def test_migrate_corruption_preserves_legacy(self):
  import io
  with tempfile.TemporaryDirectory() as d:
   store=VersionStore(Path(d));h=store.put(io.BytesIO(b'old'))
   store.add('sts2',Path(d)/'123',{'x':h},[{'id':'m','version':'1','workshop_id':'123'}]);store.save()
   store.blob(h).write_bytes(b'bad')
   with self.assertRaises(ValueError):store.migrate()
   self.assertTrue(store.blob(h).exists())
   self.assertFalse(store.index['packages'][next(iter(store.index['packages']))]['versions'][0].get('archive'))
 def test_import_zip(self):
  import hashlib
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);pkg=root/'123';bundle=root/'old.zip';payload=b'mod'
   manifest={'roots':[{'archive':'sts2/123','game':'sts2','source':str(pkg)}],
    'files':[{'path':'sts2/123/x','sha256':hashlib.sha256(payload).hexdigest()}],
    'catalogs':{'sts2':{'mods':[{'id':'m','version':'1','workshop_id':'123','package_root':str(pkg)}]}}}
   with zipfile.ZipFile(bundle,'w') as z:z.writestr('manifest.json',json.dumps(manifest));z.writestr('sts2/123/x',payload)
   store=VersionStore(root/'data');store.import_bundle(bundle);store.import_bundle(bundle)
   self.assertEqual(len(store.versions('sts2')),1);self.assertFalse((store.root/'objects').exists())
   with zipfile.ZipFile(store.versions('sts2')[0]['archive']) as z:self.assertEqual(z.read('x'),payload)
 def test_extract_independent_files_and_sts1_install(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);pkg=root/'123';pkg.mkdir();(pkg/'a.jar').write_bytes(b'old');(pkg/'asset').write_bytes(b'asset')
   m=Mod('sts1','m','Name','1',pkg/'a.jar','steam_workshop','123',package_root=pkg);store=VersionStore(root/'data');store.capture('sts1',[m]);v=store.versions('sts1')[0]
   (pkg/'a.jar').write_bytes(b'current')
   out=Path(store.extract(v['package'],v['digest'],root/'out'));self.assertEqual((out/'a.jar').read_bytes(),b'old');self.assertEqual((pkg/'a.jar').read_bytes(),b'current')
   with self.assertRaises(ValueError):store.extract(v['package'],v['digest'],root/'out')
   store.extract(v['package'],v['digest'],root/'mods',install_sts1=True);self.assertEqual((root/'mods/a.jar').read_bytes(),b'old')
   with self.assertRaises(ValueError):store.extract(v['package'],v['digest'],root/'mods',install_sts1=True)
   self.assertEqual((root/'mods/a.jar').read_bytes(),b'old')
 def test_extract_corruption_leaves_destination_clean(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);pkg=root/'123';pkg.mkdir();(pkg/'x').write_bytes(b'old');m=Mod('sts2','m','Name','1',pkg/'x','steam_workshop','123',package_root=pkg);store=VersionStore(root/'data');store.capture('sts2',[m]);v=store.versions('sts2')[0]
   with zipfile.ZipFile(v['archive'],'w') as z:z.writestr('x',b'bad')
   with self.assertRaises(ValueError):store.extract(v['package'],v['digest'],root/'out')
   self.assertEqual(list((root/'out').iterdir()),[])
 def test_unsafe(self):
  from versions import safe_relative
  for p in ['../x','/x','C:/x','a\\b']:
   with self.assertRaises(ValueError):safe_relative(p)
if __name__=='__main__': unittest.main()
