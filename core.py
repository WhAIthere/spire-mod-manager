"""Local-only mod catalog and configuration adapters. No mod code is executed."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime

APP_IDS = {"sts1": "646570", "sts2": "2868840"}


def workshop_nickname(title, name, path):
    """Prefer Chinese Workshop/mod/JAR names, then the actual Workshop title."""
    path = Path(path)
    for candidate in (title, name, path.stem if path.suffix.lower() == ".jar" else ""):
        if re.search(r"[\u3400-\u9fff]", candidate):
            return candidate
    return title or name


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def atomic_json(path: Path, data, *, ascii_only=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=ascii_only, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def steam_libraries():
    roots = []
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                roots.append(Path(winreg.QueryValueEx(key, "SteamPath")[0]))
        except OSError:
            pass
    for root in list(roots):
        vdf = root / "steamapps/libraryfolders.vdf"
        if vdf.exists():
            for value in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8")):
                candidate = Path(value.replace("\\\\", "\\"))
                if candidate not in roots:
                    roots.append(candidate)
    return roots


def discover():
    libs = steam_libraries()
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    roaming = Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming")))
    result = {}
    for game, appid in APP_IDS.items():
        folder = "SlayTheSpire" if game == "sts1" else "Slay the Spire 2"
        installs = [p / "steamapps/common" / folder for p in libs]
        config = [local / "ModTheSpire/mod_lists.json"] if game == "sts1" else list(
            (roaming / "SlayTheSpire2").glob("steam/*/settings.save"))
        if game == "sts2":
            config += list((roaming / "SlayTheSpire2").glob("default/*/settings.save"))
        config = sorted((p for p in config if p.exists()), key=lambda p: p.stat().st_mtime, reverse=True)
        result[game] = {
            "game_dir": str(next((p for p in installs if p.exists()), installs[0] if installs else "")),
            "workshop_roots": [str(p / "steamapps/workshop/content" / appid) for p in libs
                               if (p / "steamapps/workshop/content" / appid).exists()],
            "configs": [str(p) for p in config],
        }
    return result


@dataclass
class Mod:
    game: str
    id: str
    name: str
    version: str
    path: Path
    source: str
    workshop_id: str = ""
    authors: str = ""
    dependencies: list = field(default_factory=list)
    enabled: bool = False
    group: str = ""
    tags: list = field(default_factory=list)
    package_root: Path | None = None
    package_title: str = ""
    loader_status: str = ""
    loadable: bool = True
    alias: str = ""
    superseded: bool = False
    compatibility: dict = field(default_factory=dict)
    disk_bytes: int | None = None
    package_bytes: int | None = None
    workshop_disabled: bool | None = None

    @property
    def nickname(self):
        title = self.package_title if self.workshop_id and self.package_title != "工坊标题未缓存" else ""
        return self.alias or (workshop_nickname(title, self.name, self.path) if title else self.name)

    @property
    def folder_label(self):
        if not self.workshop_id:
            return "本地 mods"
        title = self.package_title if self.package_title != "工坊标题未缓存" else ""
        return f"{title} [{self.workshop_id}]" if title else f"工坊 {self.workshop_id}"

    @property
    def key(self):
        relative = self.path.relative_to(self.package_root).as_posix() if self.package_root else self.path.name
        return f"{self.source}:{self.id}@{self.workshop_id or 'local'}/{relative}"

    @property
    def legacy_key(self):
        return f"{self.source}:{self.id}"

    @property
    def package_key(self):
        return str(self.package_root or self.path.parent)

    def record(self):
        return {"id": self.id, "name": self.name, "version": self.version,
                "alias": self.alias, "nickname": self.nickname,
                "path": str(self.path), "source": self.source, "workshop_id": self.workshop_id,
                "enabled": self.enabled, "group": self.group, "tags": self.tags,
                "dependencies": self.dependencies, "compatibility": self.compatibility,
                "disk_bytes": self.disk_bytes, "package_bytes": self.package_bytes,
                "package_root": self.package_key, "package_title": self.package_title,
                "loader_status": self.loader_status, "loadable": self.loadable}


def read_legacy_json(path):
    try:
        return read_json(Path(path))
    except UnicodeDecodeError:
        return json.loads(Path(path).read_bytes().decode("gb18030"))


def cached_workshop_info():
    path = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "ModTheSpire/WorkshopInfo.json"
    try:
        data = read_legacy_json(path)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def semver(value):
    match = re.fullmatch(r"[vV]?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?", str(value))
    if not match:
        return None
    return tuple(int(x or 0) for x in match.groups()[:3]), match[4]


def compare_versions(left, right):
    """SemVer ordering for loader estimates, including numeric prerelease identifiers."""
    a, b = semver(left), semver(right)
    if a is None or b is None:
        return None
    if a[0] != b[0]:
        return (a[0] > b[0]) - (a[0] < b[0])
    if a[1] is None or b[1] is None:
        return (a[1] is None) - (b[1] is None)
    for x, y in zip(a[1].split("."), b[1].split(".")):
        if x == y:
            continue
        if x.isdecimal() and y.isdecimal():
            return (int(x) > int(y)) - (int(x) < int(y))
        if x.isdecimal() != y.isdecimal():
            return -1 if x.isdecimal() else 1
        return (x > y) - (x < y)
    return (len(a[1].split(".")) > len(b[1].split("."))) - (len(a[1].split(".")) < len(b[1].split(".")))


def resolve_candidates(mods):
    """Keep physical files visible. Do not pretend native configs can select arbitrary paths."""
    groups = {}
    for mod in mods:
        groups.setdefault((mod.game, mod.id), []).append(mod)
        mod.loadable, mod.loader_status = True, "可配置"
        mod.superseded = False
    for (game, _), candidates in groups.items():
        if len(candidates) < 2:
            continue
        if game == "sts2":
            # Separate Workshop items can be selected through Steam's per-item switches.
            # The complete folder/ID closure is validated when preparing the apply plan.
            for candidate in candidates:
                candidate.loader_status = "同 ID 副本：可选择文件，应用前检查工坊目录冲突"
            continue
        best = candidates[0]
        for candidate in candidates[1:]:
            comparison = compare_versions(candidate.version, best.version)
            if candidate.source == "steam_workshop" and comparison is not None and comparison > 0:
                best = candidate
        # Equal/unknown Workshop versions depend on enumeration order, not just filename/path.
        ambiguous = any(c is not best and c.source == "steam_workshop" and
                        (compare_versions(c.version, best.version) is None or
                         (best.source == "steam_workshop" and compare_versions(c.version, best.version) == 0)) for c in candidates)
        if len([c for c in candidates if c.source == "mods_directory"]) > 1:
            ambiguous = True
        for candidate in candidates:
            candidate.loadable = candidate is best and not ambiguous
            candidate.superseded = candidate is not best and not ambiguous
            candidate.loader_status = ("重复 ID：版本相同或未知，需核对加载器" if ambiguous else
                                       "预计采用此版本（以启动器为准）" if candidate is best else
                                       f"被 {best.path.name} {best.version} 遮蔽，不能独立指定")


def metadata(game, manifest, path, source, workshop_id=""):
    mod_id = str(manifest.get("modid" if game == "sts1" else "id", "")).strip()
    if not mod_id:
        raise ValueError("模组清单缺少 ID")
    deps = manifest.get("dependencies") or []
    if not isinstance(deps, list):
        raise ValueError("dependencies 不是数组")
    parsed = []
    for dep in deps:
        if isinstance(dep, str):
            parsed.append({"id": dep})
        elif isinstance(dep, dict) and dep.get("id"):
            parsed.append(dict(dep))
        else:
            raise ValueError("无法识别的依赖格式")
    authors = manifest.get("author_list", manifest.get("author", ""))
    if isinstance(authors, list):
        authors = ", ".join(str(x) for x in authors)
    return Mod(game, mod_id, str(manifest.get("name") or mod_id),
               str(manifest.get("version", "")), path, source, workshop_id, str(authors), parsed)


def scan(game, game_dir, workshop_roots, titles=None, manifest_cache=None):
    mods, issues = [], []
    manifest_cache = manifest_cache if manifest_cache is not None else {}
    titles = dict(titles or {})
    cached = cached_workshop_info() if game == "sts1" else []
    for item in cached:
        workshop_id = Path(item.get("installPath", "")).name
        if workshop_id.isdecimal() and item.get("title"):
            titles.setdefault(workshop_id, item["title"])
    roots = [(Path(game_dir) / "mods", "mods_directory", "")] if game_dir else []
    for root_text in workshop_roots:
        root = Path(root_text)
        if not root.is_dir():
            issues.append(f"工坊目录不存在：{root}")
            continue
        roots.extend((child, "steam_workshop", child.name) for child in sorted(root.iterdir())
                     if child.is_dir() and child.name.isdecimal())
    seen_paths = set()
    size_cache = {}
    def directory_size(directory):
        key = str(directory.resolve())
        if key not in size_cache:
            try: size_cache[key] = sum(p.stat().st_size for p in directory.rglob("*") if p.is_file())
            except OSError: size_cache[key] = None
        return size_cache[key]
    for root, source, workshop_id in roots:
        if not root.is_dir():
            continue
        # MTS reads top-level jars; STS2 packages can have their own subdirectory.
        paths = sorted(root.glob("*.jar")) if game == "sts1" else sorted(root.rglob("*.json"))
        for path in paths:
            if path.resolve() in seen_paths:
                continue
            seen_paths.add(path.resolve())
            try:
                cache_key=str(path.resolve());stat=path.stat()
                stamp=(game,stat.st_mtime_ns,stat.st_ctime_ns,stat.st_size)
                cached=manifest_cache.get(cache_key)
                if cached and cached[0]==stamp:
                    manifest=copy.deepcopy(cached[1])
                else:
                    if game == "sts1":
                        with zipfile.ZipFile(path) as jar:
                            if "ModTheSpire.json" not in jar.namelist():
                                if "ModTheSpire.config" in jar.namelist():
                                    issues.append(f"旧格式模组，暂不支持：{path}")
                                continue
                            info = jar.getinfo("ModTheSpire.json")
                            if info.file_size > 2_000_000:
                                raise ValueError("清单过大")
                            manifest = json.loads(jar.read(info).decode("utf-8-sig"))
                    else:
                        if path.stat().st_size > 2_000_000:
                            continue
                        manifest = read_json(path)
                        if not isinstance(manifest, dict) or not manifest.get("id"):
                            continue
                        # STS2 accepts JSON-only manifests too. Match its ID-based discovery
                        # so such entries cannot bypass the folder conflict check.
                    after=path.stat()
                    if (after.st_mtime_ns,after.st_ctime_ns,after.st_size)!=(stat.st_mtime_ns,stat.st_ctime_ns,stat.st_size):
                        raise ValueError('读取期间清单发生变化，请等待 Steam 下载完成后重试')
                    manifest_cache[cache_key]=(stamp,copy.deepcopy(manifest))
                mod = metadata(game, manifest, path, source, workshop_id)
                mod.compatibility = {k: v for k, v in manifest.items() if k.lower() in ("sts_version", "mts_version", "game_version", "min_game_version", "max_game_version", "supported_game_versions", "minimum_game_version", "maximum_game_version")}
                mod.disk_bytes = path.stat().st_size if game == "sts1" else directory_size(path.parent)
                mod.package_bytes = directory_size(root)
                mod.package_root = root
                mod.package_title = titles.get(workshop_id, "工坊标题未缓存") if workshop_id else "本地 mods"
                mods.append(mod)
            except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
                issues.append(f"读取失败：{path}：{exc}")
    active={str(p) for p in seen_paths}
    for key in list(manifest_cache):
        if key not in active:manifest_cache.pop(key,None)
    resolve_candidates(mods)
    return mods, issues


def filter_mods(mods, query="", group=None):
    try:
        words = shlex.split(query, posix=True)
    except ValueError:
        words = query.split()
    result = []
    for mod in mods:
        if group is not None and mod.group != group:
            continue
        fields = {"name": mod.name, "nickname": mod.nickname, "alias": mod.nickname, "id": mod.id, "version": mod.version,
                  "tag": " ".join(mod.tags), "group": mod.group, "author": mod.authors,
                  "state": "on" if mod.enabled else "off", "source": mod.source,
                  "workshop": mod.workshop_id, "package": mod.package_title,
                  "file": str(mod.path), "status": mod.loader_status}
        haystack = " ".join(fields.values()).casefold()
        matched = True
        for word in words:
            key, sep, value = word.partition(":")
            if sep and key.casefold() in fields:
                text = fields[key.casefold()].casefold()
                # A tag token matches one complete tag, not a substring of another tag.
                ok = value.casefold() in [t.casefold() for t in mod.tags] if key.casefold() == "tag" else value.casefold() in text
            else:
                ok = word.casefold() in haystack
            if not ok:
                matched = False
                break
        if matched:
            result.append(mod)
    return result


def restore_profile(mods, profile):
    enabled = set(profile["enabled"])
    for mod in mods:
        mod.enabled = mod.key in enabled or (mod.loadable and mod.legacy_key in enabled)


def dependency_issues(mods):
    by_id = {}
    for mod in mods:
        if mod.enabled:
            by_id.setdefault(mod.id, []).append(mod)
    issues = []
    def version_tuple(value):
        # Only compare plain numeric versions; prerelease/unknown versions need manual review.
        match = re.fullmatch(r"v?(\d+(?:\.\d+){0,3})", str(value).strip())
        return tuple(int(x) for x in match[1].split(".")) if match else None
    for mod in mods:
        if not mod.enabled:
            continue
        if len(by_id[mod.id]) > 1:
            issues.append(f"重复启用 ID：{mod.id}")
        for dep in mod.dependencies:
            candidates = by_id.get(dep["id"], [])
            if not candidates:
                issues.append(f"{mod.name} 需要启用 {dep['id']}")
            elif dep.get("min_version"):
                actual = version_tuple(candidates[0].version)
                minimum = version_tuple(dep["min_version"])
                if actual is None or minimum is None:
                    issues.append(f"请核实版本：{mod.name} 需要 {dep['id']} >= {dep['min_version']}")
                elif actual + (0,) * (4 - len(actual)) < minimum + (0,) * (4 - len(minimum)):
                    issues.append(f"版本不足：{mod.name} 需要 {dep['id']} >= {dep['min_version']}")
    return sorted(set(issues))


def enable_local_dependencies(mods):
    """Enable installed, uniquely resolved requirements only. Never perform network operations."""
    by_id = {}
    for mod in mods:
        by_id.setdefault(mod.id, []).append(mod)
    added = []
    missing, ambiguous = set(), set()
    changed = True
    while changed:
        changed = False
        for mod in mods:
            if not mod.enabled:
                continue
            for requirement in mod.dependencies:
                ident = requirement["id"]
                candidates = [m for m in by_id.get(ident, []) if m.loadable]
                if any(m.enabled for m in candidates):
                    continue
                if len(candidates) == 1:
                    candidates[0].enabled = True
                    added.append(candidates[0])
                    changed = True
                elif not by_id.get(ident):
                    missing.add(ident)
                else:
                    ambiguous.add(ident)
    return added, sorted(missing), sorted(ambiguous)


class ConfigSession:
    def __init__(self, game, path):
        self.game, self.path = game, Path(path)
        self.original_hash = digest(self.path)
        if self.original_hash is None:
            raise ValueError("配置文件不存在；请先通过游戏生成配置。")
        try:
            self.data = read_json(self.path)
        except UnicodeDecodeError:
            if game != "sts1":
                raise
            # Java 8 uses the Windows system code page; Chinese installations often write GBK.
            self.data = json.loads(self.path.read_bytes().decode("gb18030"))
        if not isinstance(self.data, dict):
            raise ValueError("配置根节点不是对象")
        if game == "sts1":
            lists = self.data.get("lists")
            current = self.data.get("defaultList")
            if not isinstance(lists, dict) or not isinstance(lists.get(current), list):
                raise ValueError("无法识别塔 1 的默认模组列表")
        else:
            settings = self.data.get("mod_settings")
            if not isinstance(settings, dict) or not isinstance(settings.get("mod_list"), list):
                raise ValueError("无法识别塔 2 的 mod_settings.mod_list")
            if any(not isinstance(entry, dict) or not entry.get("id") or not entry.get("source")
                   or not isinstance(entry.get("is_enabled"), bool) for entry in settings["mod_list"]):
                raise ValueError("模组开关格式发生变化，停止写入")

    @property
    def master_enabled(self):
        return self.game == "sts1" or self.data["mod_settings"].get("mods_enabled", False)

    def load_states(self, mods):
        if self.game == "sts1":
            enabled = set(self.data["lists"][self.data["defaultList"]])
            resolved_ids = {m.id for m in mods if m.loadable}
            for mod in mods:
                mod.enabled = (mod.loadable or mod.id not in resolved_ids) and mod.path.name in enabled
        else:
            enabled = {(x["source"], x["id"]): x["is_enabled"] for x in self.data["mod_settings"]["mod_list"]}
            for mod in mods:
                mod.enabled = bool(enabled.get((mod.source, mod.id), False))

    def render(self, mods, master=True, workshop_plan=None):
        if len({m.key for m in mods}) != len(mods):
            raise ValueError("扫描结果有重复标识，请先解决重复模组")
        unavailable = [f"{m.name} [{m.workshop_id or 'local'}]：{m.loader_status}" for m in mods if m.enabled and not m.loadable]
        if unavailable:
            raise ValueError("原生开关无法指定以下副本，请关闭这些条目后应用：\n" + "\n".join(unavailable))
        data = copy.deepcopy(self.data)
        if self.game == "sts1":
            # Keep existing order, including offline/unavailable entries; change only known mods.
            current = data["lists"][data["defaultList"]]
            filenames = [m.path.name for m in mods]
            known = set(filenames)
            enabled = {m.path.name for m in mods if m.enabled}
            # Identical names within one duplicate-ID entry can safely be disabled together.
            owners = {}
            for m in mods:
                if m.loadable:
                    owners.setdefault(m.path.name, set()).add(m.id)
            if any(len(ids) > 1 and filename in enabled for filename, ids in owners.items()):
                raise ValueError("塔 1 有重名 JAR，加载器无法区分，请先处理")
            chosen = [name for name in current if name not in known or name in enabled]
            chosen += [m.path.name for m in mods if m.enabled and m.path.name not in chosen]
            data["lists"]["Spire Manager"] = chosen
            data["defaultList"] = "Spire Manager"
        else:
            if workshop_plan is not None:
                from steam_workshop import validate_plan
                validate_plan(workshop_plan, mods)
            else:
                owners = {}
                for mod in mods:
                    owners.setdefault((mod.source, mod.id), []).append(mod)
                if any(len(entries) > 1 and any(m.enabled for m in entries) for entries in owners.values()):
                    raise ValueError("仅游戏配置无法指定同 ID 副本，请通过工坊目录同步计划应用。")
            settings = data["mod_settings"]
            states = {}
            for mod in mods:
                key = (mod.source, mod.id)
                states[key] = states.get(key, False) or mod.enabled
            existing = set()
            for entry in settings["mod_list"]:
                key = (entry["source"], entry["id"])
                if key in existing:
                    raise ValueError("原配置包含重复模组项，请先在游戏中核对：" + str(key))
                existing.add(key)
                if key in states:
                    entry["is_enabled"] = states[key]
            for (source, mod_id), enabled in states.items():
                if (source, mod_id) not in existing:
                    settings["mod_list"].append({"id": mod_id, "is_enabled": enabled, "source": source})
            settings["mods_enabled"] = bool(master)
        return data

    def write(self, data, backup_dir):
        if digest(self.path) != self.original_hash:
            raise ValueError("配置已被游戏或其他程序修改，请重新扫描后再应用。")
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        stem = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        backup = backup_dir / f"{self.game}-{stem}.json"
        original_bytes = self.path.read_bytes()
        backup.write_bytes(original_bytes)
        atomic_json(backup.with_suffix(".info.json"), {"target": str(self.path), "sha256": self.original_hash})
        if digest(self.path) != self.original_hash:
            raise ValueError("备份过程中配置发生变化，请重新扫描。")
        # Java 8 may read with GBK/Windows ANSI; ASCII JSON escapes work with both ANSI and UTF-8.
        atomic_json(self.path, data, ascii_only=self.game == "sts1")
        self.data, self.original_hash = copy.deepcopy(data), digest(self.path)
        return backup


def running_game_processes(game):
    if os.name != "nt":
        raise ValueError("本版本只在 Windows 上验证了进程检查")
    script = ("Get-CimInstance Win32_Process | Where-Object { "
              "$_.Name -match '^(SlayTheSpire.*|javaw?|mts-launcher)\\.exe$' } | "
              "Select-Object Name,CommandLine | ConvertTo-Json -Compress")
    proc = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    if proc.returncode:
        raise ValueError("无法检查游戏进程，请关闭游戏后重试")
    raw = proc.stdout.decode("utf-8", errors="replace").strip()
    if not raw:
        return []
    rows = json.loads(raw)
    rows = [rows] if isinstance(rows, dict) else rows
    result = []
    for row in rows:
        name = row["Name"].lower()
        command = str(row.get("CommandLine") or "").lower()
        if game == "sts2":
            matched = name.startswith("slaythespire2")
        else:
            matched = (name.startswith("slaythespire") and not name.startswith("slaythespire2")) or name == "mts-launcher.exe"
            if name in ("java.exe", "javaw.exe"):
                # Unknown command lines are conservatively treated as a possible loader.
                matched = not command or any(x in command for x in ("modthespire", "mts-launcher", "slaythespire", "desktop-1.0.jar"))
        if matched:
            result.append(row["Name"])
    return result
