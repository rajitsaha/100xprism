import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch
from _token_store import TokenStore

spec=importlib.util.spec_from_file_location('collector',str(Path(__file__).with_name('token-collector.py')))
collector=importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)

def line(n=10,session='one',text='hello'):
    return json.dumps({'type':'assistant','sessionId':session,'timestamp':'2026-10-11T10:00:00Z',
        'message':{'role':'assistant','model':'claude-sonnet-4-5','content':text,
        'usage':{'input_tokens':n,'output_tokens':1}}})+'\n'

def assert_no_pending_wal(test,db):
    # Some SQLite builds leave empty -wal/-shm files after close; only unflushed frames matter.
    wal=Path(str(db)+'-wal')
    test.assertTrue(not wal.exists() or wal.stat().st_size==0)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.discovery=patch.object(collector._value,'scan_home',return_value=({},{}))
        self.discovery.start()
        self.addCleanup(self.discovery.stop)
        self.legacy=patch.object(collector._value,'load_store',return_value={})
        self.legacy.start()
        self.addCleanup(self.legacy.stop)
        self.tmp=tempfile.TemporaryDirectory()
        root=Path(self.tmp.name)
        self.path=root/'source'/'one.jsonl'
        self.path.parent.mkdir()
        self.path.write_text(line())
        self.db=root/'db'/'tokens.sqlite3'
        self.locations=[('claude-code',str(self.path.parent))]
        self.build=lambda **kwargs: {'total':sum(r['totals']['input'] for r in kwargs['summaries'])}
    def tearDown(self): self.tmp.cleanup()
    def collect(self,**kwargs):
        return collector.collect_once(str(self.db),locations=self.locations,builder=kwargs.pop("builder",self.build),**kwargs)
    def test_unchanged_no_content_or_aggregation(self):
        self.collect()
        with patch.object(collector,'parse_source',side_effect=AssertionError('reparse')),patch.object(collector,'read_digest',side_effect=AssertionError('opened')):
            self.collect(builder=lambda **kw: self.fail('aggregation'))
    def test_builder_change_reprices_unchanged_history(self):
        self.collect()
        with patch.object(collector,'builder_fingerprint',return_value='new-pricing'):
            self.collect(builder=lambda **kw:{'total':-1,'transcripts':0})
        store=TokenStore(self.db)
        self.assertEqual(store.latest_snapshot(),{'total':-1,'transcripts':1})
        store.close()
        with patch.object(collector,'builder_fingerprint',return_value='new-pricing'):
            self.collect(builder=lambda **kw:self.fail('unneeded build'))
    def test_append_and_incomplete_record(self):
        self.collect()
        with self.path.open('a') as fh: fh.write(line(7)); fh.write('{')
        self.collect()
        store=TokenStore(self.db)
        self.assertEqual(store.latest_snapshot()['total'],17)
        offset=store.conn.execute('SELECT parsed_offset FROM source_files').fetchone()[0]
        self.assertEqual(offset,self.path.stat().st_size-1)
        store.close()
    def test_deleted_then_move_deduplicates(self):
        self.collect()
        self.path.unlink()
        self.collect()
        store=TokenStore(self.db)
        self.assertEqual(store.latest_snapshot()['total'],10)
        self.assertEqual(store.conn.execute('SELECT present FROM source_files').fetchone()[0],0)
        store.close()
        self.path.with_name('moved.jsonl').write_text(line())
        self.collect()
        store=TokenStore(self.db)
        self.assertEqual(len(store.summaries()),1)
        store.close()
    def test_active_file_growth_defers_without_degrading(self):
        self.collect()
        real=collector.parse_source
        def grow(*args,**kwargs):
            result=real(*args,**kwargs)
            with self.path.open('a') as fh: fh.write(line(5))
            return result
        with self.path.open('a') as fh: fh.write(line(7))
        with patch.object(collector,'parse_source',side_effect=grow):
            self.collect()
        store=TokenStore(self.db)
        self.assertEqual(store.latest_snapshot()['total'],10)
        self.assertEqual(store.status()['locations'][0]['status'],'current')
        self.assertIsNone(store.conn.execute('SELECT last_error FROM source_files').fetchone()[0])
        store.close()
        self.collect()
        store=TokenStore(self.db)
        self.assertEqual(store.latest_snapshot()['total'],22)
        store.close()
    def test_malformed_preserves_prior_summary(self):
        self.collect()
        self.path.write_text('invalid\n')
        self.collect()
        store=TokenStore(self.db)
        self.assertEqual(store.latest_snapshot()['total'],10)
        location=store.status()['locations'][0]
        self.assertEqual(location['status'],'degraded')
        self.assertIsNotNone(location['last_full_scan_at'])
        store.close()
    def test_snapshot_failure_recovered_without_parsing(self):
        self.collect()
        with self.path.open('a') as fh: fh.write(line(7))
        with self.assertRaises(RuntimeError):
            self.collect(builder=lambda **kw: (_ for _ in ()).throw(RuntimeError('build failed')))
        store=TokenStore(self.db)
        self.assertEqual(store.latest_snapshot()['total'],10)
        store.close()
        with patch.object(collector,'parse_source',side_effect=AssertionError('reparse')):
            self.collect()
        store=TokenStore(self.db)
        self.assertEqual(store.latest_snapshot()['total'],17)
        store.close()
    def test_same_metadata_partial_rewrite_found_by_integrity(self):
        self.collect()
        old=self.path.stat()
        self.path.write_text(line(20))
        import os
        os.utime(self.path,ns=(old.st_atime_ns,old.st_mtime_ns))
        self.collect(verify_history=True)
        store=TokenStore(self.db)
        self.assertEqual(store.latest_snapshot()['total'],20)
        store.close()

    def test_initial_and_append_hash_new_bytes_inside_parse_stream(self):
        self.path.write_text(line(text='a'*(collector.BLOCK_BYTES-1000)))
        with patch.object(collector,'read_digest',side_effect=AssertionError('second hash pass')),patch.object(collector,'file_hashes',side_effect=AssertionError('fallback hashes')):
            self.collect()
        with self.path.open('a') as fh: fh.write(line(7,text='b'*2000))
        with patch.object(collector,'file_hashes',side_effect=AssertionError('new bytes reopened')):
            self.collect()
        store=TokenStore(self.db)
        row=store.conn.execute('SELECT * FROM source_files').fetchone()
        self.assertEqual(row['tail_anchor'],collector.read_digest(self.path,max(0,row['parsed_offset']-collector.TAIL_BYTES),min(collector.TAIL_BYTES,row['parsed_offset'])))
        self.assertEqual(row['partial_digest'],collector.read_digest(self.path,row['parsed_offset']-row['partial_length'],row['partial_length']))
        block=store.conn.execute('SELECT * FROM source_file_blocks').fetchone()
        self.assertEqual(block['digest'],collector.read_digest(self.path,0,collector.BLOCK_BYTES))
        store.close()
    def test_partial_block_promotion_has_no_false_rebuild(self):
        self.path.write_text(line(text='a'*(collector.BLOCK_BYTES-1000)))
        self.collect()
        with self.path.open('a') as fh: fh.write(line(7,text='b'*2000))
        original=collector.parse_source
        starts=[]
        def spy(tool,path,offset,*args,**kwargs):
            starts.append(offset)
            return original(tool,path,offset,*args,**kwargs)
        with patch.object(collector,'parse_source',side_effect=spy): self.collect()
        self.assertGreater(starts[0],0)
        store=TokenStore(self.db)
        self.assertEqual(store.latest_snapshot()['total'],17)
        self.assertEqual(store.conn.execute('SELECT count(*) FROM source_file_blocks').fetchone()[0],1)
        store.close()
    def test_unchanged_error_retains_degraded_without_reopening(self):
        self.collect()
        self.path.write_text('invalid\n')
        self.collect()
        with patch.object(collector,'parse_source',side_effect=AssertionError('reparse')):
            self.collect(builder=lambda **kw:self.fail('unneeded build'))
        store=TokenStore(self.db)
        self.assertEqual(store.status()['locations'][0]['status'],'degraded')
        store.close()
    def test_absence_snapshot_failure_recovered_without_parsing(self):
        self.collect()
        self.path.unlink()
        with self.assertRaises(RuntimeError):
            self.collect(builder=lambda **kw:(_ for _ in ()).throw(RuntimeError('build failed')))
        with patch.object(collector,'parse_source',side_effect=AssertionError('reparse')): self.collect()
        store=TokenStore(self.db)
        self.assertTrue(store.summaries()[0]['historical'])
        self.assertEqual(*store.revisions)
        store.close()
    def test_stale_duplicate_copy_cannot_reduce_authoritative_history(self):
        self.collect()
        with self.path.open('a') as fh: fh.write(line(7))
        self.collect()
        self.path.with_name('z-stale-copy.jsonl').write_text(line())
        self.collect()
        store=TokenStore(self.db)
        self.assertEqual(store.latest_snapshot()['total'],17)
        self.assertEqual(len(store.summaries()),1)
        store.close()
        # An intentional rewrite of the authoritative file still replaces its own contribution.
        self.path.write_text(line(25))
        self.collect()
        store=TokenStore(self.db)
        self.assertEqual(store.latest_snapshot()['total'],25)
        store.close()
    def test_parent_and_subagent_contributions_append_independently(self):
        agent=self.path.parent/'subagents'/'agent-one.jsonl'
        agent.parent.mkdir()
        record=json.loads(line(30))
        record['isSidechain']=True
        record['agentId']='agent-one'
        agent.write_text(json.dumps(record)+'\n')
        self.collect()
        with self.path.open('a') as fh: fh.write(line(7))
        self.collect()
        store=TokenStore(self.db)
        self.assertEqual(store.latest_snapshot()['total'],47)
        self.assertEqual(len(store.summaries()),1)
        self.assertEqual(store.summaries()[0]['main_tokens']['input'],17)
        self.assertEqual(store.summaries()[0]['subagent_tokens']['input'],30)
        store.close()
    def test_rebuild_changed_session_does_not_leave_phantom(self):
        self.collect()
        self.path.write_text(line(25,session='replacement'))
        self.collect()
        store=TokenStore(self.db)
        self.assertEqual(store.latest_snapshot()['total'],25)
        self.assertEqual(len(store.summaries()),1)
        self.assertEqual(store.summaries()[0]['session_id'],'replacement')
        store.close()
    def test_content_backfill_preserves_economics(self):
        self.collect()
        store=TokenStore(self.db)
        store.enable_search()
        store.close()
        with patch.object(collector,'parse_source',side_effect=AssertionError('economics reparse')):
            self.collect()
        store=TokenStore(self.db)
        self.assertEqual(store.latest_snapshot()['total'],10)
        self.assertTrue(store.search('hello'))
        self.assertEqual(store.status()['content_backfill']['complete'],1)
        store.close()
    def test_content_commit_snapshot_recovery_without_content_read(self):
        self.collect()
        store=TokenStore(self.db)
        store.enable_search()
        store.close()
        with self.assertRaises(RuntimeError):
            self.collect(builder=lambda **kw:(_ for _ in ()).throw(RuntimeError('build failed')))
        from adapters import _incremental
        with patch.object(_incremental,'extract_content',side_effect=AssertionError('content reread')):
            self.collect()
        store=TokenStore(self.db)
        self.assertTrue(store.search('hello'))
        self.assertEqual(*store.revisions)
        store.close()
    def test_value_commit_snapshot_recovery_without_parse(self):
        self.collect()
        store=TokenStore(self.db)
        store.save_value_context(({},{}),{'durable':'cache'})
        store.close()
        with patch.object(collector,'parse_source',side_effect=AssertionError('reparse')):
            self.collect()
        store=TokenStore(self.db)
        self.assertEqual(store.value_context()[1],{'durable':'cache'})
        self.assertEqual(*store.revisions)
        store.close()
    def test_collector_lock_contention_exits_without_work(self):
        from _token_store import MARKER
        root=Path(self.tmp.name).resolve()/'token-economics'
        root.mkdir()
        manifest={'schema':1,'marker':MARKER,'root':str(root),'artifacts':[{'path':str(root/'manifest.lock'),'kind':'file'}, {'path':str(root/'manifest-next.json'),'kind':'file'}]}
        (root.parent/'token-economics-manifest.json').write_text(json.dumps(manifest))
        (root/'manifest.lock').touch()
        with collector.collector_lock(root) as first:
            self.assertTrue(first)
            with collector.collector_lock(root) as second: self.assertFalse(second)
    def test_stale_run_lease_and_location_restored(self):
        self.collect()
        store=TokenStore(self.db)
        old_run=store.start_run()
        with store.transaction():
            store.conn.execute("UPDATE source_locations SET status='scanning'")
        store.close()
        self.collect()
        store=TokenStore(self.db)
        self.assertEqual(store.conn.execute('SELECT status FROM scan_runs WHERE id=?',(old_run,)).fetchone()[0],'abandoned')
        self.assertEqual(store.status()['locations'][0]['status'],'current')
        self.assertIsNone(store.get_meta('lease'))
        store.close()
    def test_integrity_reads_at_most_32_mib(self):
        self.collect()
        store=TokenStore(self.db)
        file_id=store.conn.execute('SELECT id FROM source_files').fetchone()[0]
        with store.transaction():
            store.conn.execute('DELETE FROM source_file_blocks')
            store.conn.executemany('INSERT INTO source_file_blocks VALUES(?,?,?,?)',
                                  [(file_id,i,'digest',collector.BLOCK_BYTES) for i in range(40)])
            store.conn.execute('UPDATE source_files SET partial_length=0,parsed_offset=?',(40*collector.BLOCK_BYTES,))
        with patch.object(collector,'read_digest',return_value='digest') as digest:
            self.assertEqual(collector.integrity_pass(store,force=True),32*collector.BLOCK_BYTES)
            self.assertEqual(digest.call_count,32)
        self.assertEqual(store.conn.execute('SELECT integrity_cursor FROM source_files').fetchone()[0],32)
        self.assertTrue(store.get_meta('integrity_cycle_pending'))
        store.close()


