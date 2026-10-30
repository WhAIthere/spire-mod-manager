"""WebView2 desktop entry point. HTML paints; Python owns the existing local mod logic."""
import json
import os
from pathlib import Path
import sys
import threading
import traceback

VERSION = "1.9.2"
ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
ASSETS = Path(getattr(sys, "_MEIPASS", ROOT)) / "frontend"
DATA = Path(os.environ.get("SPIRE_MANAGER_DATA", ROOT / "data"))
if not getattr(sys, "frozen", False) and "SPIRE_MANAGER_DATA" not in os.environ:
    current = ROOT / "dist/尖塔模组管理器/data"
    if (current / "library.json").exists():
        DATA = current


def page():
    html = (ASSETS / "index.html").read_text(encoding="utf-8")
    css = (ASSETS / "styles.css").read_text(encoding="utf-8")
    js = (ASSETS / "app.js").read_text(encoding="utf-8")
    return html.replace('<link rel="stylesheet" href="styles.css">', "<style>" + css + "</style>").replace(
        '<script src="app.js"></script>', "<script>" + js + "</script>")


def launch(model=None, check_output=None, quick=False, first_run=False):
    import webview
    from manager_model import Manager
    from web_api import Api
    from window_layout import initial_geometry
    model = model or Manager(DATA)
    api = Api(model)
    screen = webview.screens[0]
    width, height, x, y = initial_geometry((screen.x, screen.y, screen.x + screen.width, screen.y + screen.height - 32))
    window = webview.create_window(f"尖塔模组管理器 · {VERSION}", html=page(), js_api=api,
                                   width=width, height=height, x=x, y=y, min_size=(min(860, width), min(490, height)),
                                   background_color="#f3f6f8")
    api._window = window
    def closing():
        if api._allow_close:
            return
        def ask():
            try:
                window.evaluate_js("window.requestClose && window.requestClose()")
            except Exception:
                if not model.dirty_games() and not api._jobs.value["running"]:
                    api._allow_close = True
                    window.destroy()
        threading.Thread(target=ask, daemon=True).start()
        return False
    window.events.closing += closing
    if check_output:
        from web_smoke import check, quick_check, first_run_check
        check = first_run_check if first_run else quick_check if quick else check
        webview.start(check, (window, api, check_output), gui="edgechromium", private_mode=True,
                      storage_path=str(model.directory / "webview-check"))
    else:
        webview.start(gui="edgechromium", private_mode=False, storage_path=str(model.directory / "webview"))


def main():
    # The frozen executable also hosts the isolated native Steam helper. Dispatch before
    # importing pywebview/.NET or creating any window in that subprocess.
    if len(sys.argv) == 4 and sys.argv[1] == "--steam-bridge":
        from steam_bridge import file_request
        file_request(sys.argv[2], sys.argv[3])
        return
    if len(sys.argv) == 3 and sys.argv[1] in ("--smoke-test", "--quick-smoke", "--first-run-smoke"):
        import tempfile
        import shutil
        from manager_model import Manager
        with tempfile.TemporaryDirectory(prefix="spire-web-check-") as temp:
            test_data = Path(temp) / "data"
            if sys.argv[1] == '--first-run-smoke':
                catalog = {game: {'game_dir': '', 'configs': [], 'workshop_roots': []} for game in ('sts1', 'sts2')}
                launch(Manager(test_data, catalog=catalog), sys.argv[2], first_run=True)
                return
            if DATA.exists():
                shutil.copytree(DATA, test_data, ignore=shutil.ignore_patterns("webview", "webview-check", "credentials", "versions"))
            launch(Manager(test_data), sys.argv[2], quick=sys.argv[1] == "--quick-smoke")
        return
    launch()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        details = traceback.format_exc()
        try:
            DATA.mkdir(parents=True, exist_ok=True)
            (DATA / "web-startup-error.log").write_text(details, encoding="utf-8")
        finally:
            if os.name == "nt":
                import ctypes
                ctypes.windll.user32.MessageBoxW(None, "WebView2 界面无法启动。请确认已安装 Microsoft Edge WebView2 Runtime。\n\n" +
                                                str(DATA / "web-startup-error.log"), "尖塔模组管理器", 0x10)
            elif sys.stderr:
                sys.stderr.write(details)
        raise
