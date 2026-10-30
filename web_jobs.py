"""Background jobs and durable logs for the HTML interface; reuse existing queue/archive engines."""
import copy
from datetime import datetime
from pathlib import Path
import threading

from backup import export_bundle, fetch_workshop_titles, verify_bundle
from core import APP_IDS, ConfigSession, atomic_json, cached_workshop_info, scan
from steam_account import load_key
from task_log import TaskLog


class Jobs:
    def __init__(self, model):
        self.model = model
        self.stop = threading.Event()
        self.value = {"kind": "", "running": False, "done": 0, "total": 1, "summary": "暂无后台任务", "lines": [], "version": 0}
        self.report = {}
        self.log_path = None
        self.api_key = ""
        self.user_log=TaskLog()

    def snapshot(self):
        view=self.user_log.snapshot(self.value['running'])
        summary=self.value['summary']
        if self.value['running'] and view['rows']:
            summary=f"进度 {self.value['done']}/{self.value['total']} · "+view['rows'][-1]['text']
        return copy.deepcopy({**self.value,'summary':summary,'lines':self.value['lines'][-300:],'user_log':view})

    def _append(self, text):
        if not self.user_log.rows or set(self.user_log.rows)=={"stage"}:self.user_log.row("stage",str(text))
        self.value["lines"].append(str(text))
        self.value["version"] += 1

    def _save_log(self,finishing=False):
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.report["user_log"]=self.user_log.snapshot(False if finishing else self.value["running"])
        atomic_json(self.log_path.with_suffix(".json"), self.report)
        self.log_path.write_text("\n".join(self.value["lines"]), encoding="utf-8")

    def _begin(self, kind, total):
        if self.value["running"]:
            raise ValueError("已有后台任务，请先停止或等待完成。")
        self.stop = threading.Event()
        self.user_log=TaskLog()
        self.value = {"kind": kind, "running": True, "done": 0, "total": max(1, total),
                      "summary": "正在准备…", "lines": [], "version": self.value["version"] + 1}

    def start_titles(self, overwrite=False, resume=True):
        model = self.model
        ids = [m.workshop_id for m in model.mods if m.workshop_id]
        if not ids:
            raise ValueError("当前游戏没有扫描到工坊模组。")
        api_key = ""
        if model.store.get("workshop_access", {}).get("method") == "account_api":
            api_key = self.api_key or load_key(model.directory / "credentials/steam-api-key.bin")
            if not api_key:
                raise ValueError("请先在工坊账号设置中填写 API Key。")
        cached = {}
        if resume:
            if model.game_id == "sts1":
                for item in cached_workshop_info():
                    cached[Path(item.get("installPath", "")).name] = item.get("title", "")
            cached.update(model.state.get("titles", {}))
        self._begin("titles", len(set(ids)))
        game = model.game_id
        self.log_path = model.directory / "logs" / ("workshop-titles-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".log")
        self.report = {"started": datetime.now().isoformat(), "game": game, "total": len(set(ids)),
                       "overwrite": overwrite, "method": "account_api" if api_key else "web_schinese", "results": [], "changes": []}
        self._append("账号 API 批量读取" if api_key else "匿名简体中文工坊网页")
        self._save_log()

        def worker():
            error = ""
            try:
                def progress(done, total, entry):
                    with model.lock:
                        self.user_log.title(entry)
                        model.workshop_health(entry)
                        changes = []
                        if entry["status"] == "success":
                            changes = model.apply_titles({entry["id"]: entry["title"]}, overwrite, {entry["id"]})
                        self.value.update(done=done, total=total)
                        label = {"success": "已获标题", "removed": "确认下架", "waiting": "等待", "retry": "待重试",
                                 "halted": "暂停", "deferred": "本轮次结束，待确认", "refresh": "补查", "requesting":"等待网络"}.get(entry["status"], entry["status"])
                        self.value["summary"] = f"已确认 {done}/{total} · {label} {entry['id']}"
                        self.report["results"].append(entry)
                        self.report["changes"].extend(changes)
                        self._append(f"[{done}/{total}] {label} {entry['id']} · {entry.get('title') or entry.get('error', '')}")
                        if entry["status"] == "waiting":
                            self._append(f"等待 {entry.get('wait_seconds', 0)} 秒 · {entry.get('wait_kind', '')}")
                        else:
                            self._append(f"HTTP {entry.get('http_status', '无响应')} · 累计 {entry.get('attempts', 0)} 次 · "
                                         f"{entry.get('response_url') or entry.get('url', '')}")
                        for change in changes:
                            self._append(f"{change['name']} · 昵称：{change['old_nickname']} → {change['new_nickname']} · "
                                         f"文件夹：{change['old_title']} → {change['new_title']}")
                        self._save_log()
                fetch_workshop_titles(APP_IDS[game], ids, progress, cached_titles=cached,
                                      checkpoint=model.directory / "workshop_queue" / f"{game}.json",
                                      stop_event=self.stop, force=not resume, api_key=api_key)
            except Exception as exc:
                error = str(exc)
            finally:
                with model.lock:
                    pending = self.report["total"] - self.value["done"]
                    self.value.update(running=False, summary=(f"任务已停止，已完成结果已保留 · 待确认 {pending}" if self.stop.is_set() else "更新失败：" + error if error else
                                      f"本次查询结束，可再次补查 · 待确认 {pending}" if pending else "所有工坊条目均已确认"))
                    self.report.update(finished=datetime.now().isoformat(), error=error,
                                       confirmed=self.value["done"], pending=pending)
                    self._append(self.value["summary"])
                    try:
                        self._save_log()
                    except OSError as exc:
                        self._append("日志保存失败：" + str(exc))
        threading.Thread(target=worker, daemon=True).start()

    def start_backup(self, path):
        model = self.model
        self._begin("backup", 100)
        self.log_path = None
        specs, store = copy.deepcopy(model.catalog), copy.deepcopy(model.store)
        catalogs = {model.game_id: [m.record() for m in model.mods]}
        for game, spec in specs.items():
            state = store.get("games", {}).get(game, {})
            for name in ("game_dir", "workshop_roots"):
                if name in state:
                    spec[name] = state[name]
            config = state.get("config_path")
            if config and config not in spec["configs"] and Path(config).is_file():
                spec["configs"].append(config)

        def worker():
            try:
                for game, spec in specs.items():
                    if game not in catalogs:
                        mods, _ = scan(game, spec["game_dir"], spec["workshop_roots"], store.get("games", {}).get(game, {}).get("titles", {}))
                        if spec["configs"]:
                            try:
                                ConfigSession(game, spec["configs"][0]).load_states(mods)
                            except (OSError, ValueError):
                                pass
                        catalogs[game] = [m.record() for m in mods]
                def progress(done, total, name):
                    with model.lock:
                        percent = int(done * 100 / max(1, total))
                        if percent != self.value["done"]:
                            self.value.update(done=percent, summary=f"正在备份 {percent}% · {name}", version=self.value["version"] + 1)
                result = export_bundle(path, specs, store, catalogs, progress)
                with model.lock:
                    self.value["summary"] = "文件已保存，正在验证 SHA-256…"
                    self.value["version"] += 1
                verify_bundle(path)
                summary = f"备份并校验完成 · {result['files']} 个文件 · {result['bytes'] / 1024**3:.2f} GiB\n{path}"
            except Exception as exc:
                summary = "备份失败：" + str(exc)
            with model.lock:
                self.value.update(running=False, summary=summary)
                self._append(summary)
        threading.Thread(target=worker, daemon=True).start()

    def start_covers(self, refresh=False):
        from covers import fetch_covers
        model = self.model
        ids = sorted({m.workshop_id for m in model.mods if m.workshop_id})
        if not ids:
            raise ValueError('当前游戏没有工坊条目。')
        key = self.api_key or load_key(model.directory / 'credentials/steam-api-key.bin')
        if not key:
            raise ValueError('请先在工坊账号设置中填写 Key。')
        game = model.game_id
        self._begin('covers',len(ids))
        self.log_path = model.directory/'logs'/('covers-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.log')
        self.report={'kind':'covers'}
        def worker():
            try:
                def progress(done,total,item,text):
                    with model.lock:
                        self.user_log.cover(done,total,item,text)
                        self.value.update(done=done,total=total,summary=f'封面 {done}/{total} · {text}')
                        self._append(f'{item} · {text}')
                        model.changed()
                result = fetch_covers(model.directory,game,ids,key,progress,self.stop,refresh)
                summary = f"封面已缓存 {result['cached']}/{result['total']} 项 · 可再次运行补齐未完成部分"
            except Exception as exc:
                summary = '封面获取停止：' + type(exc).__name__
            with model.lock:
                self.value.update(summary=summary)
                self._append(summary)
                self._save_log(finishing=True);model.changed();self.value['running']=False
        threading.Thread(target=worker,daemon=True).start()


    def start_scan(self, discard=False, metadata_only=False):
        model = self.model
        if not metadata_only: model.require_discard(discard)
        self._begin('scan', 1)
        game = model.game_id
        self.log_path = model.directory / 'logs' / ('scan-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.log')
        self.report = {'game':game, 'refresh':'small' if metadata_only else 'large', 'results':[]}
        def worker():
            from versions import VersionStore
            from covers import fetch_covers
            try:
                def signature(mods):
                    return {m.key:[m.version,m.name,m.disk_bytes] for m in mods}
                with model.lock:
                    before=model.store.get('scan_catalog',{}).get(game,signature(model.mods))
                    if not metadata_only:
                        model.rescan(discard=True)
                        if model.issues or model.session is None: raise ValueError("本地扫描有异常，未更新缓存：" + "; ".join(model.issues))
                    mods = list(model.mods)
                    cache_enabled=bool(model.store.get('backup_cache_enabled',False))
                def progress(text):
                    with model.lock:
                        self.value['summary'] = text
                        self._append(text)
                after=signature(mods)
                summary={'added':len(after.keys()-before.keys()),'updated':sum(k in before and before[k]!=v for k,v in after.items()),'missing':len(before.keys()-after.keys())}
                if not metadata_only:
                    if cache_enabled:
                        store = VersionStore(model.directory)
                        store.capture(game,mods,progress,self.stop)
                        with model.lock:model.versions=store
                    else:progress('缓存已关闭：只更新模组列表，不复制模组文件。')
                    with model.lock:
                        model.store.setdefault('scan_catalog',{})[game]=after;model.save();model.changed()
                    progress(f"列表变化：新增 {summary['added']} · 信息更新 {summary['updated']} · 本地消失 {summary['missing']}（已有备份记录保留）")
                else:progress('更新标题与封面：只补查当前列表的工坊标题和封面，不扫描磁盘、不缓存文件。')
                self.report['changes']=summary
                def cover_progress(done,total,item,text):
                    with model.lock:self.user_log.cover(done,total,item,text)
                    progress(f'封面 {done}/{total} · {text}')
                ids = sorted({m.workshop_id for m in mods if m.workshop_id})
                key = (self.api_key or load_key(model.directory / 'credentials/steam-api-key.bin')) if model.store.get('workshop_access',{}).get('method')=='account_api' else ''
                if ids and not self.stop.is_set():
                    def title_progress(done,total,entry):
                        with model.lock:
                            self.user_log.title(entry)
                            model.workshop_health(entry)
                            if entry['status']=='success': model.apply_titles({entry['id']:entry['title']},False,{entry['id']})
                            self.report['results'].append(entry)
                            self.value.update(done=done,total=total)
                            progress(f"工坊 {done}/{total} · {entry['id']} · 第 {entry.get('round','—')} 轮 · {entry['status']} · HTTP {entry.get('http_status','—')} · 网络耗时 {entry.get('seconds','—')} 秒 · 批量 {entry.get('batch_size','—')} 项 · {entry.get('error','')}")
                    fetch_workshop_titles(APP_IDS[game],ids,title_progress,checkpoint=model.directory/'workshop_queue'/f'{game}.json',
                                          stop_event=self.stop,force=True,api_key=key)
                    if key and not self.stop.is_set():
                        fetch_covers(model.directory,game,[i for i in ids if model.state.get('workshop_health',{}).get(i,{}).get('status')!='存疑'],key,lambda d,t,i,text:cover_progress(d,t,i,text),self.stop,False)
                    elif not key: progress('尚未配置账号 Key，标题使用网页；封面请配置账号后补齐。')
                removed=len({e['id'] for e in self.report['results'] if e['status']=='removed'})
                pending=len(ids)-len({e['id'] for e in self.report['results'] if e['status'] in ('removed','success')})
                result=f"{'标题与封面更新' if metadata_only else '模组列表更新'}{'已停止' if self.stop.is_set() else '完成'} · 新增 {summary.get('added',0)} · 更新 {summary.get('updated',0)} · 本地消失 {summary.get('missing',0)} · 确认下架 {removed} · 工坊待确认 {pending}"
            except Exception as exc:
                result='扫描未完成：'+str(exc)
            with model.lock:
                if self.stop.is_set():result='任务已停止，已完成结果已保留。'
                self.value.update(summary=result)
                self._append(result); self._save_log(finishing=True); model.changed(); self.value['running'] = False
        threading.Thread(target=worker,daemon=True).start()

    def start_release(self, path, importing=False):
        from release_store import export_release, register_many, pending
        model=self.model
        self._begin('backup',len(pending(model.versions)) if not importing else 1)
        self.log_path=model.directory/'logs'/('mod-release-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.log')
        self.report={'kind':'release_import' if importing else 'release_export','path':str(path)}
        def worker():
            try:
                def progress(text):
                    with model.lock:
                        self.value['summary']=text
                        if text.startswith('['):
                            numbers=text.split(']')[0][1:].split('/')
                            self.value.update(done=int(numbers[0]),total=int(numbers[1]))
                        self._append(text)
                result=(register_many(model.versions,path if isinstance(path,list) else [path],progress) if importing else export_release(model.versions,path,progress=progress))
                with model.lock:
                    self.report['result']=result
                    if importing:self.value['next']='versions'
                    self.value.update(done=self.value['total'],summary=(f"导入完成：{result['packs']} 个包 · 新建 {result['added']} 个版本记录 · 跳过 {len(result['errors'])} 个文件" if importing else
                        f"导出完成：{result['versions']} 个新版本 · {result['parts']} 个包 · {result.get('directory','没有新增内容')}"))
            except Exception as exc:
                with model.lock:self.value['summary']='模组包操作失败：'+str(exc)
            finally:
                with model.lock:
                    self._append(self.value['summary']);self._save_log(finishing=True);model.changed();self.value['running']=False
        threading.Thread(target=worker,daemon=True).start()
