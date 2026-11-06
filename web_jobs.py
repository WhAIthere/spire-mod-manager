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
                self._save_log(finishing=True);model.changed();self.value['running']=False;self.value['local_scanning']=False
        threading.Thread(target=worker,daemon=True).start()


    def start_scan(self, discard=False, metadata_only=False, parts=None, scope='changed'):
        model = self.model
        components = {'local', 'titles', 'covers'}
        if parts is None:
            parts = ['titles', 'covers'] if metadata_only else list(components)
        if (not isinstance(parts, list) or not parts or
                any(not isinstance(part, str) or part not in components for part in parts) or
                scope not in ('changed', 'all')):
            raise ValueError('请选择至少一个有效的更新项目。')
        parts = set(parts)
        local = 'local' in parts
        if local and getattr(model,'demo_mode',False):
            raise ValueError('空白清单体验不扫描真实文件。')
        if local: model.require_discard(discard)
        all_workshop = metadata_only or scope == 'all'
        labels = [label for part,label in [('local','本地文件'),('titles','工坊名称'),('covers','工坊封面')] if part in parts]
        self._begin('scan', 1)
        self.value['local_scanning']=local
        game = model.game_id
        self.log_path = model.directory / 'logs' / ('scan-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.log')
        self.report = {'game':game, 'parts':sorted(parts), 'scope':'all' if all_workshop else 'changed', 'results':[]}
        def worker():
            from versions import VersionStore
            from covers import fetch_covers
            try:
                def signature(mods):
                    return {m.key:[m.version,m.name,m.disk_bytes,m.scan_stamp] for m in mods if not m.missing}
                with model.lock:
                    saved_before = model.store.get('scan_catalog', {}).get(game)
                    before = saved_before if saved_before is not None else signature(model.mods)
                    old_pending = model.store.get('scan_workshop_pending', {}).get(game, [])
                    saved_pending = model.store.get('scan_refresh_pending', {}).get(game, {})
                    pending = {part:set(saved_pending.get(part, old_pending)) for part in ('titles','covers')}
                    def list_item(m):
                        return {'name':m.nickname,'id':m.id,'version':m.version,'workshop_id':m.workshop_id}
                    before_items=dict(model.store.get('scan_items',{}).get(game,{}))
                    before_items.update({m.key:list_item(m) for m in model.mods})
                    if local:
                        model.rescan(discard=True)
                        self.value['local_scanning']=False
                        if model.issues or model.session is None: raise ValueError("本地扫描有异常，未更新缓存：" + "; ".join(model.issues))
                    mods = [m for m in model.mods if not m.missing]
                    cache_enabled=bool(model.store.get('backup_cache_enabled',False))
                def progress(text):
                    with model.lock:
                        self.value['summary'] = text
                        self._append(text)
                after=signature(mods)
                added_keys = after.keys() - before.keys()
                removed_keys = before.keys() - after.keys()
                updated_keys = {
                    key for key in after.keys() & before.keys()
                    if after[key][:len(before[key])] != before[key]
                }
                def save_pending():
                    model.store.setdefault('scan_refresh_pending',{})[game] = {
                        part:sorted(ids) for part,ids in pending.items()
                    }
                    model.store.get('scan_workshop_pending',{}).pop(game,None)
                summary = {'added':0,'updated':0,'missing':0} if not local else {
                    'added':len(added_keys),'updated':len(updated_keys),'missing':len(removed_keys)
                }
                if local:
                    changed_keys = added_keys | updated_keys
                    # Older installations establish a file-stamp baseline once.
                    changed_keys |= {k for k in after if saved_before is None or len(before.get(k, [])) < 4}
                    local_ids = {m.workshop_id for m in mods if m.workshop_id}
                    changed_ids = {m.workshop_id for m in mods if m.key in changed_keys and m.workshop_id}
                    for ids in pending.values():
                        ids.intersection_update(local_ids)
                        ids.update(changed_ids)
                    def item(key,removed=False):
                        values=dict(before_items.get(key,{'name':before[key][1],'version':before[key][0]})) if removed else list_item(next(m for m in mods if m.key==key))
                        if not removed and key in before:
                            values['previous_version']=before[key][0]
                            fields=[name for index,name in enumerate(('版本','名称','占用空间','文件状态')) if index < len(before[key]) and before[key][index]!=after[key][index]]
                            values['reason']='、'.join(fields)+'变化'
                        return values
                    self.user_log.changes={'added':[item(k) for k in sorted(after.keys()-before.keys())],
                        'updated':[item(k) for k in sorted(updated_keys)],
                        'missing':[item(k,True) for k in sorted(before.keys()-after.keys())]}
                    if cache_enabled:
                        store = VersionStore(model.directory)
                        store.capture(game,mods,progress,self.stop)
                        with model.lock:model.versions=store
                    else:
                        store = VersionStore(model.directory)
                        changed_packages = {m.package_key for m in mods if m.key in changed_keys}
                        store.observe(game,[m for m in mods if m.package_key in changed_packages])
                        with model.lock:model.versions=store
                        progress('缓存已关闭：保留版本记录，不复制模组文件。')
                    with model.lock:
                        model.store.setdefault('scan_catalog',{})[game]=after
                        save_pending()
                        model.store.setdefault('scan_items',{})[game]={m.key:list_item(m) for m in mods}
                        model.save();model.changed()
                    progress(f"列表变化：新增 {summary['added']} · 信息更新 {summary['updated']} · 本地消失 {summary['missing']}（已有备份记录保留）")
                else:progress('仅更新所选工坊资料，不扫描磁盘、不缓存模组文件。')
                self.report['changes']=summary
                def cover_progress(done,total,item,text):
                    with model.lock:self.user_log.cover(done,total,item,text)
                    progress(f'封面 {done}/{total} · {text}')
                with model.lock:
                    workshop_ids = {m.workshop_id for m in model.mods if m.workshop_id}
                title_ids,cover_ids = (
                    sorted(workshop_ids if all_workshop else pending[part] & workshop_ids) if part in parts else []
                    for part in ('titles','covers')
                )
                if parts & {'titles','covers'}:
                    progress(f"工坊资料（{'当前清单' if all_workshop else '新增、文件变化或上次未完成'}）："
                             + ' · '.join(f'{label} {len(ids)} 项' for part,label,ids in
                                          [('titles','名称',title_ids),('covers','封面',cover_ids)] if part in parts))
                key = (self.api_key or load_key(model.directory / 'credentials/steam-api-key.bin')) if (
                    (title_ids or cover_ids) and model.store.get('workshop_access',{}).get('method')=='account_api') else ''
                title_done = set()
                if title_ids and not self.stop.is_set():
                    def title_progress(done,total,entry):
                        with model.lock:
                            self.user_log.title(entry)
                            model.workshop_health(entry)
                            if entry['status']=='success': model.apply_titles({entry['id']:entry['title']},False,{entry['id']})
                            if entry['status'] in ('success','removed') or entry.get('suspected'):
                                title_done.add(entry['id'])
                            self.report['results'].append(entry)
                            self.value.update(done=done,total=total)
                            progress(f"工坊 {done}/{total} · {entry['id']} · 第 {entry.get('round','—')} 轮 · {entry['status']} · HTTP {entry.get('http_status','—')} · 网络耗时 {entry.get('seconds','—')} 秒 · 批量 {entry.get('batch_size','—')} 项 · {entry.get('error','')}")
                    fetch_workshop_titles(APP_IDS[game],title_ids,title_progress,checkpoint=model.directory/'workshop_queue'/f'{game}.json',
                                          stop_event=self.stop,force=True,api_key=key)
                    with model.lock:
                        pending['titles'].difference_update(title_done)
                        save_pending();model.save()
                cover_done = set()
                if cover_ids and not self.stop.is_set():
                    if key:
                        cover_result = fetch_covers(model.directory,game,cover_ids,key,cover_progress,self.stop,not metadata_only)
                        cover_done.update(cover_result['settled'])
                        with model.lock:
                            pending['covers'].difference_update(cover_done)
                            save_pending();model.save()
                    else:
                        progress('封面已跳过：请在 Steam API 管理中启用账号 API。')
                removed=len({e['id'] for e in self.report['results'] if e['status']=='removed'})
                result = ('完整更新完成' if local and all_workshop and parts == components else
                          '模组列表更新完成' if local else '工坊资料更新完成') + ' · ' + '、'.join(labels)
                if local:
                    result += f" · 新增 {summary['added']} · 更新 {summary['updated']} · 本地消失 {summary['missing']}"
                if title_ids:
                    result += f" · 确认下架 {removed} · 名称待确认 {len(title_ids)-len(title_done)}"
                if cover_ids and not key:
                    result += ' · 封面已跳过（未启用账号 API）'
                elif len(cover_ids) > len(cover_done):
                    result += f' · 封面待确认 {len(cover_ids)-len(cover_done)}'
            except Exception as exc:
                result='扫描未完成：'+str(exc)
            with model.lock:
                if self.stop.is_set():result='任务已停止，已完成结果已保留。'
                self.user_log.row('completion',result,'stopped' if self.stop.is_set() else 'warning' if result.startswith('扫描未完成') else 'success')
                self.value.update(summary=result,local_scanning=False)
                self._append(result); self._save_log(finishing=True); model.changed(); self.value['running'] = False
        threading.Thread(target=worker,daemon=True).start()

    def start_subscriptions(self,plan):
        from subscriptions import validate
        validate(self.model,plan)
        self._begin('subscribe',len(plan['items']))
        self.log_path=self.model.directory/'logs'/('subscribe-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.log')
        self.report={'game':'sts2','results':[]}
        self._append('已确认订阅名单；Steam 必须保持运行并登录，订阅后会自动安排下载。')
        def worker():
            for number,item in enumerate(plan['items'],1):
                if self.stop.is_set():break
                ident=item['id']
                with self.model.lock:
                    self.user_log.row('subscribe:'+ident,item['name']+' ['+ident+'] · 正在提交并等待 Steam 确认')
                    self._append('订阅 '+ident+'：等待 Steam 确认')
                try:
                    # The helper rechecks the account and skips already subscribed IDs.
                    result=self.model.steam(plan['game_dir'],command='subscribe',item=ident,account=plan['account'])
                    if result.get('status') not in ('already','subscribed'):raise ValueError('Steam 返回了未识别的订阅结果，请核对；未重试。')
                    text='已订阅，无需重复提交' if result['status']=='already' else '订阅成功 · 等待 Steam 下载／安装'
                    with self.model.lock:
                        self.report['results'].append({'id':ident,**result})
                        if self.model.subscription_view is not None:self.model.subscription_view['ids'].add(ident)
                        if result['status']=='subscribed' and self.model.subscription_snapshot is not None:
                            self.model.subscription_snapshot = {
                                **self.model.subscription_snapshot,
                                'items': {**self.model.subscription_snapshot['items'], ident:result},
                            }
                        self.model.changed()
                        self.user_log.row('subscribe:'+ident,item['name']+' ['+ident+'] · '+text,'success')
                        self.value['done']=number;self._append(ident+' · '+text);self._save_log()
                except Exception as exc:
                    with self.model.lock:
                        self.report['results'].append({'id':ident,'status':'unconfirmed','error':str(exc)})
                        self.user_log.row('subscribe:'+ident,item['name']+' ['+ident+'] · '+str(exc),'warning')
                        self.user_log.problems[('subscribe',ident)]={'id':ident,'phase':'订阅','reason':str(exc)}
                        self._append('订阅未确认 '+ident+' · '+str(exc))
                    break
            with self.model.lock:
                bad=any(r.get('status')=='unconfirmed' for r in self.report['results'])
                text=('订阅任务已停止' if self.stop.is_set() else '订阅任务有未确认项目' if bad else '订阅任务已完成')+' · 已处理 '+str(len(self.report['results']))+'/'+str(len(plan['items']))+'；已提交的订阅可能已开始下载。下载完成后请更新模组列表并核对开关。'
                self.user_log.row('completion',text,'warning' if bad else 'success')
                self.value.update(summary=text,running=False);self._append(text);self._save_log(finishing=True)
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
                    self._append(self.value['summary']);self._save_log(finishing=True);model.changed();self.value['running']=False;self.value['local_scanning']=False
        threading.Thread(target=worker,daemon=True).start()
