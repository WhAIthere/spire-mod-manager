import copy
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from core import atomic_json,Mod
from manager_model import Manager
from web_api import Api

class SubscriptionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.game=self.root/'game';self.game.mkdir();self.config=self.root/'steam/00000000000000000/settings.save'
  atomic_json(self.config,{'mod_settings':{'mods_enabled':True,'mod_list':[]}})
  self.catalog={g:{'game_dir':str(self.game),'configs':[str(self.config)] if g=='sts2' else [],'workshop_roots':[]} for g in ('sts1','sts2')}
  self.state={'appid':'2868840','account':'00000000000000000','items':{}};self.calls=[]
  def steam(game,command='snapshot',**options):
   if command=='subscribe':
    self.calls.append(options['item']);self.state['items'][options['item']]={'disabled':False,'installed':False,'state':1,'path':''}
    return {'status':'subscribed','item':options['item']}
   return copy.deepcopy(self.state)
  self.model=Manager(self.root/'data',self.catalog,steam);self.model.change_game('sts2');self.model.rescan(True)
  self.model.mods=[Mod('sts2','workshop:123','测试模组','',Path('__missing__/123/x'),'steam_workshop','123',missing=True,loadable=False)]
  self.api=Api(self.model)
  self.metadata=patch('subscriptions.post',return_value={'publishedfiledetails':[{'publishedfileid':'123','result':1,'consumer_app_id':2868840,'file_type':0}]});self.metadata.start();self.addCleanup(self.metadata.stop)
  self.processes=patch('subscriptions.running_game_processes',return_value=[]);self.processes.start();self.addCleanup(self.processes.stop)
 def preview(self):
  result=self.api.request('subscription_preview',{'keys':[self.model.mods[0].key]});self.assertTrue(result['ok'],result);return result['result']
 def test_preview_does_not_subscribe_and_confirm_runs_once(self):
  original=self.config.read_bytes();plan=self.preview();self.assertEqual(self.calls,[])
  result=self.api.request('subscription_start',{'token':plan['token']});self.assertTrue(result['ok'],result)
  end=time.monotonic()+5
  while self.api._jobs.value['running'] and time.monotonic()<end:time.sleep(.01)
  self.assertEqual(self.calls,['123']);self.assertEqual(self.config.read_bytes(),original)
  self.assertFalse(self.api.request('subscription_start',{'token':plan['token']})['ok'])
 def test_account_change_stale_and_demo_are_rejected(self):
  plan=self.preview();self.state['account']='11111111111111111'
  self.assertFalse(self.api.request('subscription_start',{'token':plan['token']})['ok']);self.assertEqual(self.calls,[])
  self.state['account']='00000000000000000';plan=self.preview();self.model.changed()
  self.assertFalse(self.api.request('subscription_start',{'token':plan['token']})['ok']);self.assertEqual(self.calls,[])
  self.model.demo_mode=True;self.assertFalse(self.api.request('subscription_preview',{'keys':[self.model.mods[0].key]})['ok'])
  self.assertFalse(self.api.request('subscription_start',{'token':'anything'})['ok'])
 def test_preview_marks_existing_subscription(self):
  self.state['items']['123']={'disabled':False,'installed':False,'state':1,'path':''}
  self.assertTrue(self.preview()['items'][0]['already']);self.assertEqual(self.calls,[])
 def test_subscription_view_filters_by_subscription_not_installation(self):
  key=self.model.mods[0].key
  result=self.api.request('subscription_mode',{'enabled':True});self.assertTrue(result['ok'],result)
  self.assertEqual(result['state']['visible'],[key]);self.assertTrue(result['state']['subscription_mode'])
  self.assertFalse(self.api.request('toggle',{'keys':[key]})['ok'])
  self.state['items']['123']={'disabled':False,'installed':False,'state':1,'path':''}
  result=self.api.request('subscription_mode',{'enabled':True});self.assertEqual(result['state']['visible'],[])
  result=self.api.request('subscription_mode',{'enabled':False});self.assertEqual(result['state']['visible'],[key])
 def test_wrong_game_metadata_rejected(self):
  with patch('subscriptions.post',return_value={'publishedfiledetails':[{'publishedfileid':'123','result':1,'consumer_app_id':646570,'file_type':0}]}):
   self.assertFalse(self.api.request('subscription_preview',{'keys':[self.model.mods[0].key]})['ok'])
  self.assertEqual(self.calls,[])
