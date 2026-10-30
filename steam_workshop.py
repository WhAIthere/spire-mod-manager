"""Plan STS2 file choices and commit Steam-directory switches with game settings."""
import copy
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

from steam_bridge import same_snapshot


def bridge(game_dir, command="snapshot", **options):
    request = {"game_dir": str(game_dir), "appid": "2868840", "command": command, **options}
    with tempfile.TemporaryDirectory(prefix="spire-steam-") as temp:
        incoming, outgoing = Path(temp) / "request.json", Path(temp) / "response.json"
        incoming.write_text(json.dumps(request), encoding="utf-8")
        if getattr(sys, "frozen", False):
            command_line = [sys.executable, "--steam-bridge", str(incoming), str(outgoing)]
        else:
            command_line = [sys.executable, "-X", "utf8", str(Path(__file__).with_name("steam_bridge.py")),
                            "--request-file", str(incoming), str(outgoing)]
        process = subprocess.run(command_line, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, timeout=40,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if process.returncode or not outgoing.exists():
            raise ValueError("Steam 工坊桥接未能完成，请确认 Steam 已登录、游戏目录正确且客户端已更新。")
        response = json.loads(outgoing.read_text(encoding="utf-8"))
    if not response["ok"]:
        raise ValueError(response["error"])
    return response["result"]


def expected_account(config_path):
    path = Path(config_path)
    if path.parent.parent.name != "steam" or not path.parent.name.isdecimal():
        raise ValueError("同步工坊开关需要选择 steam/<SteamID>/settings.save，不能使用默认或改名的账号目录。")
    return path.parent.name


def check_account(snapshot, config_path):
    if snapshot["appid"] != "2868840" or snapshot["account"] != expected_account(config_path):
        raise ValueError("当前 Steam 登录账号与选定的塔二配置账号不一致，请切换账号或配置文件。")


def bind_snapshot(mods, snapshot):
    """Intersect game ID switches with Steam's per-item availability on scan only."""
    for mod in mods:
        if mod.game != "sts2" or mod.source != "steam_workshop":
            continue
        item = snapshot["items"].get(mod.workshop_id)
        mod.workshop_disabled = item["disabled"] if item else None
        if not item or item["disabled"]:
            mod.enabled = False
        mod.loader_status = ("Steam 工坊目录已关闭；开启此模组时自动打开" if item and item["disabled"] else
                             "Steam 工坊目录已开启" if item else "当前账号未订阅此工坊条目；不能自动开启")


def selection(mods):
    return sorted((m.key, m.enabled, str(m.path.resolve()), m.id, m.source, m.workshop_id,
                   m.version) for m in mods)


def manifest_hashes(mods):
    from core import digest
    return {str(m.path.resolve()): digest(m.path) for m in mods}


def build_plan(mods, snapshot, config_path):
    """All manifests in an opened Workshop directory participate in ID switch resolution."""
    check_account(snapshot, config_path)
    folders = {}
    enabled_keys = {(m.source, m.id) for m in mods if m.enabled}
    for mod in mods:
        if mod.game != "sts2":
            raise ValueError("工坊目录同步只支持塔二。")
        if mod.source == "steam_workshop":
            folders.setdefault(mod.workshop_id, []).append(mod)
    # A custom scan-root list must not omit active Steam manifests from conflict checks.
    known_paths = {str(m.path.resolve()).casefold() for m in mods}
    for ident, item in snapshot["items"].items():
        if item["disabled"] or not item["installed"] or ident in folders:
            continue
        directory = Path(item["path"])
        if not directory.is_dir():
            raise ValueError(f"Steam 工坊 {ident} 的安装目录不可读，请重新扫描。")
        for path in directory.rglob("*.json"):
            if path.stat().st_size > 2_000_000:
                continue
            try:
                manifest = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as exc:
                raise ValueError(f"无法核对未扫描工坊 {ident} 的 JSON：{path.name}：{exc}") from exc
            if isinstance(manifest, dict) and manifest.get("id") and str(path.resolve()).casefold() not in known_paths:
                raise ValueError(f"Steam 工坊 {ident} 仍开启但未纳入扫描，请添加对应工坊根目录后重新扫描。")
    desired = {}
    for ident, members in folders.items():
        opened = any(m.enabled for m in members)
        item = snapshot["items"].get(ident)
        if not item:
            if opened:
                raise ValueError(f"工坊 {ident} 未被当前账号订阅，请先在 Steam 中订阅后重新扫描。")
            continue
        if opened:
            if not item["installed"] or item["state"] & (8 | 16 | 32):
                raise ValueError(f"工坊 {ident} 尚未就绪或正在更新，请等待 Steam 完成后重新扫描。")
            install = str(Path(item["path"]).resolve()).casefold()
            if any(str(Path(m.package_key).resolve()).casefold() != install for m in members):
                raise ValueError(f"工坊 {ident} 的扫描目录与 Steam 实际安装目录不同，请重新扫描。")
        desired[ident] = not opened

    # An unselected manifest with the same (source, ID) still becomes enabled if its folder
    # is opened for another selected mod. Check this closure, not just checked rows.
    effective = [m for m in mods if (m.source, m.id) in enabled_keys and
                 (m.source != "steam_workshop" or desired.get(m.workshop_id) is False)]
    by_id = {}
    for mod in effective:
        by_id.setdefault(mod.id, []).append(mod)
    conflicts = [f"{ident}：" + "；".join(f"{m.workshop_id or '本地'} / {m.path.name}" for m in entries)
                 for ident, entries in by_id.items() if len(entries) > 1]
    if conflicts:
        raise ValueError("选中的文件和所需工坊目录会使以下同 ID 模组同时生效：\n" + "\n".join(conflicts) +
                         "\n请只保留一个版本。同一目录内的同 ID 副本无法通过游戏的 ID 开关分别选择。")
    if {m.key for m in effective} != {m.key for m in mods if m.enabled}:
        raise ValueError("当前选择不能由游戏的 ID 开关完整表达，请核对同 ID 副本。")
    return {"before": copy.deepcopy(snapshot), "desired": desired,
            "labels": {ident: members[0].folder_label for ident, members in folders.items()}, "selection": selection(mods),
            "manifests": manifest_hashes(mods)}


def validate_plan(plan, mods):
    if plan["selection"] != selection(mods):
        raise ValueError("模组选择已变化，请重新预览。")
    if plan["manifests"] != manifest_hashes(mods):
        raise ValueError("模组文件已变化，请重新扫描后预览。")


def verify_inventory(mods, game_dir, workshop_roots):
    from core import scan
    fresh, issues = scan("sts2", game_dir, workshop_roots)
    if issues or manifest_hashes(fresh) != manifest_hashes(mods):
        raise ValueError("本地模组目录或清单已变化，请重新扫描。")


def preview_lines(plan):
    lines = ["", "自动同步 Steam 工坊目录（不改变订阅）："]
    changed = 0
    for ident, disabled in plan["desired"].items():
        if plan["before"]["items"][ident]["disabled"] != disabled:
            changed += 1
            lines.append(f"  {'关闭' if disabled else '打开'} {plan['labels'].get(ident, '工坊 ' + ident)}")
    if not changed:
        lines.append("  工坊目录开关无需变化。")
    lines.append("同一工坊目录的多个模组共享目录开关，游戏内的模组 ID 开关仍分别写入。")
    return lines


def restore_bytes(path, data):
    fd, temp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def commit_plan(session, data, plan, mods, game_dir, workshop_roots, backup_dir, api=bridge):
    from core import atomic_json, digest
    validate_plan(plan, mods)
    verify_inventory(mods, game_dir, workshop_roots)
    now = api(game_dir)
    check_account(now, session.path)
    if not same_snapshot(now, plan["before"]):
        raise ValueError("Steam 工坊开关、订阅或安装目录已变化，请重新扫描后预览。")
    # Re-evaluate download state and conflict closure at the last moment.
    build_plan(mods, now, session.path)
    if digest(session.path) != session.original_hash:
        raise ValueError("游戏配置已变化，请重新扫描。")
    backup_dir = Path(backup_dir)
    receipt = backup_dir / ("sts2-workshop-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8] + ".json")
    record = {"phase": "prepared", "config_path": str(session.path), "before": now,
              "desired": plan["desired"], "game_dir": str(game_dir)}
    atomic_json(receipt, record)
    original_data, original_hash = copy.deepcopy(session.data), session.original_hash
    backup = session.write(data, backup_dir)
    written_hash = session.original_hash
    record.update(phase="config_written", config_backup=str(backup))
    try:
        atomic_json(receipt, record)
        after = api(game_dir, "apply", expected=now, changes=plan["desired"])
    except Exception as exc:
        record.update(phase="failed", error=str(exc))
        if digest(session.path) == written_hash:
            restore_bytes(session.path, backup.read_bytes())
            session.data, session.original_hash = original_data, original_hash
            record["config_restored"] = True
        else:
            record["config_restored"] = False
        atomic_json(receipt, record)
        raise ValueError(f"{exc}\n游戏配置{'已恢复' if record['config_restored'] else '已被其他程序更改，未覆盖'}。\n应用记录：{receipt}") from exc
    record.update(phase="completed", after=after)
    try:
        atomic_json(receipt, record)
    except OSError:
        # Both real writes succeeded; do not pretend they failed or roll back just the config.
        pass
    return backup, receipt, after
