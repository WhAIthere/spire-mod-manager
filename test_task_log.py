import unittest
from task_log import TaskLog
class TaskLogTests(unittest.TestCase):
 def test_wait_replaces_row_and_fifty_successes_are_one_batch(self):
  view=TaskLog()
  for seconds in range(5):view.title({'id':'1','status':'requesting','request_id':1,'batch_size':50,'seconds':seconds})
  self.assertEqual(len(view.rows),1);self.assertIn('4 秒',view.snapshot(True)['rows'][0]['text'])
  for i in range(50):view.title({'id':str(i),'status':'success','request_id':1,'batch_size':50})
  result=view.snapshot();self.assertEqual(len(result['rows']),1);self.assertIn('50 项全部更新成功',result['rows'][0]['text']);self.assertEqual(result['problems'],[])
 def test_retry_and_unresolved_ids_recover(self):
  view=TaskLog();view.title({'id':'1','request_id':1,'status':'retry','batch_ids':['1','2'],'error':'网络超时'})
  self.assertEqual(view.snapshot()['problems'],[])
  view.title({'id':'2','status':'success','request_id':2,'batch_size':2})
  view.title({'id':'1','request_id':1,'status':'deferred','batch_ids':['1','2'],'error':'未确认'})
  self.assertEqual([p['id'] for p in view.snapshot()['problems']],['1'])
  view.title({'id':'1','status':'success','request_id':2,'batch_size':2});self.assertEqual(view.snapshot()['problems'],[])
 def test_cover_and_title_failures_are_independent(self):
  view=TaskLog();view.cover(0,2,'1','图片超时');view.title({'id':'1','status':'success','batch_size':1})
  self.assertEqual(len(view.snapshot()['problems']),1)
  view.cover(2,2,'1','封面已缓存');self.assertEqual(view.snapshot()['problems'],[])

class AttentionRegressionTests(unittest.TestCase):
 def test_cached_covers_and_recovered_api_error_have_no_attention(self):
  view=TaskLog();view.title({'id':'1','status':'retry','request_id':1,'batch_ids':['1'],'error':'账号 API 请求失败：网络请求超时'})
  view.title({'id':'1','status':'success','request_id':2,'batch_size':1})
  view.cover(61,61,'缓存','已有 61 项，无需重复下载')
  self.assertEqual(view.snapshot()['problems'],[])
  self.assertEqual(view.snapshot()['rows'][-1]['status'],'success')
  self.assertNotIn('需要关注',view.snapshot()['rows'][-1]['text'])
 def test_only_finished_unresolved_items_are_shown(self):
  view=TaskLog();view.title({'id':'123','status':'deferred','error':'没有可用标题'})
  self.assertEqual(view.snapshot(True)['problems'],[])
  self.assertEqual(view.snapshot(False)['problems'][0]['id'],'123')

class OldLogTests(unittest.TestCase):
 def test_false_cache_warning_in_saved_history_is_cleaned(self):
  from task_log import clean_saved_view
  old={'rows':[{'id':'covers','text':'封面已处理61/61项 · 有项目需要关注','status':'warning'}],'problems':[{'id':'缓存','phase':'封面','reason':'已有 61 项，无需重复下载'}]}
  result=clean_saved_view(old);self.assertEqual(result['problems'],[]);self.assertEqual(result['rows'][0]['status'],'success');self.assertIn('无需重复下载',result['rows'][0]['text']);self.assertTrue(old['problems'])
