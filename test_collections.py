import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from core import atomic_json
from manager_model import Manager
from web_api import Api
from workshop_collections import fetch,merge
from mod_lists import export_list,import_list

class CollectionsTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.catalog={g:{'game_dir':str(self.root/g),'configs':[],'workshop_roots':[str(self.root/'workshop')]} for g in ('sts1','sts2')}
  self.model=Manager(self.root/'data',self.catalog);self.model.rescan(True)
  self.collection={'id':'100','title':'测试合集','items':{'123':{'title':'测试模组','updated':1}},'warnings':[]}
 def test_empty_import_repeat_resolution_and_filters(self):
  merge(self.model,self.collection);merge(self.model,self.collection)
  self.assertEqual(len(self.model.mods),1);self.assertTrue(self.model.mods[0].missing)
  self.assertEqual(self.model.mods[0].tags,['缺失']);self.assertFalse(self.model.mods[0].enabled)
  self.model.set_view({'mode':'合集分组','local_filter':'local'});self.assertEqual(self.model.snapshot()['visible'],[])
  self.model.set_view({'local_filter':'missing'});self.assertEqual(len(self.model.snapshot()['visible']),1)
  path=self.root/'workshop/123/a.jar';path.parent.mkdir(parents=True)
  with zipfile.ZipFile(path,'w') as z:z.writestr('ModTheSpire.json',json.dumps({'modid':'real','name':'Name','version':'1'}))
  self.model.rescan(True);self.assertEqual(len(self.model.mods),1);self.assertFalse(self.model.mods[0].missing)
  self.assertEqual(self.model.mods[0].id,'real');self.assertEqual(self.model.mods[0].collections[0]['id'],'100')
 def test_missing_item_metadata_refresh_without_game_install(self):
  import time
  from unittest.mock import patch
  merge(self.model,self.collection)
  self.model.demo_mode=True
  self.model.store['workshop_access']={'method':'account_api'}
  self.model.state['workshop_health']={'123':{'status':'存疑'}}
  api=Api(self.model)
  api._jobs.api_key='fixture-key'
  with patch('web_jobs.fetch_workshop_titles') as titles,patch('covers.fetch_covers') as covers:
   self.assertTrue(api.request('refresh_small')['ok'])
   deadline=time.monotonic()+5
   while api._jobs.value['running'] and time.monotonic()<deadline:time.sleep(.01)
   self.assertFalse(api._jobs.value['running'])
   self.assertEqual(titles.call_args.args[1],['123'])
   self.assertEqual(covers.call_args.args[2],['123'])
  self.model.apply_titles({'123':'新工坊标题'})
  self.assertEqual(self.model.mods[0].name,'新工坊标题')
  self.assertTrue(self.model.mods[0].missing)

 def test_preview_is_not_mutation_and_export_has_no_files(self):
  self.model.collection_fixture=self.collection;api=Api(self.model)
  result=api.collection_preview('100');self.assertTrue(result['ok']);self.assertEqual(self.model.mods,[])
  self.assertTrue(api.request('collection_import',{'token':result['token']})['ok'])
  data=export_list(self.model);self.assertEqual(data['mods'],[]);self.assertIn('100',data['collections'])
  other=Manager(self.root/'other',self.catalog);other.rescan(True);import_list(other,data)
  self.assertEqual(len(other.mods),1);self.assertTrue(other.mods[0].missing)
  self.model.edit('undo',{});self.assertEqual(len(self.model.mods),0)
 def test_fetch_nested_dedup_game_validation_and_unknown_details(self):
  def request(method,ids):
   if method=='GetCollectionDetails':return {'collectiondetails':[{'publishedfileid':i,'result':1,'children':[{'publishedfileid':'200','filetype':2},{'publishedfileid':'123','filetype':0}]} for i in ids]}
   return {'publishedfiledetails':[{'publishedfileid':i,'result':1,'consumer_app_id':646570,'title':'Title'} for i in ids]}
  result=fetch('https://steamcommunity.com/sharedfiles/filedetails/?id=100','sts1',request)
  self.assertEqual(list(result['items']),['123'])
  with self.assertRaises(ValueError):fetch('100','sts2',request)
  with self.assertRaises(ValueError):fetch('https://evil.example/?id=100','sts1',request)

