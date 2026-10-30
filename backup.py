"""Whole-package ZIP snapshots and optional public Workshop metadata lookup."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone
import urllib.request
import urllib.error
import urllib.parse
import zipfile
from html.parser import HTMLParser
import time
import re


class WorkshopTitleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside = False
        self.parts = []
        self.language = ""
        self.title_depth = 0
        self.hidden_tag = ""
        self.visible_parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden_tag = tag
        if tag == "html":
            self.language = dict(attrs).get("lang", "")
        if tag == "div" and self.inside:
            self.title_depth += 1
        elif tag == "div" and not self.parts and "workshopItemTitle" in dict(attrs).get("class", "").split():
            self.inside = True
            self.title_depth = 1

    def handle_endtag(self, tag):
        if tag == self.hidden_tag:
            self.hidden_tag = ""
        if tag == "div" and self.inside:
            self.title_depth -= 1
            self.inside = self.title_depth > 0

    def handle_data(self, data):
        if not self.hidden_tag:
            self.visible_parts.append(data)
        if self.inside:
            self.parts.append(data)


def query_chinese_workshop_title(workshop_id):
    url = f"https://steamcommunity.com/sharedfiles/filedetails/?id={workshop_id}&l=schinese"
    entry = {"id": workshop_id, "url": url, "title": "", "status": "failed", "error": "", "source": "web_schinese"}
    started = time.monotonic()
    def diagnostic_headers(headers):
        return {key: headers.get(key) for key in ("Retry-After", "Server", "Content-Type", "Via", "X-Cache") if headers.get(key)}
    entry["attempts"] = 1
    request = urllib.request.Request(url, headers={
        "User-Agent": "SpireLocalModManager/0.7", "Accept-Language": "zh-CN,zh;q=0.9"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            entry["response_url"] = response.geturl()
            entry["http_status"] = response.status
            entry["response_headers"] = diagnostic_headers(getattr(response, "headers", {}))
            returned_id = urllib.parse.parse_qs(urllib.parse.urlparse(response.geturl()).query).get("id", [])
            if returned_id != [workshop_id]:
                raise ValueError("页面重定向后工坊号不匹配，拒绝使用此标题")
            page = response.read().decode("utf-8", errors="replace")
            parser = WorkshopTitleParser()
            parser.feed(page)
        title = "".join(parser.parts).strip()
        visible = re.sub(r"\s+", "", " ".join(parser.visible_parts))
        entry["page_title"], entry["page_language"] = title, parser.language
        if title:
            entry.update(title=title, status="success", error="")
        elif any(re.sub(r"\s+", "", marker) in visible for marker in (
                "That item does not exist. It may have been removed by the author.",
                "该物品不存在。可能已被作者移除", "该物品不存在。它可能已被作者移除",
                "此物品不存在。可能已被作者移除")):
            entry.update(status="removed", error="工坊页面明确提示条目不存在／已被作者移除")
        else:
            entry["error"] = "未取得标题，也没有明确下架提示；可能为登录限制、验证页或异常响应，尚未确认"
            entry["response_excerpt"] = re.sub(r"\s+", " ", " ".join(parser.visible_parts))[-1600:]
    except Exception as exc:
        entry["error"] = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, urllib.error.HTTPError):
            entry["http_status"] = exc.code
            entry["response_headers"] = diagnostic_headers(exc.headers or {})
            try:
                entry["response_excerpt"] = re.sub(r"\s+", " ", exc.read(2048).decode("utf-8", errors="replace"))
            except (OSError, AttributeError, ValueError):
                pass
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if retry_after:
                try:
                    entry["retry_after"] = max(0, float(retry_after))
                except ValueError:
                    from email.utils import parsedate_to_datetime
                    try:
                        entry["retry_after"] = max(0, parsedate_to_datetime(retry_after).timestamp() - time.time())
                    except (ValueError, TypeError, OverflowError):
                        pass
    entry["seconds"] = round(time.monotonic() - started, 2)
    return entry


def localized_workshop_title(workshop_id):
    entry = query_chinese_workshop_title(workshop_id)
    return workshop_id, entry["title"]


from core import APP_IDS


def fetch_workshop_titles(appid, ids, progress=None, cached_titles=None, **options):
    from workshop_queue import run_title_queue
    api_key = options.pop("api_key", "")
    if api_key:
        from steam_account import AccountTitleQuery
        query = AccountTitleQuery(appid, ids, api_key,stop_event=options.get("stop_event"))
        options["transport"] = "account_api"
    else:
        query = query_chinese_workshop_title
    return run_title_queue(appid, ids, progress, cached_titles, query=query, **options)


def package_roots(spec):
    roots = []
    for index, value in enumerate(spec.get("workshop_roots", [])):
        parent = Path(value)
        if not parent.is_dir():
            raise ValueError(f"工坊目录不存在：{parent}")
        roots += [(child, f"workshop/library-{index}/{child.name}") for child in sorted(parent.iterdir())
                  if child.is_dir() and child.name.isdecimal()]
    if spec.get("game_dir"):
        game = Path(spec["game_dir"])
        local = game / "mods"
        if local.is_dir():
            roots.append((local, "local-mods"))
        # Preserve the user's manually disabled mods as well, without treating them as active mods.
        roots += [(child, f"disabled-mods/{child.name}") for child in sorted(game.glob("mods*"))
                  if child.is_dir() and child.name != "mods"]
    return roots


def backup_plan(specs):
    files, roots, seen = [], [], set()
    def fail_walk(error):
        raise error
    for game, spec in specs.items():
        if game not in APP_IDS:
            raise ValueError("未知游戏：" + game)
        for root, label in package_roots(spec):
            real_root = root.resolve()
            if root.is_symlink() or (os.name == "nt" and root.lstat().st_file_attributes & 0x400):
                raise ValueError(f"为避免备份目录外的文件，请先处理链接目录：{root}")
            if real_root in seen:
                continue
            seen.add(real_root)
            roots.append({"game": game, "source": str(root), "archive": f"payload/{game}/{label}"})
            for directory, folders, names in os.walk(root, followlinks=False, onerror=fail_walk):
                for name in folders:
                    candidate = Path(directory) / name
                    # Windows directory junctions may not be reported as symlinks on Python 3.11.
                    if candidate.is_symlink() or (os.name == "nt" and candidate.lstat().st_file_attributes & 0x400):
                        raise ValueError(f"目录含链接，停止备份：{candidate}")
                for name in sorted(names):
                    path = Path(directory) / name
                    if path.is_symlink() or not path.resolve().is_relative_to(real_root):
                        raise ValueError(f"文件链接超出备份目录：{path}")
                    archive = f"payload/{game}/{label}/" + path.relative_to(root).as_posix()
                    stat = path.stat()
                    files.append((path, archive, stat.st_size, stat.st_mtime_ns))
    if not files:
        raise ValueError("没有找到可备份的模组文件")
    return roots, files


RESTORE_HELP = """这是模组文件快照，不是游戏或 Steam 的完整备份。

