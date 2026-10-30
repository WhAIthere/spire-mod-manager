"""Isolated Windows x64 Steamworks bridge. No game or mod assemblies are executed.

ISteamUGC v021 ABI: Valve Steamworks SDK public/steam/isteamugc.h (v1.62).
Exact interface version is required; do not reuse these slots for another version.
"""
import ctypes as C
import json
import os
from pathlib import Path
import sys


class SteamClient:
    def __init__(self, game_dir, appid):
        if os.name != "nt" or C.sizeof(C.c_void_p) != 8:
            raise ValueError("Steam 工坊开关需要 Windows 64 位 Python。")
        self.appid = int(appid)
        os.environ["SteamAppId"] = str(appid)
        os.environ["SteamGameId"] = str(appid)
        folder = Path(game_dir).resolve() / "data_sts2_windows_x86_64"
        self.search_path = os.add_dll_directory(str(folder))
        self.dll = C.CDLL(str(folder / "steam_api64.dll"))
        self.dll.SteamAPI_InitFlat.argtypes = [C.c_void_p]
        self.dll.SteamAPI_InitFlat.restype = C.c_int
        error = C.create_string_buffer(1024)
        result = self.dll.SteamAPI_InitFlat(error)
        if result != 0:
            raise ValueError("无法连接 Steam，请确认 Steam 已登录且拥有塔二：" + error.value.decode("utf-8", "replace"))
        try:
            self.dll.SteamAPI_GetHSteamUser.restype = C.c_int
            self.dll.SteamInternal_FindOrCreateUserInterface.argtypes = [C.c_int, C.c_char_p]
            self.dll.SteamInternal_FindOrCreateUserInterface.restype = C.c_void_p
            self.ugc = self.dll.SteamInternal_FindOrCreateUserInterface(
                self.dll.SteamAPI_GetHSteamUser(), b"STEAMUGC_INTERFACE_VERSION021")
            if not self.ugc:
                raise ValueError("Steam 客户端不支持工坊开关接口 v021，请更新 Steam。")
            self.dll.SteamAPI_SteamUser_v023.restype = C.c_void_p
            self.dll.SteamAPI_ISteamUser_GetSteamID.argtypes = [C.c_void_p]
            self.dll.SteamAPI_ISteamUser_GetSteamID.restype = C.c_uint64
            self.account = str(self.dll.SteamAPI_ISteamUser_GetSteamID(self.dll.SteamAPI_SteamUser_v023()))
            self.dll.SteamAPI_SteamUtils_v010.restype = C.c_void_p
            self.dll.SteamAPI_ISteamUtils_GetAppID.argtypes = [C.c_void_p]
            self.dll.SteamAPI_ISteamUtils_GetAppID.restype = C.c_uint32
            actual = self.dll.SteamAPI_ISteamUtils_GetAppID(self.dll.SteamAPI_SteamUtils_v010())
            if actual != self.appid:
                raise ValueError(f"Steam 应用号不匹配：{actual}，停止操作。")
        except Exception:
            self.close()
            raise

    def close(self):
        self.dll.SteamAPI_Shutdown()

    def call(self, slot, result, *types):
        vtable = C.cast(self.ugc, C.POINTER(C.POINTER(C.c_void_p))).contents
        # Windows x64 has a unified calling convention, including the explicit this pointer.
        function = C.CFUNCTYPE(result, C.c_void_p, *types)(vtable[slot])
        return lambda *args: function(self.ugc, *args)

    def snapshot(self):
        count = self.call(74, C.c_uint32, C.c_bool)(True)
        if count > 100000:
            raise ValueError("Steam 返回的订阅数量异常。")
        ids = (C.c_uint64 * max(1, count))()
        found = self.call(75, C.c_uint32, C.POINTER(C.c_uint64), C.c_uint32, C.c_bool)(ids, count, True)
        active = (C.c_uint64 * max(1, count))()
        active_count = self.call(75, C.c_uint32, C.POINTER(C.c_uint64), C.c_uint32, C.c_bool)(active, count, False)
        if found != count or active_count > count:
            raise ValueError("Steam 订阅列表正在变化，请重试。")
        active_ids = {str(x) for x in active[:active_count]}
        state = self.call(76, C.c_uint32, C.c_uint64)
        install = self.call(77, C.c_bool, C.c_uint64, C.POINTER(C.c_uint64), C.c_void_p,
                            C.c_uint32, C.POINTER(C.c_uint32))
        items = {}
        for ident in ids[:found]:
            size, timestamp, path = C.c_uint64(), C.c_uint32(), C.create_string_buffer(32768)
            installed = bool(install(ident, C.byref(size), path, len(path), C.byref(timestamp)))
            items[str(ident)] = {"disabled": str(ident) not in active_ids, "state": state(ident),
                                 "installed": installed, "path": path.value.decode("utf-8", "strict") if installed else ""}
        if len(items) != count:
            raise ValueError("Steam 返回了重复的订阅条目。")
        return {"appid": str(self.appid), "account": self.account, "items": items}

    def set_disabled(self, ids, disabled):
        if not ids:
            return
        values = (C.c_uint64 * len(ids))(*(int(x) for x in ids))
        ok = self.call(94, C.c_bool, C.POINTER(C.c_uint64), C.c_uint32, C.c_bool)(values, len(ids), disabled)
        if not ok:
            # Some clients return false for an idempotent request. Trust the state,
            # never the return value alone; also matters during partial rollback.
            items = self.snapshot()["items"]
            if all(key in items and items[key]["disabled"] == disabled for key in ids):
                return
            raise ValueError("Steam 拒绝更新工坊本地开关。")


