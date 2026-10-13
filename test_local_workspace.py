import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location('local_workspace', Path(__file__).with_name('local_workspace.py'))
storage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage)


class LocalWorkspaceTests(unittest.TestCase):
    def test_runtime_must_not_be_synced(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'OneDrive' / 'source'
            source.mkdir(parents=True)
            (source / 'local-workspace.json').write_text(json.dumps({'id':'demo','workspace_name':'demo'}))
            with self.assertRaises(ValueError):
                storage.settings(source, source / 'runtime')
            with self.assertRaises(ValueError):
                storage.settings(source, source.parent / 'runtime')

    def test_sync_updates_code_preserves_runtime_data_and_local_edits(self):
        with tempfile.TemporaryDirectory() as temp:
            source, runtime, state = [Path(temp) / n for n in ['source', 'runtime', 'state']]
            for p in [source, runtime, state]: p.mkdir()
            (source / 'code.py').write_text('version = 1')
            (source / 'data').mkdir()
            (source / 'data/state.json').write_text('old seed')
            storage.sync_source(source, runtime, state)
            (runtime / 'data/state.json').write_text('new local data')
            (source / 'code.py').write_text('version = 2')
            storage.sync_source(source, runtime, state)
            self.assertEqual((runtime / 'code.py').read_text(), 'version = 2')
            self.assertEqual((runtime / 'data/state.json').read_text(), 'new local data')
            (runtime / 'code.py').write_text('uncommitted local edit')
            with self.assertRaises(ValueError): storage.sync_source(source, runtime, state)
            self.assertEqual((runtime / 'code.py').read_text(), 'uncommitted local edit')

    def test_hash_verified_restore_is_seed_only_and_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            source, runtime, state = [Path(temp) / n for n in ['source', 'runtime', 'state']]
            for p in [source, runtime, state]: p.mkdir()
            payload = Path(temp) / 'payload'
            payload.write_bytes(b'original history')
            archive = Path(temp) / 'data.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.write(payload, 'history/data.bin')
                z.writestr('_archive_manifest.json', json.dumps([{'path':'history/data.bin','sha256':storage.digest(payload)}]))
            info = storage.save_chunks(source, archive, 'test')
            storage.write_json(source / '.portable-history/data-latest.json', info)
            self.assertEqual(storage.restore_history(source, runtime, state), 1)
            (runtime / 'history/data.bin').write_bytes(b'new history')
            self.assertEqual(storage.restore_history(source, runtime, state), 0)
            self.assertEqual((runtime / 'history/data.bin').read_bytes(), b'new history')
            with self.assertRaises(ValueError): storage.contained(runtime / '../outside', runtime)
            (source / info['chunks'][0]['path']).write_bytes(b'corrupt')
            other = Path(temp) / 'other'; other.mkdir()
            with self.assertRaises(ValueError): storage.reassemble(source, other, info)

    def test_removed_seed_is_not_deleted_from_local_history(self):
        with tempfile.TemporaryDirectory() as temp:
            source, runtime, state = [Path(temp) / n for n in ['source', 'runtime', 'state']]
            for p in [source, runtime, state]: p.mkdir()
            (source / 'data').mkdir()
            (source / 'data/history.json').write_text('history')
            storage.sync_source(source, runtime, state)
            (source / 'data/history.json').unlink()
            storage.sync_source(source, runtime, state)
            self.assertEqual((runtime / 'data/history.json').read_text(), 'history')

    def test_operations_do_not_overlap_and_lock_releases_after_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            with patch.object(storage, 'settings', return_value=(None,None,None,state,None)):
                with self.assertRaisesRegex(RuntimeError, 'intentional'):
                    with storage.workspace_lock():
                        with self.assertRaises((RuntimeError, BlockingIOError)):
                            with storage.workspace_lock(): pass
                        raise RuntimeError('intentional')
                with storage.workspace_lock(): pass

    def test_cache_paths_all_local(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.dict('os.environ', {'TRADING_WORKSPACE_TEMP':str(root/'temp')}):
                env = storage.local_environment(root, root/'runtime', {'id':'demo'})
            for key in ['TEMP','TMP','PIP_CACHE_DIR','PYTHONPYCACHEPREFIX','MPLCONFIGDIR','PLAYWRIGHT_BROWSERS_PATH']:
                self.assertTrue(Path(env[key]).is_relative_to(root))
            self.assertEqual(env['PYTHONPATH'], str(root/'runtime'))


if __name__ == '__main__': unittest.main()
