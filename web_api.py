"""Small allowlisted JS/Python API. No GUI toolkit is used by editor operations."""
from datetime import datetime
import json
import os
from pathlib import Path
import webbrowser

from core import atomic_json, dependency_issues
from manager_model import Manager
from steam_account import load_key, save_key, validate_key
from web_jobs import Jobs
from task_log import clean_saved_view


class Api:
    def __init__(self, model):
        self._model = model
        self._jobs = Jobs(model)
        self._window = None
        self._ready = False
        self._allow_close = False

    def request(self, action, payload=None):
        try:
            payload = payload or {}
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

    def _run(self, action, p):
        model, jobs = self._model, self._jobs
        if jobs.value["running"] and action in ("game", "scan", "refresh_small", "cache_options", "settings", "account_save", "account_forget", "apply_preview", "apply_commit"):
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
        elif action == "release_info":
            from release_store import info
            return info(model.versions)
        elif action == "versions":
            return model.versions.versions(model.game_id)
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
            return model.details(p["key"])
        elif action == "issues":
            return model.issues + ([model.warning] if model.warning else []) + dependency_issues(model.mods) + [
                f"{m.name} [{m.workshop_id or '本地'}]：{m.loader_status}" for m in model.mods if not m.loadable]
        elif action == "apply_preview":
            return model.prepare_apply()
        elif action == "apply_commit":
            return model.commit(p["token"])
        elif action == "open_workshop":
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
            if jobs.value["kind"] in ("titles",'covers',"scan"):
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
            import webview
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
                    atomic_json(path, {"game": self._model.game_id, "config_path": self._model.config_path,
                                       "mods": [m.record() for m in self._model.mods], "profiles": self._model.state.get("profiles", {})})
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
