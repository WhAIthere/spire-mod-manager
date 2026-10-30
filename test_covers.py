import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from PIL import Image

from covers import CoverCache, fetch_covers, image_open, preview_url


class CoverTests(unittest.TestCase):
    def test_cancelled_image_never_writes_late_response(self):
        from steam_account import RequestCancelled
        entered=threading.Event();release=threading.Event();finished=threading.Event();stop=threading.Event();errors=[]
        def network(url):entered.set();release.wait(5);return io.BytesIO(b'late image')
        with tempfile.TemporaryDirectory() as temp,patch('covers.image_open',side_effect=network):
            cache=CoverCache(temp)
            def run():
                try:cache.download('sts1','1','https://images.steamusercontent.com/a',stop=stop)
                except Exception as exc:errors.append(exc)
                finally:finished.set()
            worker=threading.Thread(target=run);worker.start()
            try:
                self.assertTrue(entered.wait(2));stop.set();self.assertTrue(finished.wait(1.5));self.assertIsInstance(errors[0],RequestCancelled)
                self.assertFalse(cache.path('sts1','1').exists())
            finally:release.set();worker.join(2)

    def test_image_retries_wait_only_for_server_instructions(self):
        from covers import image_retry_delay
        from urllib.error import HTTPError
        self.assertEqual(image_retry_delay(TimeoutError(),0),0)
        self.assertEqual(image_retry_delay(HTTPError('https://example.invalid',503,'error',{},None),0),0)
        self.assertEqual(image_retry_delay(HTTPError('https://example.invalid',429,'limit',{},None),0),2)
        self.assertEqual(image_retry_delay(HTTPError('https://example.invalid',503,'wait',{'Retry-After':'7'},None),0),7)

    def test_only_https_steam_cdn_urls_are_accepted(self):
        self.assertTrue(preview_url('https://images.steamusercontent.com/ugc/123/example'))
        for value in ('http://images.steamusercontent.com/a','https://evil.test/a',
                      'https://images.steamusercontent.com.evil.test/a','https://key@images.steamusercontent.com/a',
                      'file:///c:/secret'):
            self.assertEqual(preview_url(value),'')

    def test_image_request_contains_no_api_credentials(self):
        opener = Mock()
        with patch('covers.urllib.request.build_opener',return_value=opener):
            image_open('https://images.steamusercontent.com/ugc/123/image')
        request = opener.open.call_args.args[0]
        self.assertIsNone(request.get_header('X-webapi-key'))
        self.assertNotIn('key=',request.full_url)

    def test_shared_cache_deduplicates_ids_resumes_and_keeps_key_out(self):
        payload = io.BytesIO()
        Image.new('RGB',(800,600),'teal').save(payload,'PNG')
        query = Mock(request_count=0)
        query.has_cached.return_value = True
        query.return_value = {'status':'success','preview_url':'https://images.steamusercontent.com/ugc/123/image'}
        events = []
        key = 'abcd'*8
        with tempfile.TemporaryDirectory() as temp, patch('covers.AccountTitleQuery',return_value=query), \
                patch('covers.image_open',side_effect=lambda url:io.BytesIO(payload.getvalue())):
            result = fetch_covers(temp,'sts1',['123','123'],key,lambda *e:events.append(e),threading.Event())
            self.assertEqual(result['cached'],1)
            self.assertEqual(query.call_count,1)
            cache = CoverCache(temp)
            with Image.open(cache.path('sts1','123')) as image:
                self.assertEqual(image.size,(320,240))
            self.assertTrue(cache.data('sts1','123').startswith('data:image/webp;base64,'))
            self.assertEqual(cache.available('sts1'),['123'])
            self.assertNotIn(key,json.dumps(events))
            self.assertNotIn(key,(cache.root/'sts1/index.json').read_text())
            fetch_covers(temp,'sts1',['123'],key,lambda *e:None,threading.Event())
            self.assertEqual(query.call_count,1)
            with self.assertRaises(ValueError):cache.data('sts1','../credentials')


if __name__ == '__main__':
    unittest.main()