class OnlineDemoTests(unittest.TestCase):
 def test_demo_borrows_key_only_in_memory_uses_real_fetch_path_and_blocks_game_writes(self):
  from unittest.mock import patch
  from web_app import demo_model
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);official=root/'official';official.mkdir();(official/'library.json').write_text('UNCHANGED')
   with patch('web_app.DATA',official),patch('steam_account.load_key',return_value='test-key-not-real'):
    model=demo_model(root/'demo')
   model.rescan(True);self.assertEqual(model.mods,[]);self.assertFalse(hasattr(model,'collection_fixture'))
   api=Api(model)
   def server(method,ids,key=''):
    self.assertEqual(key,'test-key-not-real')
    if method=='GetCollectionDetails':return {'collectiondetails':[{'publishedfileid':'100','result':1,'children':[{'publishedfileid':'123','filetype':0}]}]}
    return {'publishedfiledetails':[{'publishedfileid':i,'result':1,'consumer_app_id':646570,'title':'Title'} for i in ids]}
   with patch('workshop_collections.post',side_effect=server):result=api.collection_preview('100')
   self.assertTrue(result['ok'],result);self.assertEqual(result['total'],1)
   self.assertTrue(api.request('collection_import',{'token':result['token']})['ok']);self.assertTrue(model.mods[0].missing)
   for action in ('settings','scan','apply_commit','version_rollback','release_info'):
    self.assertFalse(api.request(action,{})['ok'],action)
   self.assertFalse(api.file('version_install',{})['ok'])
   self.assertEqual((official/'library.json').read_text(),'UNCHANGED')
   self.assertFalse((model.directory/'credentials').exists())
   self.assertNotIn('test-key-not-real',(model.directory/'library.json').read_text())

class CollectionTransportTests(unittest.TestCase):
 def test_authenticated_member_request_uses_includechildren_and_normalizes_appid(self):
  from unittest.mock import patch
  import io
  import workshop_collections as c
  payload={'response':{'publishedfiledetails':[{'publishedfileid':'100','result':1,'file_type':2,'consumer_appid':2868840,'num_children':1,'children':[{'publishedfileid':'123'}]}]}}
  def opener(request):
   self.assertIn('includechildren=true',request.full_url)
   self.assertNotIn('test-key',request.full_url)
   return io.BytesIO(json.dumps(payload).encode())
  with patch('steam_account.api_open',side_effect=opener):result=c.post('GetCollectionDetails',['100'],'test-key')
  self.assertEqual(len(result['collectiondetails'][0]['children']),1)
  self.assertEqual(result['collectiondetails'][0]['consumer_app_id'],2868840)
 def test_timeout_names_the_failing_stage(self):
  from unittest.mock import patch
  import workshop_collections as c
  with patch('urllib.request.urlopen',side_effect=TimeoutError()),self.assertRaisesRegex(ValueError,'读取合集成员超时'):
   c.post('GetCollectionDetails',['100'])


class WorkshopDetectionTests(unittest.TestCase):
 def test_auto_switch_and_single_preview(self):
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as temp:
   catalog={g:{'game_dir':'','configs':[],'workshop_roots':[]} for g in ('sts1','sts2')}
   model=Manager(Path(temp),catalog);api=Api(model)
   row={'publishedfileid':'123','result':1,'consumer_appid':2868840,'file_type':0,'title':'Single'}
   with patch('workshop_collections.post',return_value={'publishedfiledetails':[row]}):
    preview=api.collection_preview('123')
   self.assertTrue(preview['ok'],preview)
   self.assertTrue(preview['single'])
   self.assertEqual(model.game_id,'sts2')
   self.assertEqual(model.mods,[])
   result=api.request('collection_import',{'token':preview['token']})
   self.assertTrue(result['ok'],result)
   self.assertEqual(model.mods[0].collections,[])
   self.assertEqual(len(result['result']['keys']),1)
   row['consumer_appid']=440
   with patch('workshop_collections.post',return_value={'publishedfiledetails':[row]}):
    self.assertFalse(api.collection_preview('123')['ok'])