class SubprocessIntegrationTests(unittest.TestCase):
    def test_default_builder_collection_search_and_disable(self):
        import os
        import subprocess
        from _token_store import MARKER
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory).resolve()
            root=home/'.100xprism'/'token-economics'
            generation=root/'generation-test'
            generation.mkdir(parents=True)
            (generation/'.100xprism-generated').write_text(MARKER)
            db=generation/'token-economics.sqlite3'
            manifest={'schema':1,'marker':MARKER,'root':str(root),'artifacts':[
                {'path':str(generation),'kind':'generation'},
                {'path':str(generation/'.100xprism-generated'),'kind':'file'},
                {'path':str(root/'manifest.lock'),'kind':'file'}, {'path':str(root/'manifest-next.json'),'kind':'file'}]}
            (root.parent/'token-economics-manifest.json').write_text(json.dumps(manifest))
            (root/'manifest.lock').touch(mode=0o600)
            project=home/'known-project'
            project.mkdir()
            (project/'AGENTS.md').write_text('project marker')
            from _value import mangle_path
            transcript=home/'.claude'/'projects'/mangle_path(str(project))/'one.jsonl'
            transcript.parent.mkdir(parents=True)
            transcript.write_text(line())
            script=str(Path(__file__).with_name('token-collector.py').resolve())
            env={**os.environ,'HOME':str(home),'PATH':'/usr/bin:/bin'}
            def run(*flags):
                completed=subprocess.run([sys.executable,'-B',script,'--root',str(root),'--db',str(db),*flags],
                                         env=env,capture_output=True,text=True,timeout=20)
                self.assertEqual(completed.returncode,0,completed.stderr)
            run()
            store=TokenStore(db)
            self.assertEqual(store.latest_snapshot()['totals']['input'],10)
            self.assertEqual(store.summaries()[0]['project'],'~/known-project')
            self.assertEqual(store.summaries()[0]['cwd'],str(project))
            self.assertTrue(any(row.get('label')=='~/known-project' for row in store.latest_snapshot()['directories']))
            self.assertEqual(len(store.status()['locations']),5)
            first_revision=store.revisions
            store.close()
            run()
            store=TokenStore(db)
            self.assertEqual(store.revisions,first_revision)
            store.close()
            with transcript.open('a') as fh: fh.write(line(7))
            run()
            run('--enable-content-search')
            run()
            store=TokenStore(db)
            self.assertEqual(store.latest_snapshot()['totals']['input'],17)
            self.assertEqual(len(store.search('hello')),2)
            store.close()
            run('--disable-content-search')
            store=TokenStore(db)
            self.assertEqual(store.search('hello'),[])
            self.assertEqual(store.summaries()[0]['totals']['input'],17)
            self.assertFalse(store.status()['search_enabled'])
            store.close()
            self.assertFalse((home/'.100xprism'/'value.json').exists())
            candidate=root/'generation-candidate'
            from _token_store import register_artifact
            register_artifact(root,candidate,'generation')
            candidate.mkdir()
            register_artifact(root,candidate/'.100xprism-generated')
            (candidate/'.100xprism-generated').write_text(MARKER)
            candidate_db=candidate/'token-economics.sqlite3'
            completed=subprocess.run([sys.executable,'-B',script,'--root',str(root),'--db',str(candidate_db),'--backup-from',str(db)],
                                     env=env,capture_output=True,text=True,timeout=20)
            self.assertEqual(completed.returncode,0,completed.stderr)
            assert_no_pending_wal(self,candidate_db)
            cloned=TokenStore(candidate_db)
            self.assertEqual(cloned.summaries()[0]['totals']['input'],17)
            cloned.close()


