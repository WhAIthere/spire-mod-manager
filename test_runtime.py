import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import steam_bridge
import steam_workshop


class PackagedRuntimeTests(unittest.TestCase):
    def test_windowed_bridge_uses_result_files_not_standard_streams(self):
        with tempfile.TemporaryDirectory() as temp:
            incoming, outgoing = Path(temp) / "in.json", Path(temp) / "out.json"
            incoming.write_text('{"appid":"2868840"}', encoding="utf-8")
            with patch.object(steam_bridge, "main", return_value={"test": True}) as run, \
                 patch.object(sys, "stdin", None), patch.object(sys, "stdout", None):
                steam_bridge.file_request(incoming, outgoing)
            run.assert_called_once_with({"appid": "2868840"})
            self.assertEqual(json.loads(outgoing.read_text())["result"], {"test": True})

    def test_frozen_parent_relaunches_exe_in_bridge_mode_and_reads_result(self):
        calls = []

        def run(args, **options):
            calls.append(args)
            self.assertEqual(args[:2], [sys.executable, "--steam-bridge"])
            request = json.loads(Path(args[2]).read_text())
            self.assertEqual(request["command"], "snapshot")
            Path(args[3]).write_text('{"ok":true,"result":{"ready":true}}', encoding="utf-8")
            self.assertIn("stdout", options)
            return SimpleNamespace(returncode=0)

        with patch.object(sys, "frozen", True, create=True), patch.object(steam_workshop.subprocess, "run", side_effect=run):
            self.assertEqual(steam_workshop.bridge("game"), {"ready": True})
        self.assertEqual(len(calls), 1)
        self.assertFalse(Path(calls[0][2]).exists())
