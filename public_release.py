"""Prepare an allowlisted source tree and a clean portable ZIP; never read live data."""
import argparse
import ast
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parent
MODULES = '''web_app web_api web_jobs web_smoke manager_model core backup covers history
release_store steam_account steam_bridge steam_workshop task_log versions view window_layout
workshop_queue'''.split()
TESTS = '''test_backup test_core test_covers test_release_store test_runtime test_scan_cache
test_steam_account test_steam_workshop test_task_log test_versions test_web_manager
test_window_layout test_workshop_queue test_public_release'''.split()
SOURCE_FILES = [name + '.py' for name in MODULES + TESTS] + [
    'build_exe.py', 'public_release.py', 'README.md', 'DEVELOPMENT.md', 'LICENSE',
    'THIRD_PARTY_NOTICES.md', 'requirements.txt', 'requirements-build.txt', '.gitignore',
    'docs/RELEASE_NOTES.md', 'licenses/WebView2.LICENSE.txt', 'licenses/proxy-tools.LICENSE.txt',
    'frontend/index.html', 'frontend/styles.css', 'frontend/app.js',
    'assets/app-icon.png', 'assets/app-icon-small.png', 'assets/app-icon.ico', 'assets/ICON.md']


def version():
    tree = ast.parse((ROOT / 'web_app.py').read_text(encoding='utf-8'))
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'VERSION' for t in n.targets))


def copy_sources(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    for name in SOURCE_FILES:
        source = ROOT / name
        if not source.is_file() or source.is_symlink():
            raise ValueError('Missing or invalid public source: ' + name)
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return destination


def copy_licenses(bundle):
    target = Path(bundle) / 'licenses'
    target.mkdir(exist_ok=True)
    packages = ['pywebview', 'pythonnet', 'Pillow', 'clr-loader', 'cffi', 'pycparser',
                'proxy-tools', 'typing-extensions', 'bottle', 'packaging', 'setuptools', 'pyinstaller']
    lines = ['Third-party distributions used by this build (build tools may not be bundled):']
    for name in packages:
        dist = metadata.distribution(name)
        lines.append(f'{dist.metadata["Name"]} {dist.version}')
        for file in dist.files or []:
            if Path(str(file)).name.lower().startswith(('license', 'copying', 'notice')):
                path = Path(dist.locate_file(file))
                if path.is_file():
                    dest = target / name / str(file)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, dest)
    for name in ('WebView2.LICENSE.txt', 'proxy-tools.LICENSE.txt'):
        shutil.copy2(ROOT / 'licenses' / name, target / name)
    python_license = next((p for p in [Path(sys.base_prefix) / 'LICENSE.txt',
                                     Path(sys.base_prefix) / 'LICENSE_PYTHON.txt'] if p.is_file()), None)
    if python_license is None:
        raise FileNotFoundError('Python runtime license not found')
    shutil.copy2(python_license, target / 'Python.LICENSE.txt')
    (target / 'COMPONENTS.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def archive(folder, destination):
    folder, destination = Path(folder), Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    paths = sorted(folder.rglob('*'))
    for path in paths:
        relative = path.relative_to(folder)
        if path.is_symlink():
            raise ValueError('Symlinks not allowed in release')
        if any(p.lower() in {'data', 'credentials', 'exports', '__pycache__', '.git', 'webview-check'} for p in relative.parts) or relative.parts[0].lower() == 'webview':
            raise ValueError('Personal/generated data in release: ' + str(relative))
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path in paths:
            if path.is_file():
                relative = path.relative_to(folder)
                output.write(path, Path(folder.name) / relative)
    with zipfile.ZipFile(destination) as check:
        if check.testzip() is not None:
            raise ValueError('Release ZIP integrity check failed')
    with destination.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--licenses', type=Path)
    parser.add_argument('--output', type=Path, help='New output directory; existing directories are never overwritten')
    args = parser.parse_args()
    if args.licenses:
        copy_licenses(args.licenses)
        return
    bundle = ROOT / 'build/public-package/尖塔模组管理器'
    if not (bundle / '尖塔模组管理器.exe').is_file():
        raise FileNotFoundError('Run build_exe.py --public first')
    output = args.output or ROOT / 'build/public-release' / ('v' + version())
    output.mkdir(parents=True, exist_ok=False)
    source = copy_sources(output / 'spire-mod-manager')
    manifest = {}
    for folder, name in [(source, f'spire-mod-manager-{version()}-source.zip'),
                         (bundle, f'spire-mod-manager-{version()}-windows-x64.zip')]:
        manifest[name] = archive(folder, output / name)
    (output / 'SHA256SUMS.txt').write_text(''.join(f'{value}  {name}\n' for name, value in manifest.items()), encoding='utf-8')
    print(json.dumps({'output': str(output), 'sha256': manifest}, ensure_ascii=False))


if __name__ == '__main__':
    main()
