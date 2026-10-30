"""Presentation logic shared by the desktop view and its tests; never changes game state."""
import re
from core import filter_mods

MODES = ("不分组", "文件夹分组", "自定义分组", "标签分组")


def natural(value):
    return tuple((1, int(piece)) if piece.isdecimal() else (0, piece.casefold())
                 for piece in re.split(r"(\d+)", str(value)) if piece)


def sort_mods(mods, column="file", descending=False, inventory=None):
    counts = {}
    for mod in mods if inventory is None else inventory:
        counts.setdefault(mod.id, []).append(mod)
    def value(mod):
        fields = {"file": mod.path.name, "nickname": mod.nickname, "name": mod.name,
                  "version": mod.version, "id": mod.id, "group": mod.group,
                  "tags": ", ".join(mod.tags), "folder": mod.folder_label,
                  "source": mod.source, "loader": mod.loader_status,
                  "enabled": int(mod.enabled), "copies": len(counts[mod.id])}
        return natural(fields.get(column, mod.path.name)), natural(mod.id), natural(mod.path.name), natural(mod.version), mod.key
    return sorted(mods, key=value, reverse=descending)


def view_sections(mods, *, mode="不分组", query="", group=None, hide_unavailable=True,
                  sort_column="file", descending=False, group_descending=False, group_order=None):
    # Unresolved duplicates need inspection; only confirmed shadowed copies may disappear.
    eligible = [m for m in mods if not hide_unavailable or not m.superseded or m.enabled]
    visible = sort_mods(filter_mods(eligible, query, group), sort_column, descending, inventory=mods)
    if mode == "不分组":
        return visible, []
    sections = {}
    def memberships(mod):
        if mode == "文件夹分组":
            return [(mod.package_key, mod.folder_label)]
        if mode == "标签分组":
            return [("tag:" + tag, tag) for tag in mod.tags] or [("untagged:", "未添加标签")]
        return [(mod.group, mod.group or "未分组")]
    for mod in eligible:
        for key, label in memberships(mod):
            sections.setdefault(key, {"key": key, "label": label, "members": [], "shown": []})["members"].append(mod)
    for mod in visible:
        for key, _ in memberships(mod):
            sections[key]["shown"].append(mod)
    # Parent names and child columns are independent sorting levels.
    shown_sections = [s for s in sections.values() if s["shown"]]
    shown_sections.sort(key=lambda s: (natural(s["label"]), natural(s["key"])), reverse=group_descending)
    if group_order:
        ranks = {key: i for i, key in enumerate(group_order)}
        shown_sections.sort(key=lambda section: ranks.get(section["key"], len(ranks)))
    return visible, shown_sections
