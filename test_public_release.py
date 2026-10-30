import ast
from pathlib import Path
import tempfile
import unittest
import zipfile

from public_release import archive, copy_sources, MODULES, TESTS, ROOT


class PublicReleaseTests(unittest.TestCase):
    def test_source_is_self_contained_and_excludes_personal_files(self):
        # Resolve every project-local import, including imports inside functions.
        for module in MODULES + TESTS:
            for node in ast.walk(ast.parse((ROOT / (module + '.py')).read_text(encoding='utf-8'))):
                names = [n.name for n in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) else []
                for name in names:
                    name = (name or '').split('.')[0]
                    if (ROOT / (name + '.py')).exists():
                        self.assertIn(name, MODULES + TESTS + ['public_release'], (module, name))
        with tempfile.TemporaryDirectory() as temp:
            source = copy_sources(Path(temp) / 'source')
            self.assertFalse((source / 'data').exists())
            self.assertFalse((source / 'app.py').exists())
            self.assertTrue((source / 'LICENSE').is_file())
            archive(source, Path(temp) / 'source.zip')
            with zipfile.ZipFile(Path(temp) / 'source.zip') as package:
                self.assertIn('source/frontend/app.js', package.namelist())
                self.assertNotIn('source/dist', package.namelist())

    def test_zip_refuses_personal_data_even_if_accidentally_staged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'bundle'
            (root / 'data/credentials').mkdir(parents=True)
            (root / 'data/credentials/key.bin').write_bytes(b'private fixture')
            with self.assertRaisesRegex(ValueError, 'Personal/generated data'):
                archive(root, Path(temp) / 'bad.zip')

    def test_embedded_webview_component_is_not_confused_with_user_browser_storage(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'bundle'
            (root / '_internal/webview/js').mkdir(parents=True)
            (root / '_internal/webview/js/api.js').write_text('runtime')
            archive(root, Path(temp) / 'runtime.zip')
            (root / 'webview').mkdir()
            (root / 'webview/user-profile').write_text('personal fixture')
            with self.assertRaisesRegex(ValueError, 'Personal/generated data'):
                archive(root, Path(temp) / 'personal.zip')
