"""Shared Workshop thumbnails. API credentials never reach CDN requests or cache files."""
import base64
import io
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

from core import APP_IDS, atomic_json, read_json
from steam_account import AccountTitleQuery, load_key


def image_retry_delay(error, attempt):
    """No artificial pause for network failures; only server-directed backoff."""
    if not isinstance(error, urllib.error.HTTPError):return 0
    retry_after=0
    hint=error.headers.get('Retry-After') if error.headers else None
    if hint:
        try:retry_after=max(0,float(hint))
        except ValueError:
            from email.utils import parsedate_to_datetime
            try:retry_after=max(0,parsedate_to_datetime(hint).timestamp()-time.time())
            except (ValueError,TypeError,OverflowError):pass
    return max(retry_after,min(30,2*(attempt+1)) if error.code==429 else 0)


def preview_url(value):
    try:
        url = urllib.parse.urlsplit(str(value))
        host = (url.hostname or '').lower()
        trusted = any(host == suffix or host.endswith('.' + suffix) for suffix in
                      ('steamusercontent.com', 'steamstatic.com', 'steamcontent.com', 'steamuserimages-a.akamaihd.net'))
        return str(value) if trusted and url.scheme == 'https' and not url.username and not url.password and url.port in (None,443) else ''
    except ValueError:
        return ''


def image_open(url):
    class CDNRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not preview_url(newurl):
                raise ValueError('图片跳转到非 Steam 图片域名')
            return super().redirect_request(req,fp,code,msg,headers,newurl)
    if not preview_url(url):
        raise ValueError('图片地址无效')
    # Deliberately construct a fresh unauthenticated request; never reuse API headers.
    return urllib.request.build_opener(CDNRedirect()).open(
        urllib.request.Request(url,headers={'User-Agent':'SpireLocalModManager/1.1'}),timeout=20)


class CoverCache:
    def __init__(self, directory):
        self.root = Path(directory) / 'covers'

    def path(self, game, item):
        if game not in APP_IDS or not str(item).isdecimal() or len(str(item)) > 24:
            raise ValueError('未知游戏或工坊编号')
        return self.root / game / (str(item) + '.webp')

    def available(self, game):
        return sorted(p.stem for p in (self.root / game).glob('*.webp') if p.stem.isdecimal())

    def data(self, game, item):
        path = self.path(game,item)
        if not path.exists():
            return ''
        if path.stat().st_size > 512*1024:
            return ''
        payload = path.read_bytes()
        return 'data:image/webp;base64,' + base64.b64encode(payload).decode('ascii')

    def download(self, game, item, url, stop=None):
        from PIL import Image, ImageOps
        from steam_account import cancelable_call,RequestCancelled
        def read_image():
            with image_open(url) as response:return response.read(12*1024*1024+1)
        payload=cancelable_call(read_image,stop)
        if len(payload) > 12*1024*1024:
            raise ValueError('封面原图超过大小限制')
        with Image.open(io.BytesIO(payload)) as source:
            if source.width * source.height > 16_000_000 or source.format not in ('PNG','JPEG','WEBP','GIF'):
                raise ValueError('封面格式或尺寸不支持')
            image = ImageOps.exif_transpose(source)
            image.thumbnail((320,320))
            image = image.convert('RGBA')
            output = io.BytesIO()
            image.save(output,'WEBP',quality=86,method=4)
        if stop and stop.is_set():raise RequestCancelled()
        target = self.path(game,item)
        target.parent.mkdir(parents=True,exist_ok=True)
        temporary = target.with_suffix('.tmp')
        temporary.write_bytes(output.getvalue())
        temporary.replace(target)
        return target.stat().st_size


def fetch_covers(directory, game, ids, key, progress, stop, refresh=False):
    cache = CoverCache(directory)
    index_path = cache.root / game / 'index.json'
    index = read_json(index_path) if index_path.exists() else {}
    ids = sorted({str(i) for i in ids if str(i).isdecimal()})
    pending = [i for i in ids if refresh or not cache.path(game,i).exists() and index.get(i,{}).get('status') != 'unavailable']
    total, done = len(ids), len(ids)-len(pending)
    progress(done,total,'缓存',f'已有 {done} 项，无需重复下载')
    query = AccountTitleQuery(APP_IDS[game],pending,key,previews=True,stop_event=stop)
    failures = 0
    from concurrent.futures import ThreadPoolExecutor, as_completed
    def record(item,status,error=''):
        nonlocal done,failures
        index[item] = {'status':status,'updated':int(time.time()),'error':error}
        atomic_json(index_path,index)
        done += 1
        failures += status == 'failed'
        progress(done,total,item,{'success':'封面已缓存','unavailable':'此条目没有可用封面','failed':error}[status])
    def download(item,url):
        error = ''
        for attempt in range(3):
            if stop.is_set():
                return 'cancelled','已停止，稍后可补齐'
            try:
                cache.download(game,item,url,stop=stop)
                return 'success',''
            except Exception as exc:
                from steam_account import RequestCancelled
                if isinstance(exc,RequestCancelled) or stop.is_set():return 'cancelled','已停止'
                error = '图片下载失败：' + type(exc).__name__
                delay=image_retry_delay(exc,attempt)
                if attempt < 2 and delay and stop.wait(delay):
                    break
        return 'failed',error
    # Metadata remains serial/batched; only unauthenticated image transfers overlap.
    with ThreadPoolExecutor(max_workers=3,thread_name_prefix='spire-image') as pool:
        futures = {}
        for item in pending:
            if stop.is_set():
                break
            from steam_account import RequestCancelled
            query.request_progress=lambda size,seconds:progress(done,total,item,f'已发送 API 请求（{size} 项），等待网络返回 · 已用 {seconds} 秒')
            try:entry = query(item)
            except RequestCancelled:break
            for attempt in range(3):
                if entry.get('http_status') != 429 and not entry.get('retry_after'): break
                from workshop_queue import retry_delay
                delay=retry_delay(entry,attempt+1,'account_api')
                progress(done,total,item,f'接口要求等待 {delay} 秒后重试')
                if stop.wait(delay): break
                try:entry=query(item)
                except RequestCancelled:break
            if stop.is_set(): break
            if entry.get('halt_queue'):
                progress(done,total,item,entry.get('error','请求暂停，可稍后续跑'))
                break
            if entry['status']=='success':progress(done,total,item,f"封面信息已返回 · 本批 {entry.get('batch_size',1)} 项")
            if entry['status'] != 'success':
                record(item,'failed',entry.get('error','元数据查询失败'))
            elif not entry.get('preview_url'):
                record(item,'unavailable')
            else:
                futures[pool.submit(download,item,entry['preview_url'])] = item
        for future in as_completed(futures):
            status,error = future.result()
            if status!='cancelled':record(futures[future],status,error)
    return {'total':total,'processed':done,'cached':sum(cache.path(game,i).exists() for i in ids),'failed':failures}