manifest.json：工坊编号、原路径、文件 SHA-256、模组清单和管理器记录。
payload/<游戏>/workshop/library-N/<工坊编号>/：整个工坊条目的原始内容。
payload/<游戏>/local-mods/：当时本地 mods 目录的原始内容。
payload/<游戏>/disabled-mods/：游戏目录中 mods* 手动禁用文件夹的原始内容。
configs/：发现的游戏配置原始文件。可能有多个账户；恢复前务必核对目标。

离线转为本地模组：
1. 解压到一个单独的新目录，不要直接覆盖游戏。
2. 塔 1：把所需工坊条目顶层的 JAR 放进游戏 mods 目录的顶层；附带资源保留
   原相对结构，并按该模组说明放置。不要把所有数字目录直接丢进 mods，MTS 不递归
   扫描子目录。ModTheSpire 加载器本身仍需安装。一个条目带多个模组时一起恢复。
3. 塔 2：保留同一模组的 JSON、DLL、PCK 及依赖、资源，放进游戏 mods 下相应模组目录；
   若原包顶层就放着这些文件，可新建一个条目编号目录容纳它们。还需满足游戏版本要求。
4. 同一 ID 的多个版本请挑选需要的一份。塔 1 在仍订阅新版工坊副本时可能优先选择新版；
   塔 2 也可能遇到重复 ID。仅复制备份不代表成功锁定加载版本。
