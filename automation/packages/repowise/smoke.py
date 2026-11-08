#!/usr/bin/env python3
"""Exercise distribution integration using the packaged isolated Python runtime."""

# These imports belong to the package-private runtime, not the repository Python.
# pyright: reportMissingImports=false

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class DistributionRuntimeTests(unittest.TestCase):
    def test_cpp_query_captures_are_readable(self):
        from tree_sitter import Language, Parser, Query, QueryCursor
        import tree_sitter_cpp

        language = Language(tree_sitter_cpp.language())
        tree = Parser(language).parse(b'int value() { return 1; }')
        query = Query(language, '(function_definition declarator: (function_declarator declarator: (identifier) @name)) @function')
        matches = QueryCursor(query).matches(tree.root_node)
        self.assertEqual(matches[0][1]['name'][0].text, b'value')

    def test_numpy_scipy_and_vector_store_imports(self):
        import numpy
        from scipy import sparse
        from repowise.core.persistence.vector_store import LanceDBVectorStore

        self.assertEqual((sparse.eye(2) @ numpy.array([3, 4])).tolist(), [3, 4])
        self.assertTrue(callable(LanceDBVectorStore))

    def test_scipy_special_and_linear_algebra_execute(self):
        from scipy import linalg
        from scipy.special import cython_special

        self.assertAlmostEqual(cython_special.gamma(5.0), 24.0)
        self.assertAlmostEqual(linalg.det([[1.0, 2.0], [3.0, 4.0]]), -2.0)

    def test_refresh_uses_system_assets_without_a_download(self):
        from repowise.cli.commands.serve_cmd import _frontend, _web_assets

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'server.js').write_text('packaged dashboard')
            with patch.object(_frontend, '_SYSTEM_WEB_DIR', root), \
                    patch.object(_web_assets, '_download_web', side_effect=AssertionError('unexpected download')):
                for refresh in (False, True):
                    with self.subTest(refresh=refresh):
                        self.assertEqual(_frontend._resolve_web_bundle(refresh), (True, None))
                (root / 'server.js').unlink()
                self.assertEqual(_frontend._resolve_web_bundle(False), (False, None))

    def test_frontend_uses_system_directory_and_matching_api_host(self):
        from repowise.cli.commands.serve_cmd import _frontend

        with patch.object(_frontend.subprocess, 'Popen') as start:
            _frontend._start_frontend('/usr/bin/node', 7337, 3000, host='127.0.0.1')
        arguments = start.call_args
        self.assertEqual(arguments.args[0], ['/usr/bin/node', '/usr/lib/repowise/web/server.js'])
        self.assertEqual(arguments.kwargs['cwd'], '/usr/lib/repowise/web')
        self.assertEqual(arguments.kwargs['env']['HOSTNAME'], '127.0.0.1')
        self.assertEqual(arguments.kwargs['env']['REPOWISE_API_URL'], 'http://localhost:7337')

    def test_upgrade_and_uninstall_hints_preserve_package_ownership(self):
        from repowise.cli.update_check import suggest_update_command
        from repowise.cli.uninstall.inventory import _package_uninstall_hint
        from repowise.server.routers.meta import _UPGRADE_COMMAND

        for executable in ('/usr/bin/repowise', '/usr/lib/repowise/bin/repowise', None):
            command, _ = suggest_update_command(executable, '/usr/lib/repowise/bin/python')
            self.assertIn('AUR', command)
            self.assertNotIn('pip', command)
        with patch('sys.prefix', '/usr/lib/repowise'):
            self.assertEqual(_package_uninstall_hint(), 'sudo pacman -R repowise')
        self.assertIn('AUR', _UPGRADE_COMMAND)
        self.assertNotIn('pip', _UPGRADE_COMMAND)

    def test_packaged_analysis_and_agent_resources_are_present(self):
        from importlib.resources import files
        from repowise.cli.augment_hook import main as augment
        from repowise.cli.rewrite_hook import main as rewrite

        self.assertTrue(callable(augment))
        self.assertTrue(callable(rewrite))
        self.assertTrue(any(files('repowise.core.ingestion').joinpath('queries').iterdir()))
        self.assertTrue(any(files('repowise.core.generation').joinpath('templates').iterdir()))
        prompt = files('repowise.cli.agent_targets').joinpath('_data/codex_prompts/repowise-doctor.md').read_text()
        self.assertIn('AUR package', prompt)
        self.assertNotIn('pip install', prompt)


if __name__ == '__main__':
    unittest.main(verbosity=2)
