"""Build an onedir/windowed release with an isolated PyInstaller environment."""
from pathlib import Path
import argparse
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
NAME = "尖塔模组管理器"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--public', action='store_true', help='Build a clean portable bundle; never copy or seed personal data')
    args = parser.parse_args()
    python = ROOT / ".build-venv/Scripts/python.exe"
    if not python.exists() and sys.prefix != sys.base_prefix:
        python = Path(sys.executable)
    if not python.exists():
        raise SystemExit("请先创建 .build-venv 并在其中安装 PyInstaller；见 README。")
    target = ROOT / ('build/public-package' if args.public else 'build/package')
    # --public may replace only this generated staging folder, never the live dist.
    public_bundle = target / NAME
    if args.public and public_bundle.exists():
        if public_bundle.resolve().parent != (ROOT / 'build/public-package').resolve() or public_bundle.is_symlink():
            raise RuntimeError('Unexpected public staging path')
        shutil.rmtree(public_bundle)
    subprocess.run([str(python), "-m", "PyInstaller", "--noconfirm", "--windowed", "--onedir",
                    "--name", NAME, "--distpath", str(target), "--workpath", str(ROOT / "build/cache"),
                    "--specpath", str(ROOT / "build"),
                    "--icon", str(ROOT / 'assets/app-icon.ico'),
                    "--add-data", str(ROOT / 'assets') + ';assets',
                    "--add-data", str(ROOT / "frontend") + ";frontend",
                    "--exclude-module", "tkinter", "--exclude-module", "_tkinter",
                    "--exclude-module", "PyQt5", "--exclude-module", "PyQt6",
                    "--exclude-module", "PySide2", "--exclude-module", "PySide6",
                    "--exclude-module", "cefpython3", "--exclude-module", "gi",
                    str(ROOT / "web_app.py")], cwd=ROOT, check=True)
    if args.public:
        # The public build targets Windows x64; the Android bridge is unused.
        android_bridge = public_bundle / '_internal/webview/lib/pywebview-android.jar'
        if android_bridge.is_file():
            android_bridge.unlink()
        shutil.copy2(ROOT / 'README.md', public_bundle / '使用说明.md')
        shutil.copy2(ROOT / 'LICENSE', public_bundle / 'LICENSE')
        shutil.copy2(ROOT / 'THIRD_PARTY_NOTICES.md', public_bundle / 'THIRD_PARTY_NOTICES.md')
        subprocess.run([str(python), str(ROOT / 'public_release.py'), '--licenses', str(public_bundle)], check=True)
        if (public_bundle / 'data').exists():
            raise RuntimeError('Public bundle unexpectedly contains data')
        print(public_bundle / (NAME + '.exe'))
        return
    bundle = ROOT / "dist" / NAME
    # Build into staging first: rebuilding must never delete a release's live data folder.
    shutil.copytree(ROOT / "build/package" / NAME, bundle, dirs_exist_ok=True)
    # Seed the first release, but preserve the EXE's own records on subsequent builds.
    data = bundle / "data"
    if not data.exists() and (ROOT / "data").exists():
        shutil.copytree(ROOT / "data", data)
    shutil.copy2(ROOT / "README.md", bundle / "使用说明.md")
    print(bundle / (NAME + ".exe"))


if __name__ == "__main__":
    main()
