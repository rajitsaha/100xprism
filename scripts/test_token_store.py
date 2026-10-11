import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from _token_store import TokenStore, SCHEMA_VERSION, MARKER, load_manifest, register_artifact

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'generation' / 'economics.sqlite3')
        self.store = TokenStore(self.path)
    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()
    def test_private_wal_reopen(self):
        self.assertEqual(self.store.conn.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(Path(self.path).parent).st_mode & 0o777, 0o700)
        self.assertEqual(self.store.conn.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
    def test_future_schema_untouched(self):
        self.store.close()
        con = sqlite3.connect(self.path)
        con.execute('PRAGMA user_version=999')
        con.close()
        before = Path(self.path).read_bytes()
        with self.assertRaises(ValueError): TokenStore(self.path)
        self.assertEqual(Path(self.path).read_bytes(), before)
    def test_revisions_and_two_complete_snapshots(self):
        self.assertIsNone(self.store.latest_snapshot())
        self.store.save_value_context(({'/repo':'repo'}, {}), {'cache':'persisted'})
        self.assertNotEqual(*self.store.revisions)
        with self.assertRaises(RuntimeError):
            with self.store.transaction():
                self.store.conn.execute("UPDATE metadata SET value='999' WHERE key='snapshot_input_revision'")
                raise RuntimeError()
        for i in range(3): self.store.publish_snapshot({'number':i})
        self.assertEqual(self.store.latest_snapshot(), {'number':2})
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM dashboard_snapshots').fetchone()[0],2)
        self.assertEqual(*self.store.revisions)
        self.assertEqual(self.store.value_context()[1], {'cache':'persisted'})
    def test_content_disabled_secure_removal(self):
        self.assertEqual(self.store.search('private'), [])
        self.store.enable_search()
        self.store.put_content('codex:one', 'codex', 'repo', [{'text':'private sentinel needle','role':'user','timestamp':'2026-10-11','sequence':0}])
        rows = self.store.search('sentinel', role='user')
        self.assertTrue(rows)
        self.assertTrue(any(s['matched'] for s in rows[0]['segments']))
        self.store.disable_search()
        self.assertEqual(self.store.search('sentinel'), [])
        self.assertNotIn(b'private sentinel needle', Path(self.path).read_bytes())
        for suffix in ('-wal','-shm'):
            p = Path(self.path+suffix)
            self.assertTrue(not p.exists() or p.stat().st_size == 0)
    def test_revision_guard_keeps_old_snapshot(self):
        self.store.publish_snapshot({'valid':True})
        before = self.store.revisions[0]
        self.store.save_value_context(({},{}), {'new':True})
        with self.assertRaises(RuntimeError):
            self.store.publish_snapshot({'invalid':True}, expected_revision=before)
        self.assertEqual(self.store.latest_snapshot(), {'valid':True})

    def test_inclusive_day_search_and_numeric_timestamps(self):
        self.store.enable_search()
        self.store.put_content('codex:one','codex','repo',[
            {'text':'needle midnight','role':'user','sequence':0,'timestamp':'2026-10-11T00:00:00Z'},
            {'text':'needle daytime','role':'user','sequence':1,'timestamp':'2026-10-11T10:00:00Z'},
            {'text':'needle numeric','role':'assistant','sequence':2,'timestamp':1791712800000},
            {'text':'needle tomorrow','role':'user','sequence':3,'timestamp':'2026-10-12T00:00:00Z'}])
        rows=self.store.search('needle',date_from='2026-10-11',date_to='2026-10-11')
        self.assertEqual(len(rows),3)
        self.assertTrue(all(row['timestamp'].startswith('2026-10-11') for row in rows))
        self.assertEqual(self.store.search('needle',date_to='invalid'),[])
    def test_failed_cleanup_stays_disabled(self):
        self.store.enable_search()
        self.store.put_content('codex:one','codex','repo',[{'text':'private needle','role':'user','sequence':0}])
        with patch.object(self.store,'checkpoint',side_effect=RuntimeError('reader busy')):
            with self.assertRaises(RuntimeError): self.store.disable_search()
        self.assertFalse(self.store.status()['search_enabled'])
        self.assertEqual(self.store.search('needle'),[])
    def test_global_fence_suppresses_retained_index(self):
        self.store.enable_search()
        self.store.put_content('codex:one','codex','repo',[{'text':'private needle','role':'user','sequence':0}])
        fence=self.store.root/'content-search-disabled'
        fence.touch()
        self.assertFalse(self.store.status()['search_enabled'])
        self.assertEqual(self.store.search('needle'),[])
    def test_foreign_schema_rejected(self):
        foreign=Path(self.tmp.name)/'foreign.sqlite3'
        con=sqlite3.connect(foreign)
        con.execute('CREATE TABLE personal(value TEXT)')
        con.commit()
        con.close()
        before=foreign.read_bytes()
        with self.assertRaises(ValueError): TokenStore(foreign)
        self.assertEqual(foreign.read_bytes(),before)
    def test_scan_diagnostics_cap_does_not_touch_history(self):
        with self.store.transaction():
            self.store.conn.executemany("INSERT INTO scan_runs(status) VALUES('complete')",[() for _ in range(1010)])
        self.store.start_run()
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM scan_runs').fetchone()[0],1000)
    def test_ownership_registration_rejects_escape_and_foreign_file(self):
        root=Path(self.tmp.name).resolve()/'token-economics'
        root.mkdir()
        lock=root/'manifest.lock'
        lock.touch()
        manifest={'schema':1,'marker':MARKER,'root':str(root),'artifacts':[{'path':str(lock),'kind':'file'}, {'path':str(root/'manifest-next.json'),'kind':'file'}]}
        (root.parent/'token-economics-manifest.json').write_text(json.dumps(manifest))
        # A killed owned temp write can be partial; registration must recover it.
        (root/'manifest-next.json').write_text('{partial owned write')
        (root/'manifest-next.json').chmod(0o600)
        register_artifact(root,root/'new-file')
        self.assertTrue(any(e['path']==str(root/'new-file') for e in load_manifest(root)['artifacts']))
        (root/'foreign').touch()
        with self.assertRaises(ValueError): register_artifact(root,root/'foreign')
        with self.assertRaises(ValueError): register_artifact(root,root/'..'/'escaped')
        (root/'escape').symlink_to(root.parent,target_is_directory=True)
        with self.assertRaises(ValueError): register_artifact(root,root/'escape'/'external')
    def test_readers_prevent_secure_disable_success(self):
        self.store.enable_search()
        self.store.put_content('codex:one','codex','repo',[{'text':'sentinel','role':'user','sequence':0}])
        reader=sqlite3.connect(self.path)
        reader.execute('BEGIN')
        reader.execute('SELECT * FROM session_content').fetchall()
        self.store.conn.execute('PRAGMA busy_timeout=10')
        with self.assertRaises(RuntimeError): self.store.disable_search()
        self.assertFalse(self.store.get_meta('search_enabled'))
        reader.close()

if __name__ == '__main__': unittest.main()
