import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from core import scan,atomic_json

class ScanCacheTest(unittest.TestCase):
 def test_jar_reuse_and_change_detection(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);pkg=root/'mods';pkg.mkdir();jar=pkg/'a.jar';cache={}
   def write(path,ident,version):
    with zipfile.ZipFile(path,'w') as z:z.writestr('ModTheSpire.json',json.dumps({'modid':ident,'name':ident,'version':version}))
   write(jar,'a','1');mods,issues=scan('sts1',root,[],manifest_cache=cache);self.assertFalse(issues);self.assertEqual(len(mods),1)
   mods[0].name='edited in UI'
   with patch('core.zipfile.ZipFile',side_effect=AssertionError('unchanged JAR must not reopen')):
    mods,issues=scan('sts1',root,[],manifest_cache=cache)
   self.assertFalse(issues);self.assertEqual(mods[0].name,'a')
   write(jar,'a','version 2');mods,issues=scan('sts1',root,[],manifest_cache=cache);self.assertFalse(issues);self.assertEqual(mods[0].version,'version 2')
   jar.unlink();write(pkg/'b.jar','b','1');mods,issues=scan('sts1',root,[],manifest_cache=cache)
   self.assertEqual([m.id for m in mods],['b']);self.assertNotIn(str(jar.resolve()),cache)
 def test_json_cache_updates_titles_and_keeps_nested_discovery(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);manifest=root/'123/sub/a.json';atomic_json(manifest,{'id':'a','name':'A','version':'1'});cache={}
   mods,issues=scan('sts2','',[root],{'123':'old title'},cache);self.assertFalse(issues)
   with patch('core.read_json',side_effect=AssertionError('unchanged manifest must not reread')):
    mods,issues=scan('sts2','',[root],{'123':'new title'},cache)
   self.assertFalse(issues);self.assertEqual(mods[0].package_title,'new title')
   atomic_json(root/'123/sub/b.json',{'id':'b'});mods,issues=scan('sts2','',[root],manifest_cache=cache)
   self.assertFalse(issues);self.assertEqual({m.id for m in mods},{'a','b'})
if __name__=='__main__':unittest.main()
