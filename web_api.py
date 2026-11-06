"""Small allowlisted JS/Python API. No GUI toolkit is used by editor operations."""
from datetime import datetime
import json
import os
import subprocess
from pathlib import Path
import webbrowser
import uuid
import mod_lists

from core import atomic_json, dependency_issues
from manager_model import Manager
from steam_account import load_key, save_key, validate_key
from web_jobs import Jobs
from task_log import clean_saved_view


class Api:
    def __init__(self, model):
        self._model = model
        self._jobs = Jobs(model)
        self._jobs.api_key=getattr(model,'borrowed_api_key','') or ''
        self._window = None
        self._ready = False
        self._allow_close = False
        self._list_preview = None

    def collection_preview(self,value):
        try:
            import workshop_collections
            with self._model.lock:game=self._model.game_id
            key=self._jobs.api_key or (load_key(self._model.directory/'credentials/steam-api-key.bin') if self._model.store.get('workshop_access',{}).get('method')=='account_api' else '')
            if hasattr(self._model, 'collection_fixture'):
                target_game, collection = game, self._model.collection_fixture
            else:
                target_game, collection = workshop_collections.resolve(
                    value, request=lambda method, ids: workshop_collections.post(method, ids, key))

            with self._model.lock:
                if game!=self._model.game_id:raise ValueError('游戏已切换，请重新获取合集。')
                if self._jobs.value['running']:
                    raise ValueError('请先等待后台任务完成。')
                self._model.change_game(target_game)
                token=uuid.uuid4().hex
                self._collection_preview={'token':token,'game':target_game,'collection':collection}
                ids={m.workshop_id for m in self._model.mods if not m.missing and m.source=='steam_workshop'}
                return {'ok':True,'token':token,'game':target_game,'single':collection.get('single',False),'title':collection['title'],'total':len(collection['items']),
                        'local':sum(i in ids for i in collection['items']),'names':[v['title'] for v in collection['items'].values()], 'warnings':collection.get('warnings',[])}
        except Exception as exc:return {'ok':False,'error':str(exc)}

    def dependency_lookup(self, ids):
        """Resolve only known Workshop IDs, with network work outside the model lock."""
        try:
            import workshop_collections
            from core import APP_IDS
            model = self._model
            with model.lock:
                game = model.game_id
                key = self._jobs.api_key or load_key(model.directory/'credentials/steam-api-key.bin')
            ids = list(dict.fromkeys(str(i) for i in ids if str(i).isascii() and str(i).isdecimal()))
            items = {}
            errors = {}
            for offset in range(0, len(ids), 50):
                batch = ids[offset:offset+50]
                try:
                    response = workshop_collections.post('GetPublishedFileDetails', batch, key)
                except (OSError, ValueError) as exc:
                    errors.update({ident:str(exc) for ident in batch})
                    continue
                for item in response.get('publishedfiledetails', []):
                    ident = str(item.get('publishedfileid'))
                    if ident in batch and item.get('result') == 1 and str(item.get('consumer_app_id')) == APP_IDS[game] and item.get('file_type') != 2:
                        items[ident] = {'title':str(item.get('title') or '工坊 '+ident), 'confirmed':True}
            with model.lock:
                if game != model.game_id:
                    raise ValueError('游戏已切换，请重新检查前置。')
                token = uuid.uuid4().hex
                self._dependency_lookup = {'token':token, 'game':game, 'items':items}
            return {'ok':True, 'token':token, 'items':items, 'errors':errors}
        except Exception as exc:
            return {'ok':False, 'error':str(exc)}

    def request(self, action, payload=None):
        try:
            payload = payload or {}
            if action == 'subscription_status':
                return self._enter_subscription_mode(enter=False)
            if action == 'subscription_mode' and payload.get('enabled'):
                return self._enter_subscription_mode()
            if action == 'subscription_preview':
                return self._preview_subscription(payload)
            if action == 'subscription_start':
                from subscriptions import validate, validate_account
                with self._model.lock:
                    plan = getattr(self,'_subscription_plan',None)
                    if not plan or payload.get('token') != plan['token']:
                        raise ValueError('订阅确认已失效，请重新预览。')
                    validate(self._model, plan)
                    steam = self._model.steam
                validate_account(steam, plan)
            with self._model.lock:
                result = self._run(action, payload)
                response = {"ok": True, "result": result, "job": self._jobs.snapshot()}
                if payload.get("job_version") == response["job"]["version"]:
                    response["job"].pop("lines", None)
                if action not in ("poll", "details", "issues", "log", "account_info"):
                    response["state"] = self._model.snapshot()
                elif action == "poll" and payload.get("revision") != self._model.revision:
                    response["state"] = self._model.snapshot()
                return response
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _enter_subscription_mode(self, enter=True):
        from steam_workshop import check_account
        model, jobs = self._model, self._jobs
        with model.lock:
            if jobs.value['running']:
                raise ValueError('请先停止或等待后台任务完成。')
            if model.game_id != 'sts2':
                raise ValueError('辅助订阅目前仅支持尖塔 II。')
            demo = getattr(model, 'demo_mode', False)
            game_dir, config_path, revision = model.game_dir, model.config_path, model.revision
            steam = model.steam
        if demo:
            snapshot = {'items': {}, 'account': ''}
        else:
            if not game_dir:
                raise ValueError('请先设置塔二游戏目录；Steam 必须已运行并登录。')
            snapshot = steam(game_dir)
            check_account(snapshot, config_path)
        with model.lock:
            if jobs.value['running'] or revision != model.revision:
                raise ValueError('清单状态已变化，请重新打开辅助订阅。')
            if not demo:
                model.subscription_snapshot = snapshot
            if enter or model.subscription_view is not None:
                model.subscription_view = {'ids': set(snapshot['items']), 'account': snapshot['account']}
            model.changed()
            return {'ok': True, 'state': model.snapshot(), 'job': jobs.snapshot()}

    def _preview_subscription(self, payload):
        from subscriptions import preview, validate
        model, jobs = self._model, self._jobs
        with model.lock:
            if jobs.value['running']:
                raise ValueError('请先等待后台任务完成。')
            key = jobs.api_key or load_key(model.directory/'credentials/steam-api-key.bin')
        plan = preview(model, payload.get('keys', []), key)
        with model.lock:
            if jobs.value['running']:
                raise ValueError('后台任务已开始，请稍后重新预览。')
            validate(model, plan)
            plan['token'] = uuid.uuid4().hex
            self._subscription_plan = plan
            return {'ok':True, 'result':{'token':plan['token'],'account':plan['account'],'items':plan['items']}}

    def _run(self, action, p):
        model, jobs = self._model, self._jobs
        if model.subscription_view is not None and action=="toggle":raise ValueError("辅助订阅模式不修改游戏开关，请使用订阅勾选。")
        if getattr(model,'demo_mode',False) and action not in {
            'init','poll','game','subscription_mode','api_intro_seen','view','theme','titlebar','toggle','undo','redo','master',
            'dependencies','profile_save','profile_restore','profile_delete','alias','group','group_rename',
            'tags_add','tags_remove','tags_rename','list_forget_missing','list_import_apply','collection_import',
            'details','issues','logs','log_read','log','dependency_page','open_workshop','account_info','account_save',
            'account_forget','api_page','refresh_small','refresh_selected','titles_start','covers_start','job_stop'}:
            raise ValueError('空白清单体验仅支持清单整理与工坊信息获取，不扫描真实文件、不备份、不修改游戏。')
        if jobs.value["running"] and action in ("list_import_apply", "game", "scan", "refresh_small", "refresh_selected", "cache_options", "settings", "account_save", "account_forget", "apply_preview", "apply_commit"):
            raise ValueError("请先停止后台获取任务或等待备份完成，再切换游戏、配置或应用。")
        if action == "init":
            if not self._ready:
                model.rescan(discard=True)
                model.preload()
                self._ready = True
            return
        if action == "poll":
            return
        if action == "game":
            model.change_game(p["game"], p.get("discard", False))
        elif action == "api_intro_seen":
            model.store.setdefault("web_ui",{})["api_intro_seen"]=True;model.save();model.changed()
        elif action == "cache_options":
            model.store['backup_cache_enabled']=bool(p.get('enabled',False));model.save();model.changed()
        elif action == "refresh_small":
            jobs.start_scan(metadata_only=True)
        elif action == "refresh_selected":
            jobs.start_scan(p.get('discard',False),parts=p.get('parts',[]),scope=p.get('scope','changed'))
        elif action == "logs":
            files=sorted((model.directory/'logs').glob('*.log'),key=lambda f:f.stat().st_mtime,reverse=True)
            view=jobs.user_log.snapshot(jobs.value['running'])
            if not jobs.value['kind'] and files and files[0].with_suffix('.json').is_file():
                view=json.loads(files[0].with_suffix('.json').read_text(encoding='utf-8')).get('user_log',view)
            return {'files':[{'name':f.name,'date':datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec='seconds')} for f in files[:80]],'issues':self._run('issues',{}),'text':self._run('log',{})['text'],'user_log':clean_saved_view(view)}
        elif action == "log_read":
            name=str(p.get('name',''))
            if Path(name).name!=name or not name.endswith('.log'):raise ValueError('无效日志文件')
            path=(model.directory/'logs'/name).resolve()
            if not path.is_relative_to((model.directory/'logs').resolve()):raise ValueError('日志路径无效')
            report=json.loads(path.with_suffix('.json').read_text(encoding='utf-8')) if path.with_suffix('.json').is_file() else {}
            return {'text':path.read_text(encoding='utf-8'),'user_log':clean_saved_view(report.get('user_log'))}
        elif action == "scan":
            jobs.start_scan(p.get("discard", False))
        elif action == "settings":
            model.require_discard(p.get("discard", False))
            if p.get("config_path"):
                model.config_path = str(p["config_path"])
            if p.get("game_dir"):
                model.game_dir = str(p["game_dir"])
            if "workshop_roots" in p:
                model.state["workshop_roots"] = list(dict.fromkeys(str(x) for x in p["workshop_roots"]))
            model.rescan(discard=True)
        elif action == "view":
            model.set_view(p)
        elif action == "theme":
            theme = p["value"]
            if theme not in ("system", "light", "dark"):
                raise ValueError("未知主题。")
            model.store.setdefault("web_ui", {})["theme"] = theme
            model.save()
            model.changed()
            self._titlebar(bool(p.get("dark")))
        elif action == "titlebar":
            self._titlebar(bool(p.get("dark")))
        elif action in ("toggle", "undo", "redo", "master", "dependencies", "profile_save", "profile_restore",
                        "profile_delete", "alias", "group", "group_rename", "tags_add", "tags_remove", "tags_rename"):
            return model.edit(action, p)
        elif action == 'list_forget_missing':
            return mod_lists.forget_missing(model,p.get('keys',[]))
        elif action == 'list_import_apply':
            pending=self._list_preview
            if not pending or p.get('token')!=pending['token'] or pending['game']!=model.game_id:
                raise ValueError('清单预览已失效，请重新选择文件。')
            result=mod_lists.import_list(model,pending['data'],bool(p.get('overwrite')),bool(p.get('switches',True)))
            self._list_preview=None
            return result
        elif action == 'collection_import':
            if jobs.value['running']:raise ValueError('请先等待后台任务完成。')
            import workshop_collections
            pending=getattr(self,'_collection_preview',None)
            if not pending or pending['token']!=p.get('token') or pending['game']!=model.game_id:raise ValueError('合集预览已失效，请重新获取。')
            result=workshop_collections.merge(model,pending['collection'])
            self._collection_preview=None
            return {'count':result,'keys':[m.key for m in model.mods if m.source=='steam_workshop' and m.workshop_id in pending['collection']['items'] and m.missing]}
        elif action == 'subscription_mode':
            if jobs.value['running']:
                raise ValueError('请先停止或等待后台任务完成。')
            model.subscription_view = None
            model.changed()
        elif action == 'subscription_start':
            plan=getattr(self,'_subscription_plan',None)
            if not plan or p.get('token')!=plan['token']:raise ValueError('订阅确认已失效，请重新预览。')
            self._subscription_plan=None
            jobs.start_subscriptions(plan)
            return {"started":True}
        elif action == "release_info":
            from release_store import info
            return info(model.versions)
        elif action == "versions":
            if not jobs.value['running']: model.versions.observe(model.game_id, model.mods)
            return model.versions.versions(model.game_id)
        elif action == 'version_rollback':
            if jobs.value['running']: raise ValueError('请先停止后台任务。')
            return model.rollback_version(p['package'],p['digest'],p['mod_id'])
        elif action == "version_restore":
            model.require_discard(False)
            if jobs.value["running"]: raise ValueError("请先停止后台任务。")
            from core import running_game_processes
            if running_game_processes(model.game_id): raise ValueError("请先关闭游戏和加载器。")
            package = model.versions.index["packages"][p["package"]]
            if package["game"] != model.game_id: raise ValueError("游戏不匹配")
            if model.store.get("backup_cache_enabled",False): model.versions.capture(model.game_id, model.mods)
            rollback = model.versions.restore(p["package"], p["digest"])
            model.rescan(discard=True)
            return {"rollback": rollback}
        elif action == "details":
            return model.details(p["key"],observe=not jobs.value['running'])
        elif action == "issues":
            return model.issues + ([model.warning] if model.warning else []) + dependency_issues(model.mods) + [
                f"{m.name} [{m.workshop_id or '本地'}]：{m.loader_status}" for m in model.mods if not m.loadable]
        elif action == "apply_preview":
            return model.prepare_apply()
        elif action == "apply_commit":
            return model.commit(p["token"])
        elif action == 'dependency_add':
            lookup = getattr(self, '_dependency_lookup', None)
            if jobs.value['running'] or not lookup or lookup['token'] != p.get('token') or lookup['game'] != model.game_id:
                raise ValueError('前置检查结果已失效，请重新检查。')
            self._dependency_lookup = None
            import workshop_collections
            collections = model.state.setdefault('collections', {})
            for ident, item in lookup['items'].items():
                if not any(m.workshop_id == ident for m in model.mods):
                    collections.setdefault(ident, {'id':ident,'title':item['title'],'single':True,'items':{ident:item}})
            model.mods = workshop_collections.attach(model.game_id, model.mods, collections, model.state.get('metadata', {}))
            model.persist_metadata()
            return [m.key for m in model.mods if m.missing and m.workshop_id in lookup['items']]
        elif action == 'dependency_page':
            from urllib.parse import urlencode
            from core import APP_IDS
            ident = str(p.get('id', ''))
            if ident.isascii() and ident.isdecimal():
                webbrowser.open('https://steamcommunity.com/sharedfiles/filedetails/?id=' + ident)
            else:
                query = urlencode({'appid':APP_IDS[model.game_id], 'searchtext':str(p.get('query',''))[:200]})
                webbrowser.open('https://steamcommunity.com/workshop/browse/?' + query)
        elif action == "open_local_source":
            mod = model.targets([p["key"]])[0]
            if mod.missing or mod.source != "mods_directory":
                raise ValueError("此项目没有可打开的本地模组文件。")
            path = mod.path.resolve()
            if path.is_dir():
                os.startfile(str(path))
            elif path.is_file():
                explorer = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "explorer.exe"
                subprocess.Popen([str(explorer), "/select,", str(path)])
            else:
                raise ValueError("本地文件已移动或删除，请更新模组列表。")
        elif action == "open_workshop":
            if hasattr(model,"collection_fixture"):raise ValueError("隔离演示使用模拟编号，不打开真实工坊页面。")
            mod = model.targets([p["key"]])[0]
            if not mod.workshop_id or not mod.workshop_id.isdecimal():
                raise ValueError("这个模组没有关联的创意工坊页面。")
            webbrowser.open("https://steamcommunity.com/sharedfiles/filedetails/?id=" + mod.workshop_id)
        elif action == "api_page":
            webbrowser.open("https://steamcommunity.com/dev/apikey")
        elif action == "account_info":
            return {"method": model.store.get("workshop_access", {}).get("method", "web_schinese"),
                    "saved": (model.directory / "credentials/steam-api-key.bin").exists(),
                    "session": bool(jobs.api_key)}
        elif action == "account_save":
            if getattr(model,"demo_mode",False):p=dict(p,remember=False)
            path = model.directory / "credentials/steam-api-key.bin"
            method = p["method"]
            if method not in ("web_schinese", "account_api"):
                raise ValueError("未知获取方式。")
            if method == "account_api":
                key = validate_key(p["key"]) if p.get("key", "").strip() else jobs.api_key or load_key(path)
                if not key:
                    raise ValueError("请输入 Steam Web API Key。")
                if p.get("remember"):
                    save_key(path, key)
                else:
                    path.unlink(missing_ok=True)
                jobs.api_key = key
            model.store["workshop_access"] = {"method": method}
            model.save()
            model.changed()
        elif action == "account_forget":
            (model.directory / "credentials/steam-api-key.bin").unlink(missing_ok=True)
            jobs.api_key = ""
            model.store["workshop_access"] = {"method": "web_schinese"}
            model.save()
            model.changed()
        elif action == "titles_start":
            jobs.start_titles(bool(p.get("overwrite")), bool(p.get("resume", True)))
        elif action == 'covers_start':
            jobs.start_covers(bool(p.get('refresh',False)))
        elif action == "job_stop":
            if jobs.value["kind"] in ("titles",'covers',"scan",'subscribe'):
                jobs.stop.set()
                jobs.value["summary"] = "正在停止后续任务，保留此前完成结果；取消等待中的网络请求…"
                jobs.value["version"] += 1
        elif action == "log":
            if jobs.value["kind"]:
                return {"text": "\n".join(jobs.value["lines"]), "path": str(jobs.log_path or "")}
            paths = sorted((model.directory / "logs").glob("*.log"), key=lambda p: p.stat().st_mtime)
            return {"text": paths[-1].read_text(encoding="utf-8") if paths else "暂无更新日志。",
                    "path": str(paths[-1]) if paths else ""}
        else:
            raise ValueError("未知操作。")

    def cover(self, game, item):
        try:
            path = self._model.covers.path(game,item)
            if not path.is_file():
                return {'data':'','code':'missing_file','reason':'本地封面文件已不存在'}
            if path.stat().st_size == 0:
                return {'data':'','code':'empty_file','reason':'本地封面文件为空'}
            if path.stat().st_size > 512*1024:
                return {'data':'','code':'oversized','reason':'本地封面文件过大'}
            data = self._model.covers.data(game,item)
            return {'data':data,'code':'ok' if data else 'empty_file','reason':'' if data else '本地封面文件为空'}
        except PermissionError:
            return {'data':'','code':'permission_denied','reason':'没有封面文件的读取权限'}
        except OSError:
            return {'data':'','code':'read_error','reason':'本地封面文件读取失败'}
        except ValueError:
            return {'data':'','code':'invalid_item','reason':'工坊编号或缓存路径无效'}

    def file(self, kind, payload=None):
        """Only native pickers can authorize new export destinations through this API."""
        try:
            if getattr(self._model,'demo_mode',False) and kind not in ('list_import','export','log'):
                raise ValueError('空白清单体验不安装、恢复或备份模组，也不修改游戏路径。')
            import webview
            if kind=='list_import':
                if self._jobs.value['running']:raise ValueError('请先结束后台任务再导入清单。')
                values=self._window.create_file_dialog(webview.FileDialog.OPEN,file_types=('模组清单 JSON (*.json)',))
                if not values:return {'ok':True,'path':''}
                path=Path(values[0])
                if path.stat().st_size>10_000_000:raise ValueError('清单文件超过 10 MB。')
                with self._model.lock:
                    data=mod_lists.normalize(json.loads(path.read_text(encoding='utf-8-sig')),self._model.game_id)
                    token=uuid.uuid4().hex
                    self._list_preview={'token':token,'game':self._model.game_id,'data':data}
                    result=mod_lists.preview(self._model,data)
                return {'ok':True,'path':str(path),'preview':result,'token':token}
            if kind in ('release_import','release_import_folder'):
                folder=kind=='release_import_folder'
                values=self._window.create_file_dialog(webview.FileDialog.FOLDER if folder else webview.FileDialog.OPEN,allow_multiple=not folder,file_types=() if folder else ('备份包 ZIP (*.zip)',))
                if not values:return {'ok':True,'path':''}
                paths=sorted(Path(values[0]).glob('*.zip')) if folder else [Path(v) for v in values]
                if not paths:raise ValueError('这个文件夹中没有 ZIP 文件（只检查所选文件夹，不搜索其他位置）。')
                with self._model.lock:self._jobs.start_release(paths,importing=True)
                return {'ok':True,'path':str(values[0]),'job':self._jobs.snapshot()}
            if kind in ('version_extract','version_install'):
                p=payload or {};model=self._model
                with model.lock:
                    if self._jobs.value['running']:raise ValueError('请先等待或停止后台任务。')
                    package=model.versions.index['packages'][p['package']]
                    if package['game']!=model.game_id:raise ValueError('游戏不匹配')
                    if kind=='version_install':
                        from core import running_game_processes
                        if running_game_processes(model.game_id):raise ValueError('请先关闭游戏和加载器。')
                        if not model.game_dir or not Path(model.game_dir).is_dir():raise ValueError('请先设置有效的游戏目录。')
                        path=Path(model.game_dir)/'mods'
                    else:path=None
                if path is None:
                    values=self._window.create_file_dialog(webview.FileDialog.FOLDER)
                    if not values:return {'ok':True,'path':''}
                    path=Path(values[0])
                with model.lock:
                    result=model.versions.extract(p['package'],p['digest'],path,install_sts1=kind=='version_install' and model.game_id=='sts1')
                return {'ok':True,'path':result}
            picker = {
                "config": (webview.FileDialog.OPEN, "", ("游戏配置 (*.json;*.save)",)),
                "install": (webview.FileDialog.FOLDER, "", ()),
                "workshop": (webview.FileDialog.FOLDER, "", ()),
                "export": (webview.FileDialog.SAVE, f"{self._model.game_id}-mods.json", ("JSON (*.json)",)),
                "backup": (webview.FileDialog.SAVE, "spire-mods-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".zip", ("ZIP (*.zip)",)),
                "release_export": (webview.FileDialog.FOLDER, "", ()),
                "release_import": (webview.FileDialog.OPEN, "", ("模组包 ZIP (*.zip)",)),
                "log": (webview.FileDialog.SAVE, "工坊更新日志.log", ("日志 (*.log)",))}
            if kind not in picker:
                raise ValueError("未知文件选择操作。")
            mode, name, types = picker[kind]
            values = self._window.create_file_dialog(mode, save_filename=name, file_types=types)
            if not values:
                return {"ok": True, "path": ""}
            path = Path(values[0])
            with self._model.lock:
                if kind == "export":
                    atomic_json(path, mod_lists.export_list(self._model))
                elif kind == "backup":
                    self._jobs.start_backup(path)
                elif kind in ("release_export", "release_import"):
                    self._jobs.start_release(path, importing=kind=="release_import")
                elif kind == "log":
                    data = self._run("log", {})
                    path.write_text(data["text"], encoding="utf-8")
            return {"ok": True, "path": str(path), "job": self._jobs.snapshot()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _titlebar(self, dark):
        if os.name != "nt" or self._window is None or self._window.native is None:
            return
        try:
            import ctypes
            handle = self._window.native.Handle.ToInt64()
            value = ctypes.c_int(int(dark))
            function = ctypes.windll.dwmapi.DwmSetWindowAttribute
            function.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
            function(ctypes.c_void_p(handle), 20, ctypes.byref(value), ctypes.sizeof(value))
        except (AttributeError, OSError):
            pass

    def close(self, discard=False):
        with self._model.lock:
            if self._jobs.value["running"]:
                if self._jobs.value["kind"] == "backup":
                    return {"ok": False, "error": "备份进行中，请等待完成后退出。"}
                self._jobs.stop.set()
                return {"ok": True, "waiting": True}
            if self._model.dirty_games() and not discard:
                return {"ok": False, "error": "仍有未应用的开关修改。"}
            self._allow_close = True
        self._window.destroy()
        return {"ok": True}
