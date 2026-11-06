"""Portable per-package ZIP snapshots with readable Workshop-number directories."""
import hashlib
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import zipfile
from datetime import datetime
from core import atomic_json, read_json


def safe_relative(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name:
        raise ValueError('快照内路径不安全')
    return p


class VersionStore:
    def __init__(self, directory):
        self.root = Path(directory) / 'versions'
        self.index_path = self.root / 'index.json'
        self.index = read_json(self.index_path) if self.index_path.exists() else {'schema': 2, 'packages': {}, 'inventory': {}, 'imports': []}

    def blob(self, digest):
        if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('无效文件校验值')
        return self.root / 'objects' / digest[:2] / digest

    def put(self, stream, expected=None):
        self.root.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=self.root, suffix='.partial')
        h = hashlib.sha256()
        try:
            with os.fdopen(fd, 'wb') as out:
                while block := stream.read(1024*1024):
                    h.update(block); out.write(block)
            digest = h.hexdigest()
            if expected and expected != digest:
                raise ValueError('备份文件校验失败')
            target = self.blob(digest); target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists(): os.replace(name, target)
            return digest
        finally:
            if os.path.exists(name): os.unlink(name)

    def save(self): atomic_json(self.index_path, self.index)

    def add(self, game, source, files, mods, origin='scan'):
        identity = game + ':' + str(Path(source).resolve()).casefold()
        digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
        package = self.index['packages'].setdefault(identity, {'game': game, 'source': str(source), 'versions': []})
        if not any(v['digest'] == digest for v in package['versions']):
            package['versions'].append({'digest': digest, 'created': datetime.now().isoformat(timespec='seconds'),
                                        'origin': origin, 'files': files, 'mods': mods})
        return identity, digest

    def archive_path(self, package, version):
        rel = version.get('archive')
        if not rel:
            return None
        safe_relative(rel)
        path = self.root.joinpath(*PurePosixPath(rel).parts)
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError('备份路径超出历史库')
        return path

    def _archive_name(self, package, version):
        def clean(value):
            return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value)).strip(' .')[:72] or 'unknown'
        records = version['mods']
        ids = {str(m.get('workshop_id', '')) for m in records if m.get('workshop_id')}
        source = Path(package['source'])
        if not ids and source.name.isdigit() and source.parent.parent.name.casefold() == 'content':
            ids = {source.name}
        folder = next(iter(ids)) if len(ids) == 1 and next(iter(ids)).isdigit() else Path(package['source']).name
        if not ids:
            folder = 'local-' + clean(folder)
        versions = list(dict.fromkeys(str(m.get('version') or '未声明') for m in records))
        label = clean('+'.join(versions) or '未声明')
        stamp = clean(version['created'].replace(':', '-'))
        return f"{package['game']}/{clean(folder)}/{stamp}_{label}_{version['digest'][:12]}.zip"

    @contextmanager
    def open_version(self, package, version):
        if version.get('record_only') or not version['files']:
            raise ValueError('这个版本仅有记录，没有备份文件，无法恢复。')
        archive = self.archive_path(package, version)
        if archive and archive.is_file():
            with zipfile.ZipFile(archive) as z:
                yield lambda rel: z.open(rel)
        elif version.get('export'):
            from release_store import open_export
            with open_export(self, version) as opener:
                yield opener
        else:
            yield lambda rel: self.blob(version['files'][rel]).open('rb')

    def _verify_archive(self, path, files):
        with zipfile.ZipFile(path) as z:
            if len(z.namelist()) != len(files) or set(z.namelist()) != set(files):
                raise ValueError('备份包文件列表不一致')
            for rel, digest in files.items():
                safe_relative(rel)
                with z.open(rel) as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                        raise ValueError('历史文件校验失败：' + rel)

    def _write_archive(self, package, version, opener):
        relative = self._archive_name(package, version)
        target = self.root.joinpath(*PurePosixPath(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            fd, name = tempfile.mkstemp(dir=target.parent, suffix='.partial')
            os.close(fd)
            try:
                with zipfile.ZipFile(name, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
                    for rel, digest in version['files'].items():
                        safe_relative(rel)
                        h = hashlib.sha256()
                        with opener(rel) as source, z.open(rel, 'w', force_zip64=True) as out:
                            while block := source.read(1024*1024):
                                h.update(block)
                                out.write(block)
                        if h.hexdigest() != digest:
                            raise ValueError('备份源文件变化或校验失败：' + rel)
                self._verify_archive(name, version['files'])
                os.replace(name, target)
            finally:
                if os.path.exists(name): os.unlink(name)
        else:
            self._verify_archive(target, version['files'])
        version['archive'] = relative
        return target

    def migrate(self, progress=lambda x: None):
        """Resumable migration; only remove legacy objects after every version verifies."""
        original = self.root / 'index-before-zip.json'
        if self.index_path.exists() and not original.exists():
            shutil.copyfile(self.index_path, original)
        versions = [(p, v) for p in self.index['packages'].values() for v in p['versions'] if not v.get('record_only')]
        for n, (package, version) in enumerate(versions, 1):
            with self.open_version(package, version) as opener:
                path = self._write_archive(package, version, opener)
            self.index['schema'] = 2
            self.save()
            progress(f'[{n}/{len(versions)}] 已校验 {path.relative_to(self.root)}')
        # All references now resolve to verified independent ZIPs. Reject links before cleanup.
        objects = self.root / 'objects'
        if objects.exists():
            resolved = objects.resolve()
            if objects.is_symlink() or resolved != self.root.resolve() / 'objects':
                raise ValueError('旧对象库路径异常，保留文件')
            for p in objects.rglob('*'):
                if p.is_symlink() or not p.resolve().is_relative_to(resolved):
                    raise ValueError('旧对象库包含链接，保留文件')
            shutil.rmtree(objects)
        self.index['schema'] = 2
        self.save()
        (self.root / '备份说明.txt').write_text(
            '这里是普通 ZIP 模组备份，无加密，无需本管理器也可解压。\n'
            '目录：sts1 或 sts2 / 创意工坊编号（本地包为 local-名称）/ 时间_版本_校验前缀.zip。\n'
            'ZIP 根目录直接保留原包内的文件名与目录结构，不添加其他文件。\n'
            '同一编号下保留多个历史版本。整个包一起恢复，适配信息以模组清单声明为准。\n'
            'index.json 用于管理器恢复及完整性校验，请保留；index-before-zip.json 只是迁移前元数据。\n'
            '手动使用：解压到单独目录后按游戏加载器要求放入 mods；避免与已订阅的同 ID 模组同时启用。\n'
            'Steam Key 单独保存在 credentials 中并由 Windows DPAPI 加密，不在这些 ZIP 中。\n', encoding='utf-8-sig')
        return len(versions)

    @staticmethod
    def observed_files(root):
        files = []
        for path in sorted(root.rglob('*')):
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError('模组目录含链接，未核对历史版本')
            if path.is_file():
                files.append((path, path.stat()))
        return files

    @staticmethod
    def observation(records, root=None, observed=None):
        values = []
        for m in records:
            p = Path(m['path'])
            stat = p.stat()
            values.append([m['id'], m.get('version', ''), str(p), stat.st_size, stat.st_mtime_ns])
        # Assets can change without the manifest/JAR changing. Include the entire package.
        tree = []
        if root is not None:
            root = Path(root).resolve()
            for p, stat in (VersionStore.observed_files(root) if observed is None else observed):
                tree.append([p.relative_to(root).as_posix(), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns])
        return hashlib.sha256(json.dumps([sorted(values),tree], ensure_ascii=False).encode()).hexdigest()

    def observe(self, game, mods):
        """Remember encountered versions without claiming to possess their bytes."""
        packages = {}
        for m in mods:
            if not m.missing and m.path.is_file():
                packages.setdefault(m.package_key, []).append(m.record())
        changed = False
        for source, records in packages.items():
            identity = game + ':' + str(Path(source).resolve()).casefold()
            package = self.index['packages'].setdefault(identity, {'game': game, 'source': source, 'versions': []})
            root = Path(source).resolve()
            stamp = self.observation(records, root)
            if any(v.get('observation') == stamp for v in package['versions']): continue
            # Older indexes have content hashes but no observation fingerprint. Compare
            # bytes before inventing a second version with the same author version number.
            if any(not v.get('record_only') for v in package['versions']):
                files = {}
                for p in sorted(root.rglob('*')):
                    if p.is_file():
                        with p.open('rb') as stream:
                            files[p.relative_to(root).as_posix()] = hashlib.file_digest(stream,'sha256').hexdigest()
                if self.observation(records,root) != stamp: raise ValueError('核对期间模组文件变化，请稍后更新列表')
                digest = hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()
                known = next((v for v in package['versions'] if not v.get('record_only') and v['digest'] == digest), None)
                if known:
                    known.update(observation=stamp,mods=records,last_seen=datetime.now().isoformat(timespec='seconds'))
                    package['current_digest'] = digest
                    package['versions'] = [v for v in package['versions'] if not (v.get('record_only') and v.get('observation') in (stamp, self.observation(records)))]
                    changed = True
                    continue
            package['versions'].append({'digest': stamp, 'observation': stamp, 'record_only': True,
                'created': datetime.now().isoformat(timespec='seconds'), 'origin': 'observation', 'files': {}, 'mods': records})
            package['current_digest'] = stamp
            changed = True
        if changed: self.save()

    def capture(self, game, mods, progress=lambda x:None, stop=None, force=False, update_inventory=True):
        mods=[m for m in mods if not getattr(m,"missing",False)]
        packages = {}
        for m in mods: packages.setdefault(m.package_key, []).append(m.record())
        inventory = {}; previous = self.index['inventory'].get(game, {})
        for source, records in packages.items():
            if stop and stop.is_set(): break
            root = Path(source).resolve(); files = {}
            if not root.is_dir() or any(not Path(m['path']).is_file() for m in records):
                raise ValueError('扫描后模组文件消失，请等 Steam 下载完成后重试')
            before = self.observed_files(root)
            stamp = self.observation(records, root, before)
            identity = game + ':' + str(root).casefold()
            package = self.index['packages'].get(identity, {})
            cached = next((v for v in package.get('versions', []) if v.get('observation') == stamp and v['files']), None)
            if cached and not force:
                archive = self.archive_path(package, cached)
                if cached.get('export') or (archive and archive.is_file()):
                    inventory[identity] = cached['digest']
                    package['current_digest'] = cached['digest']
                    continue
            for p, stat in before:
                rel = p.relative_to(root).as_posix(); safe_relative(rel)
                with p.open('rb') as stream:
                    files[rel] = hashlib.file_digest(stream, 'sha256').hexdigest()
            known = {v['digest'] for v in self.index['packages'].get(identity, {}).get('versions', [])}
            identity, digest = self.add(game, source, files, records)
            package = self.index['packages'][identity]
            version = next(v for v in package['versions'] if v['digest'] == digest)
            version['observation'] = stamp
            version.update(mods=records,last_seen=datetime.now().isoformat(timespec='seconds'))
            package['current_digest'] = digest
            try:
                if (force or not version.get('export')) and (not version.get('archive') or not self.archive_path(package, version).is_file()):
                    self._write_archive(package, version, lambda rel: root.joinpath(*PurePosixPath(rel).parts).open('rb'))
            except Exception:
                if digest not in known:
                    package['versions'].remove(version)
                raise
            if self.observation(records, root) != stamp:
                raise ValueError('快照期间目录变化，请重新扫描')
            package['versions'] = [v for v in package['versions'] if not (v.get('record_only') and v.get('observation') in (stamp, self.observation(records)))]
            inventory[identity] = digest; self.save()
            archive = self.archive_path(package, version)
            if version.get('export') and not (archive and archive.is_file()):
                progress(f'已核对 {records[0]["nickname"]} · 与已导出版本一致，不重复缓存（{version["export"]["part"]}）')
            else:
                progress(f'已保存 {records[0]["nickname"]} · {len(files)} 个文件')
        if stop and stop.is_set(): return {'stopped': True}
        result = {'added':len(inventory.keys()-previous.keys()),'updated':sum(k in previous and previous[k]!=v for k,v in inventory.items()),
                  'missing':len(previous.keys()-inventory.keys()),'packages':len(inventory)}
        if update_inventory: self.index['inventory'][game] = inventory
        self.save(); return result

    def versions(self, game, mod_id=None):
        result = []
        for identity,p in self.index['packages'].items():
            if p['game']!=game: continue
            for v in reversed(p['versions']):
                records=[m for m in v['mods'] if mod_id is None or m['id']==mod_id]
                archive = self.archive_path(p,v)
                local = bool(archive and archive.is_file()) or bool(v['files'] and not v.get('record_only') and all(self.blob(h).is_file() for h in v['files'].values()))
                available = local or bool(v.get('export') and Path(self.index.get('release_locations',{}).get(v['export']['part'],'')).is_file())
                if records: result.append({'package':identity,'digest':v['digest'],'created':v['created'],'source':p['source'],
                    'origin':v['origin'],'mods':records,'files':len(v['files']),'archive':str(archive or ''),'export':v.get('export'),
                    'backup_state':'local' if local else 'exported' if v.get('export') else 'recorded', 'available':available, 'record_only':v.get('record_only',False)})
                    
                if records:
                    result[-1].update(current_file=p.get('current_digest')==v['digest'],current_match=p.get('current_digest')==v['digest'] and not v.get('record_only'),last_seen=v.get('last_seen',''))
        from functools import cmp_to_key
        from core import compare_versions
        def order(a,b):
            if mod_id and a['mods'] and b['mods']:
                comparison=compare_versions(a['mods'][0].get('version',''),b['mods'][0].get('version',''))
                if comparison:return -comparison
            return (b['created']>a['created'])-(b['created']<a['created'])
        result = sorted(result, key=cmp_to_key(order))
        for row in result:
            peers = [v for v in result if v['package']==row['package'] and not v['record_only'] and
                     sorted((m['id'],m.get('version','')) for m in v['mods'])==sorted((m['id'],m.get('version','')) for m in row['mods'])]
            row['same_version_variants'] = len(peers)
            if not row['record_only'] and len(peers)>1:
                pos = peers.index(row)
                other = peers[pos+1] if pos+1<len(peers) else peers[pos-1]
                package = self.index['packages'][row['package']]
                a = next(v['files'] for v in package['versions'] if v['digest']==row['digest'])
                b = next(v['files'] for v in package['versions'] if v['digest']==other['digest'])
                row['file_changes'] = [{'file':name,'kind':'新增' if name not in b else '移除' if name not in a else '修改'}
                                       for name in sorted(a.keys()|b.keys()) if a.get(name)!=b.get(name)]
        return result

    def import_bundle(self, path, progress=lambda x:None):
        token = str(Path(path).resolve())
        if token in self.index['imports']: return
        with zipfile.ZipFile(path) as z:
            manifest=json.loads(z.read('manifest.json'))
            for root in manifest['roots']:
                prefix=root['archive']+'/'; files={}
                for record in manifest['files']:
                    if not record['path'].startswith(prefix): continue
                    rel=record['path'][len(prefix):]; safe_relative(rel)
                    files[rel]=record['sha256']
                catalog=manifest.get('catalogs',{}).get(root['game'],{})
                mods=catalog.get('mods',[]) if isinstance(catalog,dict) else catalog
                records=[m for m in mods if str(Path(m['package_root']).resolve()).casefold()==str(Path(root['source']).resolve()).casefold()]
                if files:
                    identity, digest = self.add(root['game'],root['source'],files,records,'import')
                    package = self.index['packages'][identity]
                    version = next(v for v in package['versions'] if v['digest'] == digest)
                    self._write_archive(package, version, lambda rel: z.open(prefix+rel))
                    self.save()
                progress(root['source'])
        self.index['imports'].append(token); self.save()

    def restore(self, identity, digest):
        package=self.index['packages'][identity]
        version=next(v for v in package['versions'] if v['digest']==digest)
        if package.get('external'):raise ValueError('导入的备份只支持取出到所选位置或安装到本机 mods，不写回旧电脑路径。')
        target=Path(package['source']).resolve()
        if not target.parent.is_dir() or target == target.parent or target.name in ('','steamapps','content'):
            raise ValueError('原目录不可用，请检查游戏路径')
        stage=Path(tempfile.mkdtemp(prefix='.spire-restore-',dir=target.parent))
        rollback=stage.with_name(stage.name+'-previous')
        try:
            with self.open_version(package, version) as opener:
                for rel,digest in version['files'].items():
                    safe_relative(rel); dest=stage.joinpath(*PurePosixPath(rel).parts); dest.parent.mkdir(parents=True,exist_ok=True)
                    h=hashlib.sha256()
                    with opener(rel) as stream, dest.open('wb') as out:
                        while block := stream.read(1024*1024):
                            h.update(block); out.write(block)
                    if h.hexdigest()!=digest: raise ValueError('历史文件校验失败')
            if target.exists(): target.rename(rollback)
            try: stage.rename(target)
            except Exception:
                if rollback.exists(): rollback.rename(target)
                raise
            # Keep displaced original for rollback, even if a scan was never captured.
            return str(rollback) if rollback.exists() else ''
        finally:
            if stage.exists(): shutil.rmtree(stage)

    def extract(self, identity, digest, destination, install_sts1=False, include=None):
        """Extract just one selected mod package, without replacing any existing file."""
        package=self.index['packages'][identity]
        version=next(v for v in package['versions'] if v['digest']==digest)
        files = version['files'] if include is None else {rel:version['files'][rel] for rel in include}
        if not files or version.get('record_only'): raise ValueError('这个版本没有备份文件可供取出。')
        destination=Path(destination).resolve();destination.mkdir(parents=True,exist_ok=True)
        stage=Path(tempfile.mkdtemp(prefix='.spire-extract-',dir=destination))
        try:
            with self.open_version(package,version) as opener:
                for rel,expected in files.items():
                    safe_relative(rel);out=stage.joinpath(*PurePosixPath(rel).parts);out.parent.mkdir(parents=True,exist_ok=True)
                    h=hashlib.sha256()
                    with opener(rel) as stream,out.open('wb') as target:
                        while block:=stream.read(1024*1024):h.update(block);target.write(block)
                    if h.hexdigest()!=expected:raise ValueError('模组文件校验失败：'+rel)
            if install_sts1:
                entries=list(stage.iterdir())
                if any((destination/p.name).exists() for p in entries):raise ValueError('mods 中存在同名文件或目录，未覆盖；请先取出到其他位置检查。')
                moved=[]
                try:
                    for entry in entries:
                        target=destination/entry.name
                        if target.exists():raise ValueError('目标已存在，未覆盖')
                        entry.rename(target);moved.append(target)
                except Exception:
                    for target in reversed(moved):target.rename(stage/target.name)
                    raise
                return str(destination)
            name=re.sub(r'[^\w.-]','_',Path(package['source']).name)[:60] or 'mod'
            target=destination/(name+'-'+digest[:12])
            if target.exists():raise ValueError('目标版本文件夹已存在，未覆盖：'+str(target))
            stage.rename(target)
            return str(target)
        finally:
            if stage.exists():shutil.rmtree(stage)