5. 本地恢复后来源由 steam_workshop 变为 mods_directory。请重新扫描、选择启用，
   原来的工坊开关清单不能保证直接迁移。不要批量改写来源来代替实际文件安装。

不建议把本地模组反向放进工坊数字目录：Steam 的订阅／安装记录不会随复制生成，
游戏未必枚举它，Steam 后续更新还可能覆盖该目录。请使用本地 mods 目录。

配置与库记录可以回退开关、标签、分组；ZIP 保存当时文件。没有保存游戏本体、
存档或所有分散存放的模组设置，不能保证跨游戏版本重现运行环境。部分模组有
自定义外部资源路径，需按作者说明恢复。本工具没有执行模组来验证兼容性。
"""


def export_bundle(destination, specs, manager_data, catalogs=None, progress=None):
    """Atomic archive creation; abort on changed/unreadable files, retain any existing ZIP."""
    destination = Path(destination)
    roots, files = backup_plan(specs)
    for root in roots:
        if destination.resolve().is_relative_to(Path(root["source"]).resolve()):
            raise ValueError("压缩包不能放在被备份的模组目录内")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="spire-backup-", suffix=".partial", dir=destination.parent)
    os.close(fd)
    total = sum(size for _, _, size, _ in files)
    done, records = 0, []
    manifest = {"schema": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
                "games": list(specs), "roots": roots, "catalogs": catalogs or {},
                "manager_data": manager_data, "files": records, "configs": []}
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
            for path, target, size, mtime in files:
                hasher = hashlib.sha256()
                info = zipfile.ZipInfo.from_file(path, arcname=target)
                info.compress_type = zipfile.ZIP_STORED if path.suffix.lower() in (".jar", ".zip", ".png", ".jpg", ".ogg", ".mp3", ".pck") else zipfile.ZIP_DEFLATED
                with path.open("rb") as source, archive.open(info, "w", force_zip64=True) as sink:
                    while block := source.read(1024 * 1024):
                        sink.write(block)
                        hasher.update(block)
                        done += len(block)
                        if progress:
                            progress(done, total, path.name)
                stat = path.stat()
                if stat.st_size != size or stat.st_mtime_ns != mtime:
                    raise ValueError(f"备份时文件发生变化，请等待 Steam 下载结束再重试：{path}")
                records.append({"path": target, "size": size, "mtime_ns": mtime, "sha256": hasher.hexdigest()})
            for game, spec in specs.items():
                for index, value in enumerate(spec.get("configs", [])):
                    path = Path(value)
                    payload = path.read_bytes()
                    target = f"configs/{game}/{index}-{path.name}"
                    archive.writestr(target, payload)
                    manifest["configs"].append({"source": str(path), "path": target, "sha256": hashlib.sha256(payload).hexdigest()})
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
            archive.writestr("恢复说明.txt", RESTORE_HELP.encode("utf-8-sig"))
        # Recheck the inventory: also notice newly added/deleted files during the snapshot.
        _, after = backup_plan(specs)
        if {(str(p), s, t) for p, _, s, t in files} != {(str(p), s, t) for p, _, s, t in after}:
            raise ValueError("备份期间模组目录有变化，请等 Steam 下载结束再重试")
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"files": len(files), "bytes": total, "packages": len(roots), "path": str(destination)}


def verify_bundle(path, progress=None):
    """Read every archived payload and config; validate SHA-256 as well as ZIP CRC."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("压缩包内存在重名文件")
        manifest = json.loads(archive.read("manifest.json"))
        records = manifest["files"] + manifest["configs"]
        total = sum(archive.getinfo(record["path"]).file_size for record in records)
        done = 0
        for record in records:
            checksum = hashlib.sha256()
            with archive.open(record["path"]) as source:
                while block := source.read(1024 * 1024):
                    checksum.update(block)
                    done += len(block)
                    if progress:
                        progress(done, total, record["path"])
            if checksum.hexdigest() != record["sha256"]:
                raise ValueError("文件哈希不符：" + record["path"])
        archive.read("恢复说明.txt")
    return {"checked": len(records), "bytes": total}
