"""Resumable, paced title queries. Only positive titles/removal evidence are terminal."""
import copy
import random
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from core import atomic_json, read_json


TERMINAL = ("success", "removed")


def has_chinese(title):
    return bool(re.search(r"[\u3400-\u9fff]", title))


def needs_chinese_page_check(entry):
    if entry.get("status") != "success" or has_chinese(entry.get("title", "")):
        return False
    verified = entry.get("locale_verified") or (
        entry.get("source") == "web_schinese" and
        entry.get("page_language", "").lower().replace("_", "-") in ("zh-cn", "zh-hans", "schinese"))
    return not verified


def retry_delay(entry, failures, transport="web_schinese"):
    # Respect the server's Retry-After even if it exceeds our normal backoff cap.
    base, cap = (2, 30) if transport == "account_api" else (120, 1800)
    rate_delay = min(cap, base * 2 ** min(failures - 1, 6)) if entry.get("http_status") == 429 else 0
    return max(float(entry.get("retry_after", 0)), rate_delay)


def run_title_queue(appid, ids, progress=None, cached_titles=None, *, checkpoint=None,
                    stop_event=None, force=False, query=None, clock=time.time, wait=None, interval=None,
                    transport="web_schinese", max_rounds=3):
    if max_rounds < 1: raise ValueError("重试轮数必须大于零")
    attempts_this_run = {}
    stop = stop_event or threading.Event()
    wait = wait or stop.wait
    interval = interval or ((lambda: 0) if transport == "account_api" else (lambda: random.uniform(5, 10)))
    ids = sorted({str(item) for item in ids if str(item).isdecimal()})
    path = Path(checkpoint) if checkpoint else None
    state = read_json(path) if path and path.exists() else {"appid": str(appid), "items": {}}
    if str(state.get("appid")) != str(appid):
        raise ValueError("工坊队列与游戏不匹配，未覆盖原记录")
    items = state["items"]
    if state.get("transport", "web_schinese") != transport:
        state.pop("cooldown_until", None)
        state.pop("cooldown_code", None)
        for entry in items.values():
            if entry.get("status") not in TERMINAL:
                entry.update(next_attempt=0, failures=0, rate_failures=0, unclear_failures=0)
    state["transport"] = transport
    # Migrate the old policy: 403, timeouts and an unparsed HTTP 200 are not
    # rate-limit evidence. Discard only waits attributable to those responses.
    old_deadline = state.get("cooldown_until", 0)
    old_evidence = [entry for entry in items.values()
                    if entry.get("status") == "retry" and entry.get("next_attempt") == old_deadline]
    if old_evidence and not any(e.get("http_status") == 429 or e.get("retry_after", 0) > 0 for e in old_evidence):
        state.pop("cooldown_until", None)
        state.pop("cooldown_code", None)
    for entry in items.values():
        if entry.get("status") == "retry" and entry.get("http_status") != 429 and not entry.get("retry_after", 0):
            entry["next_attempt"] = 0
    one_attempt = {item for item in ids if items.get(item, {}).get("suspected") or items.get(item, {}).get("status") == "removed"}
    if transport == "account_api":
        state["next_request_at"] = 0
    if force:
        for item in ids:
            if items.get(item, {}).get("status") in TERMINAL:
                items.pop(item, None)
        cached_titles = {}
    result = {}
    confirmed = set()

    def save():
        if path:
            state["updated"] = datetime.now().isoformat(timespec="seconds")
            atomic_json(path, state)

    def emit(entry):
        if progress:
            progress(len(confirmed), len(ids), copy.deepcopy(entry))

    # Replay saved confirmations too: an interrupted UI may not have applied the
    # last worker result. This also applies new nickname rules without re-querying.
    for item in ids:
        if stop.is_set():
            break
        saved = items.get(item, {})
        cached = (cached_titles or {}).get(item, "")
        if needs_chinese_page_check(saved):
            saved = dict(saved, status="pending", refresh_reason="旧英文缓存未经中文页面核验，补查中文标题")
            items[item] = saved
            save()
            emit(dict(saved, status="refresh", error=saved["refresh_reason"]))
        if saved.get("status") not in TERMINAL and cached and has_chinese(cached) and item not in items:
            saved = {"id": item, "status": "success", "title": cached, "source": "local_cache",
                     "url": f"https://steamcommunity.com/sharedfiles/filedetails/?id={item}&l=schinese",
                     "attempts": 0, "seconds": 0, "error": ""}
            items[item] = saved
            save()
        if saved.get("status") in TERMINAL:
            confirmed.add(item)
            if saved["status"] == "success":
                result[item] = saved["title"]
            emit(dict(saved, resumed=True))

    pending = [item for item in ids if item not in confirmed]
    if callable(getattr(type(query), "set_pending", None)):
        query.set_pending(pending)
    # Pace the first request as well, and preserve pacing across quick restarts.
    next_request = max(state.get("next_request_at", 0), clock() + interval())
    while pending and not stop.is_set():
        now = clock()
        item = min(pending, key=lambda item: (attempts_this_run.get(item, 0), items.get(item, {}).get("next_attempt", 0)))
        buffered = callable(getattr(type(query), "has_cached", None)) and query.has_cached(item)
        due = max(next_request, state.get("cooldown_until", 0), items.get(item, {}).get("next_attempt", 0))
        if buffered:
            due = now
        if due > now:
            remaining = due - now
            server_wait = due == state.get("cooldown_until", 0) or (
                due == items.get(item, {}).get("next_attempt", 0) and due > next_request)
            code = state.get("cooldown_code") or items.get(item, {}).get("http_status") or next((e.get("http_status") for e in old_evidence), None)
            if server_wait:
                reason = (f"Steam 返回 HTTP {code}：" if code else "服务器冷却：")
                reason += "请求过多，等待后重试" if code == 429 else "遵守 Retry-After 等待"
            else:
                reason = "正常请求间隔（不是限流）"
            emit({"id": item, "status": "waiting", "title": "", "error": reason,
                  "wait_kind": "server_cooldown" if server_wait else "request_interval",
                  "wait_seconds": round(remaining), "retry_at": due,
                  "url": f"https://steamcommunity.com/sharedfiles/filedetails/?id={item}&l=schinese"})
            if wait(min(30, remaining)):
                break
            continue
        from steam_account import RequestCancelled, cancelable_call
        try:
            if transport=='account_api' and hasattr(query,'request_progress'):
                query.request_progress=lambda size,seconds:emit({'id':item,'status':'requesting','title':'','batch_size':size,'request_id':query.request_count,'seconds':seconds,'error':f'已发送 API 请求（{size} 项），等待网络返回 · 已用 {seconds} 秒'})
                entry=query(item)
            else:
                emit({'id':item,'status':'requesting','title':'','error':'正在请求网页，等待网络返回'})
                entry=cancelable_call(lambda:query(item),stop)
        except RequestCancelled:break
        if stop.is_set():break
        attempts_this_run[item] = attempts_this_run.get(item, 0) + 1
        entry["round"] = attempts_this_run[item]
        if entry.get("id") != item:
            raise ValueError("工坊查询结果编号与请求不符，已停止；原有进度保留")
        saved = items.get(item, {})
        entry["attempts"] = saved.get("attempts", 0) + 1
        entry["checked_at"] = datetime.now().isoformat(timespec="seconds")
        if not buffered:
            next_request = clock() + interval()
            state["next_request_at"] = next_request
        if entry["status"] in TERMINAL:
            items[item] = entry
            confirmed.add(item)
            pending.remove(item)
            if entry["status"] == "success":
                # query() explicitly reads the schinese page; even an English
                # title is now a verified response rather than an old cache hit.
                entry["locale_verified"] = True
                result[item] = entry["title"]
            save()
            emit(entry)
            continue
        if item in one_attempt:
            entry["suspected"] = True
        failures = saved.get("failures", 0) + 1
        rate_failures = saved.get("rate_failures", 0) + 1 if entry.get("http_status") == 429 else 0
        delay = retry_delay(entry, max(1, rate_failures), transport)
        entry.update(status="retry", failures=failures, rate_failures=rate_failures,
                     next_attempt=clock() + delay, wait_seconds=delay,
                     wait_kind="server_cooldown" if delay else "normal_retry")
        unclear = entry.get("http_status") in (200, 400, 401, 403, 404, 410)
        entry["unclear_failures"] = saved.get("unclear_failures", 0) + 1 if unclear else 0
        items[item] = entry
        if entry.get("http_status") == 429 or entry.get("retry_after", 0) > 0:
            state["cooldown_until"] = entry["next_attempt"]
            state["cooldown_code"] = entry.get("http_status")
        save()
        emit(entry)
        if entry.get("halt_queue"):
            emit(dict(entry, status="halted"))
            break
        # Exhaustion belongs to this item and this run, never to the whole queue.
        # Keep its retry checkpoint so a future run can try it again.
        limit = 1 if item in one_attempt else max_rounds
        if attempts_this_run[item] >= limit:
            if unclear:
                entry['suspected'] = True
                save()
            pending.remove(item)
            emit(dict(entry, status="deferred", error=f"本次已查 {limit} 轮，仍待确认；保留原名称，其他条目继续。原因：" + entry.get("error", "未返回标题")))
    save()
    return result
