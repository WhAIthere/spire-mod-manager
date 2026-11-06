"""UI-independent editor state for the WebView2 interface. Game adapters stay in core.py."""
import copy
from pathlib import Path
from datetime import datetime
import mod_lists
import workshop_collections
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from core import (ConfigSession, atomic_json, dependency_issues, discover, check_local_dependencies,
                  read_json, restore_profile, running_game_processes, scan, workshop_nickname)
from history import History
from steam_bridge import same_snapshot
from steam_workshop import (bridge, bind_snapshot, build_plan, check_account, commit_plan,
                            preview_lines, validate_plan)
from view import MODES, view_sections
from covers import CoverCache
from versions import VersionStore


class Manager:
    _CONTEXT_FIELDS = ("mods", "issues", "session", "master", "baseline", "history", "status", "warning",
                       "workshop_snapshot", "subscription_snapshot", "query", "group", "view", "config_path", "game_dir", "list_records", "scan_state")

    def __init__(self, directory, catalog=None, steam=bridge):
        self.directory = Path(directory)
        self.covers = CoverCache(directory)
        self.versions = VersionStore(directory)
        self.store_path = self.directory / "library.json"
        self.store = read_json(self.store_path) if self.store_path.exists() else {"schema": 1, "games": {}}
        self.catalog = catalog if catalog is not None else discover()
        self.steam = steam
        self._contexts, self._loading = {}, {}
        self._scan_cache = {}
        self._executor = None
        self.lock = threading.RLock()
        self.game_id = "sts1"
        self.mods, self.issues = [], []
        self.session = None
        self.master = True
        self.baseline = {}
        self.history = History()
        self.revision = 0
        self.status = "正在读取本地模组…"
        self.warning = ""
        self.workshop_snapshot = None
        self.subscription_snapshot = None
        self.pending_plan = None
        self.query, self.group = "", "全部分组"
        self.view = {}
        self.config_path, self.game_dir = "", ""
        self.list_records = {}
        self.scan_state = {"phase":"not_scanned", "checked_at":""}
        self.subscription_view=None
        self._configure_game()
        self.history.reset(self.editor_state())

    @property
    def state(self):
        return self.store.setdefault("games", {}).setdefault(self.game_id, {"metadata": {}, "profiles": {}})

    def save(self):
        atomic_json(self.store_path, self.store)

    def changed(self):
        self.pending_plan = None
        self.revision += 1

    def _configure_game(self):
        saved, catalog = self.state, self.catalog[self.game_id]
        self.list_records = copy.deepcopy(saved.get("shared_list", {}))
        self.game_dir = saved.get("game_dir") or catalog["game_dir"]
        choices = list(catalog["configs"])
        old = saved.get("config_path", "")
        self.config_path = old if old and Path(old).exists() else choices[0] if choices else ""
        self.query, self.group = "", "全部分组"
        self.view = {"mode": "不分组", "show_folder": False, "hide_unavailable": True,
                     "sort_column": "nickname", "descending": False, "group_descending": False, **saved.get("view", {})}
        if self.view["mode"] not in MODES:
            self.view["mode"] = "不分组"
        if self.view["sort_column"] in ("file", "id", "copies"):
            self.view["sort_column"] = "nickname"

    def dirty(self):
        return bool(any(m.enabled != self.baseline.get(m.key, False) for m in self.mods) or
                    (self.session is not None and self.master != self.session.master_enabled))

    def require_discard(self, discard):
        if self.dirty() and not discard:
            raise ValueError("有未应用的开关修改，请先确认是否放弃。")

    def change_game(self, game, discard=False):
        if game not in self.catalog:
            raise ValueError("未知游戏。")
        if game == self.game_id:
            return
        self.subscription_view=None
        self._contexts[self.game_id] = {key: getattr(self, key) for key in self._CONTEXT_FIELDS}
        if game in self._loading:
            self._contexts[game] = self._loading.pop(game).result()
        context = self._contexts.get(game)
        if context is None:
            context = self._read_context(game, copy.deepcopy(self.store.get("games", {}).get(game, {})))
        self.game_id = game
        for key, value in context.items():
            setattr(self, key, value)
        self.state["shared_list"] = self.list_records
        self.state["profiles"] = context.get("resolved_profiles", self.state.get("profiles",{}))
        self.save()
        self.changed()

    def preload(self):
        """Scan the other game off the UI/request thread; switching later reuses its result."""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="spire-preload")
        for game in self.catalog:
            if game != self.game_id and game not in self._contexts and game not in self._loading:
                saved = copy.deepcopy(self.store.get("games", {}).get(game, {}))
                self._loading[game] = self._executor.submit(self._read_context, game, saved)

    def dirty_games(self):
        result = [self.game_id] if self.dirty() else []
        for game, context in self._contexts.items():
            if game == self.game_id:
                continue
            if any(m.enabled != context["baseline"].get(m.key, False) for m in context["mods"]) or (
                    context["session"] is not None and context["master"] != context["session"].master_enabled):
                result.append(game)
        return result

    def _read_context(self, game, saved):
        catalog = self.catalog[game]
        choices = catalog["configs"]
        old = saved.get("config_path", "")
        config_path = old if old and Path(old).exists() else choices[0] if choices else ""
        game_dir = saved.get("game_dir") or catalog["game_dir"]
        view = {"mode": "不分组", "show_folder": False, "hide_unavailable": True, "sort_column": "nickname",
                "descending": False, "group_descending": False, **saved.get("view", {})}
        if view["mode"] not in MODES:
            view["mode"] = "不分组"
        if view["sort_column"] in ("file", "id", "copies"):
            view["sort_column"] = "nickname"
        scan_state={"phase":"failed","checked_at":datetime.now().isoformat(timespec="seconds")}
        context = dict(scan_state=scan_state,list_records=copy.deepcopy(saved.get("shared_list",{})),mods=[], session=None, issues=[], master=True, baseline={}, history=History(),
                       status="正在读取本地模组…", warning="", workshop_snapshot=None, subscription_snapshot=None, query="", group="全部分组",
                       view=view, config_path=config_path, game_dir=game_dir)
        try:
            roots = saved.get("workshop_roots", catalog["workshop_roots"])
            mods, issues = scan(game, game_dir, roots, saved.get("titles", {}), self._scan_cache.setdefault(game, {}))
            scan_state['phase']='attention' if issues else 'complete'
            context.update(mods=mods,issues=issues)
            if not config_path:
                if not game_dir and not roots: scan_state['phase']='not_configured'
                raise ValueError("未发现游戏配置，请在右上角省略号 →“路径与高级设置”选择配置文件；首次使用请先运行游戏及模组加载器以生成配置。")
            session = ConfigSession(game, config_path)
            session.load_states(mods)
            if game == "sts2" and (any(m.source == "steam_workshop" for m in mods)
                                   or saved.get("collections") or saved.get("shared_list")):
                try:
                    snapshot = self.steam(game_dir)
                    check_account(snapshot, session.path)
                    bind_snapshot(mods, snapshot)
                    context["workshop_snapshot"] = snapshot
                    context["subscription_snapshot"] = snapshot
                except Exception as exc:
                    context["warning"] = str(exc)
            for mod in mods:
                data = saved.get("metadata", {}).get(mod.key, saved.get("metadata", {}).get(mod.legacy_key, {}))
                mod.group, mod.tags, mod.alias = data.get("group", ""), list(data.get("tags", [])), data.get("alias", "")
            context.update(mods=mods, issues=issues, session=session, master=session.master_enabled,
                           status="扫描完成" if not issues else "扫描完成，有提示待核对")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            context.update(issues=[str(exc)], status="无法扫描：" + str(exc))
        for mod in context['mods']:
            data=saved.get('metadata',{}).get(mod.key,{})
            mod.group=data.get('group',mod.group);mod.tags=list(data.get('tags',mod.tags));mod.alias=data.get('alias',mod.alias)
        context["baseline"] = {m.key: m.enabled for m in context["mods"]}
        context['mods'],context['list_records'],context['resolved_profiles']=mod_lists.attach(game,context['mods'],context['list_records'],saved.get('profiles',{}))
        context['mods']=workshop_collections.attach(game,context['mods'],saved.get('collections',{}),saved.get('metadata',{}))
        saved['profiles']=context['resolved_profiles'];saved['shared_list']=context['list_records']
        context["history"].reset(self._editor_snapshot(context["mods"], context["master"], saved))
        return context

    def rescan(self, discard=False):
        self.require_discard(discard)
        saved = copy.deepcopy(self.state)
        saved.update(game_dir=self.game_dir, config_path=self.config_path,shared_list=copy.deepcopy(self.list_records))
        for key, value in self._read_context(self.game_id, saved).items():
            setattr(self, key, value)
        if self.session is not None:
            self.state.update(game_dir=self.game_dir, config_path=self.config_path)
            self.save()
        if self.subscription_view is not None:
            snapshot = self.subscription_snapshot
            if snapshot is None or snapshot['account'] != self.subscription_view['account']:
                self.subscription_view = None
            else:
                self.subscription_view['ids'] = set(snapshot['items'])
        self.state["shared_list"] = self.list_records
        self.state["profiles"] = self.resolved_profiles
        self.save()
        self.changed()

    def editor_state(self):
        return self._editor_snapshot(self.mods, self.master, self.state)

    @staticmethod
    def _editor_snapshot(mods, master, saved):
        return {"mods": {m.key: {"enabled": m.enabled, "group": m.group, "tags": list(m.tags),
                                "alias": m.alias, "package_title": m.package_title} for m in mods},
                "collections":copy.deepcopy(saved.get("collections",{})),"master": master, "profiles": copy.deepcopy(saved.get("profiles", {})),
                "titles": copy.deepcopy(saved.get("titles", {})),"shared_list":copy.deepcopy(saved.get("shared_list",{}))}

    def persist_metadata(self, record=True):
        for mod in self.mods:
            mod.tags=[t for t in mod.tags if t!=mod_lists.MISSING]+([mod_lists.MISSING] if mod.missing else [])
            if mod.missing: mod.enabled=False
            ident=mod_lists.identity(mod_lists.record(mod))
            if ident in self.list_records:
                self.list_records[ident].update(mod_lists.record(mod))
        self.state['shared_list']=copy.deepcopy(self.list_records)
        if record:
            self.history.record(self.editor_state())
        for mod in self.mods:
            self.state.setdefault("metadata", {})[mod.key] = {"group": mod.group, "tags": mod.tags, "alias": mod.alias}
        self.save()
        self.changed()

    def restore_edit(self, state):
        if state is None:
            return
        self.state['collections']=copy.deepcopy(state.get('collections',{}))
        self.list_records=copy.deepcopy(state.get('shared_list',{}))
        self.mods,self.list_records,_=mod_lists.attach(self.game_id,[m for m in self.mods if not m.missing],self.list_records,{},False)
        self.mods=workshop_collections.attach(self.game_id,self.mods,self.state.get('collections',{}),self.state.get('metadata',{}))
        transitions = {}
        for mod in self.mods:
            if mod.group and mod.key in state['mods']:
                transitions.setdefault(mod.group,set()).add(state['mods'][mod.key]['group'])
        renames = {old:next(iter(values)) for old,values in transitions.items() if len(values)==1 and next(iter(values))}
        order = self.view.get('group_orders', {}).get('自定义分组', [])
        if order:
            self.view['group_orders']['自定义分组'] = list(dict.fromkeys(renames.get(k,k) for k in order))
            self.state['view'] = dict(self.view)
        if self.group in renames: self.group = renames[self.group]
        for mod in self.mods:
            if mod.key in state["mods"]:
                values = state["mods"][mod.key]
                mod.enabled, mod.group, mod.alias = values["enabled"], values["group"], values["alias"]
                mod.tags, mod.package_title = list(values["tags"]), values["package_title"]
        self.master = state["master"]
        self.state["profiles"], self.state["titles"] = copy.deepcopy(state["profiles"]), copy.deepcopy(state["titles"])
        self.persist_metadata(record=False)

    def targets(self, keys):
        wanted = set(keys)
        mods = [m for m in self.mods if m.key in wanted]
        if len(mods) != len(wanted):
            raise ValueError("列表已变化，请重新选择模组。")
        return mods

    def toggle(self, keys, value=None):
        mods = [m for m in self.targets(keys) if not m.missing]
        available = [m for m in mods if m.loadable] or mods
        value = not all(m.enabled for m in available) if value is None else bool(value)
        if value and len(mods) == 1 and mods[0].game == "sts2":
            for other in self.mods:
                if other.id == mods[0].id and other.key != mods[0].key:
                    other.enabled = False
        for mod in mods:
            if not value or mod.loadable:
                mod.enabled = value
        self.history.record(self.editor_state())
        self.changed()

    def set_view(self, values):
        if "group_order" in values:
            order = values["group_order"]
            if not isinstance(order,list) or len(order)>10000 or any(not isinstance(k,str) for k in order):
                raise ValueError("分组顺序无效")
            self.view.setdefault("group_orders", {})[self.view["mode"]] = list(dict.fromkeys(order))
        if 'layout' in values:
            if values['layout'] not in ('table','covers'):
                raise ValueError('未知列表布局。')
            self.view['layout'] = values['layout']
        if "column_widths" in values:
            allowed = {"enabled", "nickname", "name", "version", "group", "tags", "folder", "source", "loader"}
            widths = values["column_widths"]
            if not isinstance(widths, dict) or any(k not in allowed or not isinstance(v, (int, float)) or
                                                  not 32 <= v <= 3000 for k, v in widths.items()):
                raise ValueError("列宽设置无效。")
            self.view["column_widths"] = {k: round(v, 2) for k, v in widths.items()}
        for key in ("mode", "show_folder", "hide_unavailable", "sort_column", "descending", "group_descending"):
            if key in values:
                self.view[key] = values[key]
        if 'local_filter' in values:
            if values['local_filter'] not in ('all','local','missing'):raise ValueError('未知本地文件筛选。')
            self.view['local_filter']=values['local_filter']
        if self.view["mode"] not in MODES:
            raise ValueError("未知展示方式。")
        self.query = str(values.get("query", self.query))
        self.group = str(values.get("group", self.group))
        self.state["view"] = dict(self.view)
        self.save()
        self.changed()

    def workshop_status(self, mod):
        if not mod.workshop_id or mod.source != 'steam_workshop':
            return 'local'
        if self.subscription_snapshot is None:
            return 'unknown'
        item = self.subscription_snapshot['items'].get(mod.workshop_id)
        if item is None:
            return 'unsubscribed'
        flags = item.get('state', 0)
        if flags & 16:
            return 'downloading'
        if flags & 32:
            return 'queued'
        if item.get('installed'):
            return 'installed'
        return 'subscribed'

    def snapshot(self):
        groups = ["全部分组", "未分组"] + sorted({m.group for m in self.mods if m.group})
        if self.group not in groups:
            self.group = "全部分组"
        group = None if self.group == "全部分组" else "" if self.group == "未分组" else self.group
        filtered=[m for m in self.mods if self.view.get('local_filter','all')=='all' or (m.missing==(self.view.get('local_filter')=='missing'))]
        if self.subscription_view is not None:
            filtered=[m for m in filtered if m.source=='steam_workshop' and m.workshop_id and m.workshop_id not in self.subscription_view['ids']]
        subscribed_states = {'subscribed','downloading','queued','installed'}
        subscribed = [m for m in self.mods if self.workshop_status(m) in subscribed_states]
        visible, sections = view_sections(filtered, mode=self.view["mode"], query=self.query, group=group,
                                         hide_unavailable=self.view["hide_unavailable"],
                                         sort_column=self.view["sort_column"], descending=self.view["descending"],
                                         group_descending=self.view["group_descending"],
                                         group_order=self.view.get("group_orders", {}).get(self.view["mode"], []))
        configs = list(self.catalog[self.game_id]["configs"])
        if self.config_path and self.config_path not in configs:
            configs.insert(0, self.config_path)
        return {"revision": self.revision, "game": self.game_id, "view": dict(self.view), "query": self.query,
                "group": self.group, "groups": groups, "profiles": sorted(self.state.get("profiles", {})),
                "master": self.master, "dirty": self.dirty(), "dirty_games": self.dirty_games(), "ready": self.session is not None,
                "undo": self.history.index > 0, "redo": self.history.index < len(self.history.states) - 1,
                "mods": [dict(m.record(), key=m.key, folder_label=m.folder_label,
                              changed=m.enabled != self.baseline.get(m.key, False), subscription_status=self.workshop_status(m)) for m in self.mods],
                "visible": [m.key for m in visible], "sections": [dict(key=s["key"], label=s["label"],
                    members=[m.key for m in s["members"]], shown=[m.key for m in s["shown"]]) for s in sections],
                "counts": {"disk_bytes": sum({m.package_key:m.package_bytes or 0 for m in self.mods}.values()), "total": len(self.mods), "shown": len(visible), "enabled": sum(m.enabled for m in self.mods),
                           "changed": sum(m.enabled != self.baseline.get(m.key, False) for m in self.mods),
                           "hidden": sum(m.superseded and not m.enabled for m in self.mods) if self.view["hide_unavailable"] else 0},
                "scan_state": dict(self.scan_state),
                "inventory": {"subscribed":len(subscribed) if self.subscription_snapshot is not None else None,
                               "subscribed_missing":sum(m.missing for m in subscribed), "local":sum(not m.missing for m in self.mods),"available":sum(not m.missing and m.loadable for m in self.mods),"missing":sum(m.missing for m in self.mods)},
                "status": self.status, "warning": self.warning, "configs": configs, "config_path": self.config_path,
                "game_dir": self.game_dir, "workshop_roots": self.state.get("workshop_roots", self.catalog[self.game_id]["workshop_roots"]),
                "cover_ids": self.covers.available(self.game_id),
                "backup_cache_enabled": bool(self.store.get("backup_cache_enabled",False)),
                "backup_cache_configured": "backup_cache_enabled" in self.store,
                "api_intro_seen": bool(self.store.get("web_ui",{}).get("api_intro_seen",False)),
                "demo_mode": bool(getattr(self,'demo_mode',False)),"subscription_mode":self.subscription_view is not None,"subscription_keys":[m.key for m in self.mods if m.source=="steam_workshop" and self.subscription_view is not None and m.workshop_id not in self.subscription_view["ids"]],
                "theme": self.store.get("web_ui", {}).get("theme", "system")}

    def edit(self, action, payload):
        keys = payload.get("keys", [])
        if action == "group_rename":
            old, new = str(payload["old"]), str(payload["value"]).strip()
            if not old or not new or new in ("全部分组", "未分组"):
                raise ValueError("请输入有效自定义分组名；未分组不能重命名。")
            if old != new and any(m.group == new for m in self.mods):
                raise ValueError("已有同名分组，请使用设置分组移动模组。")
            if not any(m.group == old for m in self.mods): raise ValueError("分组已不存在")
            for m in self.mods:
                if m.group == old: m.group = new
            if self.group == old: self.group = new
            order = self.view.get('group_orders', {}).get('自定义分组', [])
            if order:
                self.view['group_orders']['自定义分组'] = [new if k==old else k for k in order]
                self.state['view'] = dict(self.view)
            self.persist_metadata()
            return
        if action == "toggle":
            self.toggle(keys, payload.get("value"))
            return
        if action in ("undo", "redo"):
            self.restore_edit(self.history.undo() if action == "undo" else self.history.redo())
            return
        if action == "master":
            self.master = bool(payload["value"])
            self.history.record(self.editor_state())
            self.changed()
            return
        if action == "dependencies":
            report = check_local_dependencies(self.mods)
            if report['added']:
                self.history.record(self.editor_state())
                self.changed()
            # Older snapshots and imported lists can retain a verified ID even
            # when the corresponding mod is no longer in the current inventory.
            known_ids = {}
            known_names = {}
            for row in self.list_records.values():
                if row.get("name"):
                    known_names[row["id"]] = row["name"]
                if row.get('workshop_id'):
                    known_ids.setdefault(row['id'], set()).add(row['workshop_id'])
            for package in self.versions.index['packages'].values():
                if package['game'] != self.game_id:
                    continue
                for version in package['versions']:
                    for record in version['mods']:
                        if record.get("nickname") or record.get("name"):
                            known_names.setdefault(record["id"], record.get("nickname") or record["name"])
                        if record.get('workshop_id'):
                            known_ids.setdefault(record['id'], set()).add(record['workshop_id'])
            for row in report['rows']:
                row['name'] = row['name'] or known_names.get(row['id'], '')
                candidates = known_ids.get(row['id'], set())
                if row['status'] == 'missing' and not row['workshop_id'] and len(candidates) == 1:
                    row['workshop_id'] = next(iter(candidates))
            return report
        if action == "profile_save":
            name = str(payload["name"]).strip()
            if not name:
                raise ValueError("请输入快照名称。")
            if name in self.state.setdefault("profiles", {}) and not payload.get("overwrite"):
                raise ValueError("同名快照已存在。")
            self.state["profiles"][name] = {"enabled": [m.key for m in self.mods if m.enabled or (m.missing and m.wanted_enabled)], "master": self.master}
        elif action == "profile_restore":
            profile = self.state.get("profiles", {}).get(payload["name"])
            if not profile:
                raise ValueError("快照不存在。")
            restore_profile(self.mods, profile)
            self.master = profile.get("master", True)
        elif action == "profile_delete":
            self.state.get("profiles", {}).pop(payload["name"], None)
        else:
            mods = self.targets(keys)
            if action.startswith('tags_') and (mod_lists.MISSING in payload.get('tags',[]) or payload.get('old')==mod_lists.MISSING or mod_lists.MISSING in [t.strip() for t in str(payload.get('value','')).replace('，',',').split(',')]):
                raise ValueError('“缺失”是系统状态标签，扫描到文件后会自动移除，不能手动修改。')
            if action == "alias":
                for mod in mods:
                    mod.alias = str(payload["value"]).strip()
            elif action == "group":
                value = str(payload["value"]).strip()
                if value in ("全部分组", "未分组"):
                    raise ValueError("这个分组名称已保留；留空可清除分组。")
                for mod in mods:
                    mod.group = value
            elif action == "tags_add":
                tags = {tag.strip() for tag in str(payload["value"]).replace("，", ",").split(",") if tag.strip()}
                for mod in mods:
                    mod.tags = sorted(set(mod.tags) | tags)
            elif action == "tags_remove":
                for mod in mods:
                    mod.tags = sorted(set(mod.tags) - set(payload["tags"]))
            elif action == "tags_rename":
                old, new = payload["old"], str(payload["value"]).strip()
                if not new:
                    raise ValueError("请输入新标签名称。")
                for mod in mods:
                    if old in mod.tags:
                        mod.tags = sorted((set(mod.tags) - {old}) | {new})
            else:
                raise ValueError("未知编辑操作。")
        self.persist_metadata()

    def details(self, key, observe=True):
        mod = self.targets([key])[0]
        if observe:
            roots = {m.package_key for m in self.mods if m.id == mod.id}
            self.versions.observe(self.game_id, [m for m in self.mods if m.package_key in roots])
        return {"mod": dict(mod.record(), key=mod.key, folder_label=mod.folder_label),
                "workshop_health": self.state.get("workshop_health", {}).get(mod.workshop_id, {}),
                "versions": self.versions.versions(self.game_id, mod.id),
                "copies": [dict(m.record(), folder_label=m.folder_label, predicted=m.loadable and not m.missing and (self.game_id=='sts1' or m.key==mod.key), applied_enabled=self.baseline.get(m.key,False),master_enabled=self.master) for m in self.mods if m.id == mod.id]}

    def rollback_version(self, package_id, digest, mod_id):
        """Verify old bytes, force a safety snapshot, then install to mods as a draft."""
        import re
        import shutil
        import tempfile
        self.require_discard(False)
        if running_game_processes(self.game_id): raise ValueError('请先关闭游戏和加载器。')
        if not self.game_dir or not Path(self.game_dir).is_dir(): raise ValueError('请先设置有效的游戏目录。')
        package = self.versions.index['packages'][package_id]
        if package['game'] != self.game_id: raise ValueError('游戏不匹配。')
        version = next(v for v in package['versions'] if v['digest'] == digest)
        record = next(m for m in version['mods'] if m['id'] == mod_id)
        from release_store import package_relative_file
        relative = package_relative_file(record, package['source'], version['files'])
        if relative is None and len(version['mods']) == 1:
            suffix = '.jar' if self.game_id == 'sts1' else '.json'
            candidates = [name for name in version['files'] if Path(name).suffix.lower() == suffix]
            if len(candidates) == 1:
                relative = candidates[0]
        if relative is None:
            raise ValueError('无法唯一确定包内模组文件，请先取出文件检查。')
        prefix = Path(relative).parent.as_posix()
        if self.game_id == 'sts2':
            for other in version['mods']:
                if other['id'] == mod_id:
                    continue
                other_file = package_relative_file(other, package['source'], version['files'])
                if not other_file or prefix == '.' or Path(other_file).is_relative_to(prefix):
                    raise ValueError('备份中有其他模组共用此目录，请先取出文件并手动安装。')
        current = [m for m in self.mods if not m.missing and m.id == mod_id]
        mods_dir = Path(self.game_dir).resolve() / 'mods'
        if mods_dir.is_symlink() or mods_dir.resolve().parent != Path(self.game_dir).resolve():
            raise ValueError('mods 目录指向游戏目录之外，未自动写入；请先取出文件。')
        moves = []
        for m in current:
            if m.source != 'mods_directory': continue
            source = m.path.resolve() if self.game_id == 'sts1' else m.path.parent.resolve()
            if source == mods_dir or not source.is_relative_to(mods_dir):
                raise ValueError('现有本地版本直接共用 mods 根目录，请先取出文件检查；未覆盖现有文件。')
            if self.game_id == 'sts2' and any(other.id != mod_id and other.path.resolve().is_relative_to(source) for other in self.mods if not other.missing):
                raise ValueError('现有版本与其他模组共用目录，未替换。请先取出文件手动处理。')
            if source not in moves: moves.append(source)
        name = 'spire-history-' + (re.sub(r'[^\w.-]', '_', mod_id)[:48] or 'mod') + '-' + digest[:12]
        target = mods_dir / (name + '.jar' if self.game_id == 'sts1' else name)
        if target.exists() and target not in moves: raise ValueError('恢复目标已存在，未覆盖：' + str(target))
        self.directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='rollback-', dir=self.directory) as temporary:
            temporary = Path(temporary)
            include = [relative] if self.game_id == 'sts1' else [rel for rel in version['files'] if prefix == '.' or rel.startswith(prefix + '/')]
            extracted = Path(self.versions.extract(package_id, digest, temporary / 'verified', include=include))
            # Capture complete affected packages, even with caching disabled or an export already recorded.
            roots = {m.package_key for m in current}
            self.versions.capture(self.game_id, [m for m in self.mods if m.package_key in roots and not m.missing], force=True, update_inventory=False)
            mods_dir.mkdir(parents=True, exist_ok=True)
            saved = []
            installed = False
            try:
                for n, source in enumerate(moves):
                    backup = temporary / ('previous-' + str(n))
                    shutil.move(str(source), str(backup)); saved.append((source, backup))
                origin = extracted / relative if self.game_id == 'sts1' else extracted if prefix == '.' else extracted / prefix
                if target.exists(): raise ValueError('恢复目标已存在，未覆盖。')
                installed = True
                if self.game_id == 'sts1':
                    with origin.open('rb') as source, target.open('xb') as output: shutil.copyfileobj(source, output)
                else: shutil.copytree(origin, target)
                self.rescan(discard=True)
            except Exception:
                if installed and target.exists():
                    if target.is_dir(): shutil.rmtree(target)
                    else: target.unlink()
                for source, backup in reversed(saved): shutil.move(str(backup), str(source))
                self.rescan(discard=True)
                raise
            for m in self.mods:
                if m.id == mod_id and not m.missing:
                    restored = m.path.resolve() == target if self.game_id == 'sts1' else m.path.resolve().is_relative_to(target)
                    m.enabled = bool(restored and m.loadable)
                    if restored and current:
                        m.alias, m.group, m.tags = current[0].alias, current[0].group, list(current[0].tags)
            self.persist_metadata()
            self.versions.observe(self.game_id, self.mods)
        return {'path':str(target), 'backed_up':len(roots),
                'message':'旧版文件已放入 mods，当前版本已强制备份。请预览并应用开关后再启动。' +
                    ('塔一同 ID 工坊副本可能遮蔽旧版；请先处理重复副本，不能保证直接指定旧版。' if self.game_id == 'sts1' else '')}

    def prepare_apply(self):
        local_mods=[m for m in self.mods if not m.missing]
        if self.session is None:
            raise ValueError("请先成功扫描游戏配置。")
        if self.issues:
            raise ValueError("扫描存在异常，请先查看扫描提示：\n" + "\n".join(self.issues))
        problems = dependency_issues(local_mods)
        if problems and self.master:
            raise ValueError("\n".join(problems))
        plan = None
        if self.game_id == "sts2" and any(m.source == "steam_workshop" for m in local_mods):
            snapshot = self.steam(self.game_dir)
            if not self.workshop_snapshot or not same_snapshot(snapshot, self.workshop_snapshot):
                raise ValueError("Steam 工坊状态已变化或未同步，请重新扫描后预览。")
            plan = build_plan(local_mods, snapshot, self.session.path)
        data = self.session.render(local_mods, self.master, workshop_plan=plan)
        token = uuid.uuid4().hex
        self.pending_plan = {"token": token, "revision": self.revision, "steam": plan, "data": data}
        lines = [f"目标：{self.session.path}", "关闭游戏和 ModTheSpire；Steam 保持运行。", ""]
        lines += [("开启 " if m.enabled else "关闭 ") + f"{m.nickname} [{m.workshop_id or '本地'}] {m.path.name}"
                  for m in local_mods if m.enabled != self.baseline.get(m.key, False)]
        if self.game_id == "sts2":
            lines.append("模组总开关：" + ("开启" if self.master else "关闭"))
            if plan is not None:
                lines.extend(preview_lines(plan))
        else:
            lines.append("写入独立的 Spire Manager 列表并设为默认，保留其他列表。")
        lines.append("\n应用前自动备份；不更改订阅或模组文件。")
        return {"token": token, "text": "\n".join(lines)}

    def commit(self, token):
        local_mods=[m for m in self.mods if not m.missing]
        pending = self.pending_plan
        if not pending or pending["token"] != token or pending["revision"] != self.revision:
            raise ValueError("选择已变化，请重新预览。")
        running = running_game_processes(self.game_id)
        if running:
            raise ValueError("请先关闭游戏／加载器：" + ", ".join(running))
        receipt = None
        if pending["steam"] is not None:
            roots = self.state.get("workshop_roots", self.catalog[self.game_id]["workshop_roots"])
            backup, receipt, after = commit_plan(self.session, pending["data"], pending["steam"], local_mods,
                                                 self.game_dir, roots, self.directory / "backups", api=self.steam)
            self.workshop_snapshot = after
            bind_snapshot(local_mods, after)
        else:
            backup = self.session.write(pending["data"], self.directory / "backups")
        self.baseline = {m.key: m.enabled for m in local_mods}
        self.status = "已应用，下次启动生效"
        self.changed()
        return {"backup": str(backup), "receipt": str(receipt) if receipt else ""}

    def apply_titles(self, titles, overwrite=False, only_ids=None):
        changes = []
        for mod in self.mods:
            if not mod.workshop_id or (only_ids is not None and mod.workshop_id not in only_ids):
                continue
            old_title, alias = mod.package_title, mod.alias
            valid = old_title if old_title != "工坊标题未缓存" else ""
            old_nickname = mod.nickname
            automatic = alias in ("", old_title, mod.name, workshop_nickname(valid, mod.name, mod.path))
            if mod.workshop_id in titles:
                mod.package_title = titles[mod.workshop_id]
                if mod.collection_placeholder:
                    mod.name = mod.package_title
                if overwrite or automatic:
                    mod.alias = ""
            changes.append({"id": mod.workshop_id, "key": mod.key, "name": mod.name,
                            "old_title": old_title, "new_title": mod.package_title, "old_nickname": old_nickname,
                            "new_nickname": mod.nickname, "action": "保留手动昵称" if mod.alias else "更新自动昵称"})
        self.state.setdefault("titles", {}).update(titles)
        for collection in self.state.get('collections',{}).values():
            for wid,item in collection['items'].items():
                if wid in titles:item.update(title=titles[wid],confirmed=True)
        self.persist_metadata()
        return changes


    def workshop_health(self, entry):
        item = entry['id']
        if entry['status'] not in ('success','removed','deferred'): return
        health = self.state.setdefault('workshop_health', {})
        previous = health.get(item, {})
        suspect = entry['status']=='removed' or bool(entry.get('suspected'))
        if entry['status']!='success' and not suspect: return
        owned = set(previous.get('tag_keys', []))
        for mod in self.mods:
            if mod.workshop_id != item: continue
            if suspect and '已下架' not in mod.tags:
                mod.tags.append('已下架'); owned.add(mod.key)
            elif not suspect and mod.key in owned:
                mod.tags = [t for t in mod.tags if t!='已下架']
        health[item] = {'status':'存疑' if suspect else '正常', 'tag_keys':list(owned) if suspect else [],
                        'error':entry.get('error',''), 'checked_at':entry.get('checked_at','')}
        self.persist_metadata(record=False)