def same_snapshot(left, right):
    """Compare account, subscriptions, switches and paths; download flags may change."""
    def stable(value):
        return {"appid": value["appid"], "account": value["account"], "items": {
            key: {k: item[k] for k in ("disabled", "installed", "path")}
            for key, item in value["items"].items()}}
    return stable(left) == stable(right)


def apply_switches(client, expected, changes):
    before = client.snapshot()
    if not same_snapshot(before, expected):
        raise ValueError("Steam 账号、工坊订阅或开关已变化，请重新扫描并预览。")
    if any(key not in before["items"] or type(value) is not bool for key, value in changes.items()):
        raise ValueError("工坊开关计划包含未订阅条目或无效状态。")
    changed = {key: value for key, value in changes.items() if before["items"][key]["disabled"] != value}
    try:
        # Disable unwanted copies before enabling replacements.
        for disabled in (True, False):
            client.set_disabled([key for key, value in changed.items() if value == disabled], disabled)
        after = client.snapshot()
        wanted = json.loads(json.dumps(before))
        for key, disabled in changed.items():
            wanted["items"][key]["disabled"] = disabled
        if not same_snapshot(after, wanted):
            raise ValueError("Steam 工坊开关回读与计划不一致。")
        return after
    except Exception as exc:
        try:
            current = client.snapshot()
            if current["account"] != before["account"]:
                raise ValueError("Steam 账号已变化")
            for disabled in (True, False):
                restore = [key for key in changed if key in current["items"] and
                           before["items"][key]["disabled"] == disabled]
                client.set_disabled(restore, disabled)
            rolled_back = client.snapshot()
            if not same_snapshot(rolled_back, before):
                raise ValueError("恢复后的工坊状态与备份不一致")
        except Exception as recovery:
            raise ValueError(f"{exc}；工坊开关恢复失败：{recovery}。请依据应用记录核对 Steam。") from exc
        raise ValueError(f"{exc}；本次工坊开关已恢复。") from exc


def main(request=None):
    if request is None:
        request = json.loads(sys.stdin.read())
    if str(request["appid"]) != "2868840":
        raise ValueError("此桥接仅用于塔二。")
    client = SteamClient(request["game_dir"], request["appid"])
    try:
        command = request.get("command", "snapshot")
        if command == "snapshot":
            return client.snapshot()
        if command == "apply":
            return apply_switches(client, request["expected"], request["changes"])
        raise ValueError("未知 Steam 工坊操作。")
    finally:
        client.close()


def file_request(input_path, output_path):
    """File IPC also works in a windowed EXE where standard streams are absent."""
    try:
        request = json.loads(Path(input_path).read_text(encoding="utf-8"))
        result = {"ok": True, "result": main(request)}
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    Path(output_path).write_text(json.dumps(result, ensure_ascii=True), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--request-file":
        file_request(sys.argv[2], sys.argv[3])
    else:
        try:
            result = {"ok": True, "result": main()}
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
        print("SPIRE_STEAM_RESULT=" + json.dumps(result, ensure_ascii=True), flush=True)