class MaintenanceRaceTests(unittest.TestCase):
    def test_owned_db_inventory_occurs_after_lock_and_ignores_stale_list(self):
        from contextlib import contextmanager
        from _token_store import MARKER, register_artifact
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()/'token-economics'
            root.mkdir()
            manifest={'schema':1,'marker':MARKER,'root':str(root),'artifacts':[
                {'path':str(root/'manifest.lock'),'kind':'file'},
                {'path':str(root/'manifest-next.json'),'kind':'file'}]}
            (root.parent/'token-economics-manifest.json').write_text(json.dumps(manifest))
            (root/'manifest.lock').touch(mode=0o600)
            def generation(name):
                path=root/name
                register_artifact(root,path,'generation')
                path.mkdir()
                register_artifact(root,path/'.100xprism-generated')
                (path/'.100xprism-generated').write_text(MARKER)
                db=path/'token-economics.sqlite3'
                for suffix,kind in (('','database'),('-wal','sidecar'),('-shm','sidecar')):
                    register_artifact(root,str(db)+suffix,kind)
                store=TokenStore(db)
                store.enable_search()
                store.put_content(name,'codex','repo',[{'text':'secret needle','role':'user','sequence':0}])
                store.close()
                return db
            first=generation('generation-first')
            later=[]
            @contextmanager
            def acquire(root,lock_held=False):
                # Models an update cutover that finishes while maintenance waited for flock.
                later.append(generation('generation-after-wait'))
                yield True
            with patch.object(collector,'collector_lock',side_effect=acquire):
                collector.main(['--root',str(root),'--db',str(first),'--disable-content-search',
                                '--all-owned-dbs','--maintenance-dbs',json.dumps([str(first)])])
            self.assertEqual(len(later),1)
            for db in (first,later[0]):
                store=TokenStore(db)
                self.assertFalse(store.get_meta('search_enabled'))
                self.assertEqual(store.search('needle'),[])
                self.assertFalse(store.conn.execute("SELECT name FROM sqlite_master WHERE name='session_content_fts'").fetchone())
                store.close()
                assert_no_pending_wal(self,db)

if __name__=='__main__': unittest.main()
