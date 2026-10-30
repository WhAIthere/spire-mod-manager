"""Read-only authenticated Workshop metadata. Keys are never placed in URLs/logs."""
import ctypes
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request


def validate_key(key):
    key = key.strip()
    if not re.fullmatch(r"[0-9a-fA-F]{32}", key):
        raise ValueError("请输入 32 位 Steam Web API Key；这里不填写密码或 Cookie。")
    return key


def protect(data, decrypt=False):
    if os.name != "nt":
        raise OSError("此系统不支持 Windows 本机加密，请使用仅本次会话选项。")
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    api = ctypes.WinDLL("crypt32", use_last_error=True)
    api.CryptProtectData.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.POINTER(Blob), ctypes.c_void_p,
                                    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    api.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
                                      ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    api.CryptProtectData.restype = api.CryptUnprotectData.restype = wintypes.BOOL
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        ok = api.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        ok = api.CryptProtectData(ctypes.byref(source), "Spire Manager Steam API", None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise OSError("无法使用当前 Windows 用户加密／解密 Steam Key。")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def save_key(path, key):
    path = Path(path)
    encrypted = protect(validate_key(key).encode("ascii"))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(encrypted)
    temporary.replace(path)


def load_key(path):
    path = Path(path)
    return validate_key(protect(path.read_bytes(), decrypt=True).decode("ascii")) if path.exists() else ""


class RequestCancelled(Exception):
    pass


def cancelable_call(action, stop=None, heartbeat=None):
    """Cancel waiting promptly; a blocked OS socket may finish in a daemon thread."""
    if stop is None:return action()
    import threading
    if stop.is_set():raise RequestCancelled()
    done=threading.Event();result=[]
    def worker():
        try:result.append((True,action()))
        except Exception as exc:result.append((False,exc))
        finally:done.set()
    threading.Thread(target=worker,daemon=True,name='spire-network').start()
    started=time.monotonic();last=0
    while not done.wait(.1):
        if stop.is_set():raise RequestCancelled()
        elapsed=int(time.monotonic()-started)
        if heartbeat and elapsed>last:heartbeat(elapsed);last=elapsed
    if stop.is_set():raise RequestCancelled()
    ok,value=result[0]
    if not ok:raise value
    return value


def network_error_kind(exc):
    import socket,ssl
    reason=exc.reason if isinstance(exc,urllib.error.URLError) else exc
    if isinstance(reason,(TimeoutError,socket.timeout)):return 'timeout','网络请求超时'
    if isinstance(reason,socket.gaierror):return 'dns','DNS 域名解析失败'
    if isinstance(reason,ssl.SSLError):return 'tls','TLS 安全连接失败'
    if isinstance(reason,ConnectionRefusedError):return 'connection_refused','连接被拒绝'
    if isinstance(reason,ConnectionResetError):return 'connection_reset','连接被重置'
    return 'network_error','网络请求失败（'+type(reason).__name__+'）'


def api_open(request):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ValueError("带 Key 的 API 请求不跟随重定向")
    return urllib.request.build_opener(NoRedirect()).open(request, timeout=20)


API_BATCH_SIZE = 50


class AccountTitleQuery:
    def __init__(self, appid, ids, key, previews=False, stop_event=None):
        self.appid = str(appid)
        self.ids = sorted({str(item) for item in ids})
        self.key = validate_key(key)
        self.buffer = {}
        self.request_count = 0
        self.previews = previews
        self.stop_event = stop_event
        self.request_progress = None

    def has_cached(self, item):
        return item in self.buffer

    def set_pending(self, pending):
        self.ids = list(pending)

    def entry(self, item):
        return {"id": item, "url": f"https://steamcommunity.com/sharedfiles/filedetails/?id={item}&l=schinese",
                "source": "account_api", "title": "", "status": "failed", "error": "",
                "api_endpoint": "https://api.steampowered.com/IPublishedFileService/GetDetails/v1/",
                "page_language": "schinese", "locale_verified": True}

    def __call__(self, item):
        if item in self.buffer:
            return self.buffer.pop(item)
        # Already requested IDs are removed after successful batches. Retries can
        # still explicitly request a missing/failed ID again.
        batch = [item] + [other for other in self.ids if other != item and other not in self.buffer][:API_BATCH_SIZE-1]
        params = {"appid": self.appid, "language": 6}
        if self.previews:
            params['return_previews'] = 'true'
        params.update({f"publishedfileids[{i}]": value for i, value in enumerate(batch)})
        endpoint = "https://api.steampowered.com/IPublishedFileService/GetDetails/v1/"
        request = urllib.request.Request(endpoint + "?" + urllib.parse.urlencode(params), headers={
            "x-webapi-key": self.key, "User-Agent": "SpireLocalModManager/0.8"})
        started = time.monotonic()
        entry = self.entry(item)
        try:
            self.request_count += 1
            def read_response():
                with api_open(request) as response:return response.status,json.load(response)
            if self.request_progress:self.request_progress(len(batch),0)
            status,payload=cancelable_call(read_response,self.stop_event,lambda seconds:self.request_progress(len(batch),seconds) if self.request_progress else None)
            details = payload.get("response", {}).get("publishedfiledetails", [])
            by_id = {str(row.get("publishedfileid")): row for row in details}
            for target in batch:
                row = by_id.get(target, {})
                result = self.entry(target)
                result.update(http_status=status, api_result=row.get("result"), batch_size=len(batch), request_id=self.request_count,
                              seconds=round(time.monotonic() - started, 2))
                consumer = row.get("consumer_appid", row.get("consumer_app_id"))
                title = str(row.get("title") or "").strip()
                if consumer is not None and str(consumer) != self.appid:
                    result["error"] = "接口条目所属游戏不匹配，未使用此标题"
                elif row.get("result") == 1 and title:
                    result.update(status="success", title=title, page_title=title)
                    if self.previews:
                        from covers import preview_url
                        result['preview_url'] = preview_url(row.get('preview_url', ''))
                else:
                    result["error"] = f"账号接口未返回可用标题（result={row.get('result')}）；可能私密或不可用，尚未确认下架"
                self.buffer[target] = result
            self.ids = [target for target in self.ids if target not in batch]
            return self.buffer.pop(item)
        except urllib.error.HTTPError as exc:
            entry["http_status"] = exc.code
            entry["error"] = f"账号 API HTTP {exc.code}"
            if exc.code in (401, 403):
                entry.update(halt_queue=True, error=f"账号 API 返回 HTTP {exc.code}，请检查 Key／接口权限；没有切回匿名网页。")
            hint = exc.headers.get("Retry-After") if exc.headers else None
            if hint:
                try:
                    entry["retry_after"] = max(0, float(hint))
                except ValueError:
                    from email.utils import parsedate_to_datetime
                    try:
                        entry["retry_after"] = max(0, parsedate_to_datetime(hint).timestamp() - time.time())
                    except (ValueError, TypeError, OverflowError):
                        pass
            if exc.code == 403 and entry.get("retry_after", 0) > 0:
                entry.pop("halt_queue", None)
                entry["error"] = "账号 API 返回 HTTP 403，并明确要求 Retry-After，等待后重试。"
        except RequestCancelled:
            raise
        except Exception as exc:
            # Do not serialize exception messages/headers/response bodies which
            # could contain credentials echoed by a server or proxy.
            entry["network_error"],reason=network_error_kind(exc)
            entry["error"] = "账号 API 请求失败："+reason
            entry["reason_type"] = type(exc.reason if isinstance(exc,urllib.error.URLError) else exc).__name__
        entry.update(request_id=self.request_count,batch_size=len(batch),batch_ids=batch)
        entry["seconds"] = round(time.monotonic() - started, 2)
        return entry
