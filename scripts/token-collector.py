#!/usr/bin/env python3
"""One-shot, local-only incremental transcript collector."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parent))
from _token_store import TokenStore, register_artifact, load_manifest, MARKER
import adapters
import _value

BLOCK_BYTES=1048576
TAIL_BYTES=65536
INTEGRITY_BUDGET=32*BLOCK_BYTES
CONTENT_BUDGET=4*BLOCK_BYTES
ADAPTER_BY_TOOL={a.TOOL:a for a in adapters.ADAPTERS}


def read_digest(path,start,length):
    h=hashlib.blake2b(digest_size=32)
    with open(path,'rb') as fh:
        fh.seek(start)
        remaining=length
        while remaining:
            piece=fh.read(min(65536,remaining))
            if not piece: raise ValueError('source changed during hash')
            h.update(piece)
            remaining-=len(piece)
    return h.hexdigest()


class StreamingFileHashes:
    """Hash complete parser records without reopening newly consumed bytes."""
    def __init__(self,offset=0,partial=b'',tail=b''):
        self.offset=offset
        self.block_index=offset//BLOCK_BYTES
        self.partial_length=len(partial)
        self.digest=hashlib.blake2b(partial,digest_size=32)
        self.tail=tail[-TAIL_BYTES:]
        self.blocks=[]

    def __call__(self,raw):
        view=memoryview(raw)
        cursor=0
        while cursor<len(view):
            count=min(BLOCK_BYTES-self.partial_length,len(view)-cursor)
            self.digest.update(view[cursor:cursor+count])
            cursor+=count
            self.partial_length+=count
            if self.partial_length==BLOCK_BYTES:
                self.blocks.append((self.block_index,self.digest.hexdigest(),BLOCK_BYTES))
                self.block_index+=1
                self.digest=hashlib.blake2b(digest_size=32)
                self.partial_length=0
        self.offset+=len(raw)
        self.tail=(self.tail+raw)[-TAIL_BYTES:] if len(raw)<TAIL_BYTES else raw[-TAIL_BYTES:]

    def checkpoint(self):
        return (self.blocks,self.partial_length,self.digest.hexdigest() if self.partial_length else None,
                hashlib.blake2b(self.tail,digest_size=32).hexdigest() if self.offset else None)


def append_hash_observer(row,path,st,version):
    if row is None or row['parser_version']!=version or row['last_error']: return None
    if (row['device'],row['inode'])!=(st.st_dev,st.st_ino) or st.st_size<row['size'] or row['parsed_offset']>st.st_size: return None
    offset=row['parsed_offset']
    length=row['partial_length']
    partial=b''
    tail=b''
    # The seed is the same verified prior partial block; newly appended bytes are never read here.
    if offset:
        with open(path,'rb') as stream:
            if length:
                stream.seek(offset-length)
                partial=stream.read(length)
                if len(partial)!=length or hashlib.blake2b(partial,digest_size=32).hexdigest()!=row['partial_digest']: return None
            tail_length=min(TAIL_BYTES,offset)
            if length>=tail_length: tail=partial[-tail_length:]
            else:
                stream.seek(offset-tail_length)
                tail=stream.read(tail_length)
                if len(tail)!=tail_length: return None
            if hashlib.blake2b(tail,digest_size=32).hexdigest()!=row['tail_anchor']: return None
    return StreamingFileHashes(offset,partial,tail)


def source_locations():
    return [(a.TOOL,a.SOURCE_DIR) for a in adapters.ADAPTERS]


def inventory(tool,root):
    """A complete metadata walk, excluding symlink escapes and unrelated files."""
    root=Path(root)
    if not root.exists(): return []
    out=[]
    for directory,dirs,files in os.walk(root,followlinks=False,onerror=lambda e: (_ for _ in ()).throw(e)):
        dirs[:]=[d for d in dirs if not (Path(directory)/d).is_symlink()]
        for name in files:
            path=Path(directory)/name
            if path.is_symlink(): continue
            rel=path.relative_to(root)
            if tool=='cursor' and ('agent-transcripts' not in rel.parts or path.suffix!='.jsonl'): continue
            if tool=='antigravity':
                if not ((rel.parts[0]=='conversations' and path.suffix=='.pb') or
                        (rel.parts[0]=='brain' and name.endswith('.metadata.json'))): continue
            elif path.suffix!='.jsonl': continue
            if tool=='codex' and not name.startswith('rollout-'): continue
            out.append((str(path.resolve()),path.stat()))
    return sorted(out,key=lambda item:item[0])


def parse_source(tool,path,offset,state,summary,dir_index,observer=None):
    adapter=ADAPTER_BY_TOOL[tool]
    if hasattr(adapter,'parse_incremental'):
        result=adapter.parse_incremental(path,offset=offset,state=state,summary=summary,observer=observer)
        return enrich_summary(tool,path,result,dir_index)
    if tool=='cursor':
        # Compatibility parser accepts malformed records; the durable collector must not.
        with open(path,'rb') as fh:
            for record in fh:
                if not record.endswith(b'\n'): break
                if record.strip(): json.loads(record)
        row=adapter.parse_file(path,dir_index=dir_index)
    elif tool=='antigravity':
        row=activity_summary(path,adapter)
    else: row=adapter.parse_file(path)
    return {'summary':row,'state':{},'offset':os.stat(path).st_size,'bytes_read':os.stat(path).st_size}


def enrich_summary(tool,path,result,dir_index):
    row=result['summary']
    st=os.stat(path)
    row['mtime']=st.st_mtime
    row['size']=st.st_size
    row['tool']=tool
    cwd=row.get('cwd')
    if tool=='claude-code':
        parts=path.split('/projects/',1)
        projdir=parts[1].split('/',1)[0] if len(parts)>1 else Path(path).parent.name
        cwd=cwd or (dir_index or {}).get(projdir) or _value.resolve_real_dir(projdir)
        row['project']=_value.project_label_for_path(cwd) if cwd else _value.project_label(path,dir_index)
        row['projdir']=projdir
        row['cwd']=cwd
    else:
        row['project']=_value.project_label_for_path(cwd) if cwd else ('pi' if tool=='pi' else 'unknown')
        row['projdir']=_value.mangle_path(os.path.abspath(os.path.expanduser(cwd))) if cwd else 'unknown'
    return result


def activity_summary(path,adapter):
    p=Path(path)
    session=p.stem if p.suffix=='.pb' else p.parent.name
    st=p.stat()
    empty={'input':0,'output':0,'cache_read':0,'cache_write':0}
    artifacts,day=adapter._artifact_info(session)
    return {'totals':empty,'by_day':{},'by_model':{},'by_day_model':{},'comp':{},'msgs':0,'turns':0,
            'first_fixed':0,'session_id':session,'main_tokens':empty,'subagent_tokens':empty,
            'by_skill':{},'skill_invocations':{},'skill_exact':[],'mtime':st.st_mtime,'size':st.st_size,
            'project':'Antigravity · unmapped','projdir':session,'cwd':None,'tool':'antigravity',
            'activity_only':True,'activity':{'day':day or time.strftime('%Y-%m-%d',time.localtime(st.st_mtime)),
            'messages':None,'user_messages':None,'assistant_messages':None,'artifacts':artifacts,
            'text_chars':None,'source':'protobuf+artifact-metadata'}}


def ensure_location(store,tool,root):
    root=str(Path(root).resolve())
    with store.transaction():
        cur=store.conn.execute('INSERT OR IGNORE INTO source_locations(tool,root_path) VALUES(?,?)',(tool,root))
        if cur.rowcount: store.bump_revision()
    return store.conn.execute('SELECT * FROM source_locations WHERE tool=? AND root_path=?',(tool,root)).fetchone()


def file_changed(row,st,version):
    if row is not None and row['present'] and row['last_error'] and row['observed_signature_json']:
        if json.loads(row['observed_signature_json'])==[st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,version]: return False
    return (row is None or not row['present'] or row['last_error'] or row['parser_version']!=version or
            (row['device'],row['inode'],row['size'],row['mtime_ns'])!=(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns))


def append_ok(row,path,st,version):
    if row is None or row['parser_version']!=version or row['last_error']: return False
    if (row['device'],row['inode'])!=(st.st_dev,st.st_ino) or st.st_size<row['size'] or row['parsed_offset']>st.st_size: return False
    offset=row['parsed_offset']
    if offset and read_digest(path,max(0,offset-TAIL_BYTES),min(offset,TAIL_BYTES))!=row['tail_anchor']: return False
    length=row['partial_length']
    if length and read_digest(path,offset-length,length)!=row['partial_digest']: return False
    return True


def file_hashes(store,row,path,offset,rebuild):
    old_offset=0 if rebuild or row is None else row['parsed_offset']
    first=old_offset//BLOCK_BYTES
    blocks=[]
    for index in range(first,offset//BLOCK_BYTES):
        blocks.append((index,read_digest(path,index*BLOCK_BYTES,BLOCK_BYTES),BLOCK_BYTES))
    partial=offset%BLOCK_BYTES
    digest=read_digest(path,offset-partial,partial) if partial else None
    anchor=read_digest(path,max(0,offset-TAIL_BYTES),min(offset,TAIL_BYTES)) if offset else None
    return blocks,partial,digest,anchor


def contribution_key(tool,path,summary):
    session=tool+':'+str(summary.get('session_id') or path)
    agent=summary.get('agent_id')
    if not agent and tool=='claude-code' and ('subagents' in Path(path).parts or Path(path).stem.startswith('agent-')):
        agent=Path(path).stem
    return session+':'+str(agent or 'main')


def merge_summaries(rows):
    """Sum disjoint source contributions while retaining normalized metadata."""
    def merge(left,right,key=''):
        if key=='context_growth': return left if left is not None else right
        if key=='first_activity' and right: return min(left,right) if left else right
        if key=='last_activity' and right: return max(left,right) if left else right
        if isinstance(right,dict):
            out=dict(left) if isinstance(left,dict) else {}
            for name,value in right.items(): out[name]=merge(out.get(name),value,name)
            return out
        if isinstance(right,bool): return right if left is None else left and right
        if isinstance(right,(int,float)):
            if key=='mtime': return max(left or 0,right)
            return (left or 0)+right
        if isinstance(right,list):
            out=list(left or [])
            for item in right:
                if item not in out: out.append(item)
            return out
        return left if left is not None else right
    result={}
    for row in rows: result=merge(result,row)
    contexts=[row for row in rows if row.get('context_growth')]
    if contexts:
        first=min(contexts,key=lambda r:r.get('first_activity') or '')['context_growth']['first_context_tokens']
        last=max(contexts,key=lambda r:r.get('last_activity') or '')['context_growth']['last_context_tokens']
        maximum=max(r['context_growth']['max_context_tokens'] for r in contexts)
        result['context_growth']={'first_context_tokens':first,'last_context_tokens':last,'max_context_tokens':maximum,
                                  'delta_tokens':last-first,'growth_ratio':round(last/first,4) if first else None}
    return result


def refresh_session(store,key):
    rows=store.conn.execute("SELECT tool,summary_json FROM session_contributions WHERE session_key=? ORDER BY contribution_key LIKE '%:main' DESC,contribution_key",(key,)).fetchall()
    if not rows:
        store.conn.execute('DELETE FROM session_summaries WHERE session_key=?',(key,))
        return
    summary=merge_summaries([json.loads(row['summary_json']) for row in rows])
    store.conn.execute('''INSERT INTO session_summaries VALUES(?,?,?,?,?) ON CONFLICT(session_key) DO UPDATE SET
        project=excluded.project,summary_json=excluded.summary_json,updated_at=excluded.updated_at''',
        (key,rows[0]['tool'],summary.get('project'),json.dumps(summary),time.time()))


def contribution_score(summary,offset=0):
    return (summary.get('last_activity') or '',sum(summary.get('totals',{}).values()),
            (summary.get('msgs') or 0)+(summary.get('turns') or 0),offset)


def update_contribution(store,contributor,key,tool,file_id,summary):
    candidates=store.conn.execute('SELECT id,summary_json,parsed_offset FROM source_files WHERE contribution_key=? AND present=1 AND summary_json IS NOT NULL',(contributor,)).fetchall()
    selected=max(candidates,key=lambda r:contribution_score(json.loads(r['summary_json']),r['parsed_offset']))
    authoritative=json.loads(selected['summary_json'])
    authority_id=selected['id']
    previous=store.conn.execute('SELECT summary_json FROM session_contributions WHERE contribution_key=?',(contributor,)).fetchone()
    previous_id=store.get_meta('contribution_authority:'+contributor)
    if previous and previous_id!=file_id:
        prior=json.loads(previous['summary_json'])
        if contribution_score(prior)>contribution_score(authoritative):
            authoritative=prior
            authority_id=previous_id
    store.conn.execute('''INSERT INTO session_contributions VALUES(?,?,?,?,?) ON CONFLICT(contribution_key) DO UPDATE SET
        session_key=excluded.session_key,summary_json=excluded.summary_json,updated_at=excluded.updated_at''',
        (contributor,key,tool,json.dumps(authoritative),time.time()))
    store.set_meta('contribution_authority:'+contributor,authority_id)


class SourceChanged(ValueError):
    """An actively written transcript moved under the parser; retry next run."""


def commit_file(store,location,run_id,path,st,old,result,version,rebuild):
    summary=result['summary']
    if summary is None: raise ValueError('parser did not produce a summary')
    summary['tool']=location['tool']
    key=location['tool']+':'+str(summary.get('session_id') or path)
    contributor=contribution_key(location['tool'],path,summary)
    offset=result['offset']
    if not 0<=offset<=st.st_size: raise ValueError('parser returned invalid byte offset')
    blocks,partial,digest,anchor=result.get("hashes") or file_hashes(store,old,path,offset,rebuild)
    after=os.stat(path)
    if (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)!=(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns):
        raise SourceChanged('source changed during collection')
    with store.transaction():
        store.conn.execute('''INSERT INTO source_files(location_id,path,device,inode,size,mtime_ns,parsed_offset,
            tail_anchor,partial_digest,partial_length,parser_version,parser_state_json,session_key,present,last_seen_run_id,last_error)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,NULL) ON CONFLICT(location_id,path) DO UPDATE SET
            device=excluded.device,inode=excluded.inode,size=excluded.size,mtime_ns=excluded.mtime_ns,
            parsed_offset=excluded.parsed_offset,tail_anchor=excluded.tail_anchor,partial_digest=excluded.partial_digest,
            partial_length=excluded.partial_length,parser_version=excluded.parser_version,parser_state_json=excluded.parser_state_json,
            session_key=excluded.session_key,present=1,last_seen_run_id=excluded.last_seen_run_id,last_error=NULL,observed_signature_json=NULL''',
            (location['id'],path,st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,offset,anchor,digest,partial,version,
             json.dumps(result['state']),key,run_id))
        file_id=store.conn.execute('SELECT id FROM source_files WHERE location_id=? AND path=?',(location['id'],path)).fetchone()[0]
        if rebuild:
            store.conn.execute('DELETE FROM source_file_blocks WHERE file_id=?',(file_id,))
            store.conn.execute("UPDATE source_files SET content_offset=0,content_parser_version=NULL,content_state_json='{}',integrity_cursor=0 WHERE id=?",(file_id,))
            if old and old['session_key']: store.clear_content(old['session_key'],old['contribution_key'])
        store.conn.executemany('INSERT OR REPLACE INTO source_file_blocks(file_id,block_index,digest,length) VALUES(?,?,?,?)',
                              [(file_id,*b) for b in blocks])
        store.conn.execute('UPDATE source_files SET summary_json=?,contribution_key=? WHERE id=?',
                           (json.dumps(summary),contributor,file_id))
        if old and old['contribution_key'] and old['contribution_key']!=contributor:
            if not store.conn.execute('SELECT 1 FROM source_files WHERE contribution_key=? LIMIT 1',(old['contribution_key'],)).fetchone():
                store.conn.execute('DELETE FROM session_contributions WHERE contribution_key=?',(old['contribution_key'],))
                refresh_session(store,old['session_key'])
        update_contribution(store,contributor,key,location['tool'],file_id,summary)
        refresh_session(store,key)
        store.bump_revision()


def mark_file_error(store,location,path,st,run_id,error,version):
    with store.transaction():
        store.conn.execute('''INSERT INTO source_files(location_id,path,device,inode,size,mtime_ns,last_error,last_seen_run_id,observed_signature_json)
            VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(location_id,path) DO UPDATE SET last_error=excluded.last_error,last_seen_run_id=excluded.last_seen_run_id,present=1,observed_signature_json=excluded.observed_signature_json''',
            (location['id'],path,st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,error,run_id,json.dumps([st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,version])))
        store.bump_revision()


def integrity_pass(store,force=False,budget=INTEGRITY_BUDGET):
    now=time.time()
    pending=store.get_meta('integrity_cycle_pending',[])
    if not force and not pending and now<store.get_meta('integrity_due',0): return 0
    if not pending:
        with store.transaction(): store.set_meta('integrity_cycle_failed',False)
        pending=[r[0] for r in store.conn.execute('SELECT id FROM source_files WHERE present=1 AND parsed_offset>0 ORDER BY id')]
    consumed=0
    while pending:
        file_id=pending[0]
        row=store.conn.execute('SELECT * FROM source_files WHERE id=?',(file_id,)).fetchone()
        if row is None or not row['present']:
            pending.pop(0)
            continue
        blocks=store.conn.execute('SELECT block_index,digest,length FROM source_file_blocks WHERE file_id=? ORDER BY block_index',(file_id,)).fetchall()
        pieces=[tuple(b) for b in blocks]
        if row['partial_length']:
            pieces.append((row['parsed_offset']//BLOCK_BYTES,row['partial_digest'],row['partial_length']))
        cursor=row['integrity_cursor']
        if cursor>=len(pieces): cursor=0
        failed=False
        while cursor<len(pieces):
            index,digest,length=pieces[cursor]
            if consumed+length>budget: break
            try: actual=read_digest(row['path'],index*BLOCK_BYTES,length)
            except (OSError,ValueError): actual=None
            consumed+=length
            cursor+=1
            if actual!=digest:
                failed=True
                break
        with store.transaction():
            store.conn.execute('UPDATE source_files SET integrity_cursor=? WHERE id=?',(0 if failed or cursor==len(pieces) else cursor,file_id))
            if failed:
                store.set_meta('integrity_cycle_failed',True)
                store.conn.execute("UPDATE source_files SET last_error='historical integrity mismatch',observed_signature_json=NULL WHERE id=?",(file_id,))
                store.bump_revision()
        if failed or cursor==len(pieces): pending.pop(0)
        else: break
    with store.transaction():
        store.set_meta('integrity_cycle_pending',pending)
        if not pending:
            store.set_meta('integrity_due',now+86400)
            if not store.get_meta('integrity_cycle_failed'): store.set_meta('integrity_verified_at',now)
            store.bump_revision()
    return consumed


def content_backfill(store,budget=CONTENT_BUDGET):
    if not store.get_meta('search_enabled'): return 0
    from adapters._incremental import extract_content
    consumed=0
    rows=store.conn.execute('''SELECT f.*,l.tool,s.project FROM source_files f JOIN source_locations l ON l.id=f.location_id
        LEFT JOIN session_summaries s ON s.session_key=f.session_key WHERE f.present=1 AND f.session_key IS NOT NULL
        AND (f.content_offset<f.size OR f.content_parser_version IS NULL) ORDER BY f.last_seen_run_id,f.id''').fetchall()
    for row in rows:
        if consumed>=budget: break
        if row['tool']=='antigravity':
            result={'rows':[],'offset':row['size'],'bytes_read':0,'state':{}}
        else:
            try:
                result=extract_content(row['tool'],row['path'],offset=row['content_offset'],
                                       limit_bytes=min(BLOCK_BYTES,budget-consumed),state=json.loads(row['content_state_json']))
            except (OSError,ValueError): continue
        consumed+=result['bytes_read']
        with store.transaction():
            store.put_content(row['session_key'],row['tool'],row['project'],result['rows'],row['contribution_key'])
            store.conn.execute('UPDATE source_files SET content_offset=?,content_parser_version=?,content_state_json=? WHERE id=?',
                               (result['offset'],'1',json.dumps(result.get('state',{})),row['id']))
            if result['offset']!=row['content_offset'] or result.get('state',{})!=json.loads(row['content_state_json']):
                store.bump_revision()
    return consumed


def builder_fingerprint():
    digest=hashlib.blake2b(digest_size=16)
    for name in ('token-dashboard.py','pricing.py'):
        digest.update(Path(__file__).with_name(name).read_bytes())
    return digest.hexdigest()


def default_builder(**kwargs):
    spec=importlib.util.spec_from_file_location('_collector_dashboard',Path(__file__).with_name('token-dashboard.py'))
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build(**kwargs)


def durable_value_refresh(store,now=None):
    now=now or time.time()
    context,value_store=store.value_context()
    # Import legacy cache once as a hint only; never write it.
    if not store.get_meta('legacy_value_imported',False):
        try: value_store=_value.load_store()
        except (OSError,ValueError): value_store={}
        with store.transaction(): store.set_meta('legacy_value_imported',True)
    due=now-store.get_meta('value_refreshed_at',0)>=1800
    if due:
        context=_value.scan_home()
        nonrepos=sorted(path for path,entry in value_store.get('dirs',{}).items() if entry.get('head') is None)
        cursor=store.get_meta('filesystem_refresh_cursor',0)
        batch=[nonrepos[(cursor+i)%len(nonrepos)] for i in range(min(16,len(nonrepos)))] if nonrepos else []
        for path in batch:
            # Invalidate a bounded rotating batch of filesystem values; repo HEAD caches remain valid.
            value_store['dirs'][path]['window']={'collector_refresh':now}
        with store.transaction():
            store.set_meta('filesystem_refresh_cursor',(cursor+len(batch))%len(nonrepos) if nonrepos else 0)
            store.set_meta('value_refreshed_at',now)
            # Periodic value work is itself a snapshot input even if discovery did not change.
            store.bump_revision()
    store.save_value_context(context,value_store)
    return context,value_store


def collect_once(db_path,root=None,verify_history=False,locations=None,builder=None):
    store=TokenStore(db_path)
    run_id=store.start_run()
    counts={'locations_inspected':0,'files_inspected':0,'files_parsed':0,'bytes_parsed':0,'sessions_updated':0}
    try:
        context,value_store=durable_value_refresh(store)
        # Initial hashing already covered every parsed block; defer maintenance until tomorrow.
        if not store.get_meta('integrity_due'):
            with store.transaction(): store.set_meta('integrity_due',time.time()+86400)
        integrity_bytes=integrity_pass(store,force=verify_history)
        degraded=False
        for tool,source_root in (locations if locations is not None else source_locations()):
            location=ensure_location(store,tool,source_root)
            counts['locations_inspected']+=1
            with store.transaction():
                store.conn.execute("UPDATE source_locations SET status='scanning',last_scan_started_at=? WHERE id=?",(time.time(),location['id']))
            error=None
            complete=True
            try: files=inventory(tool,source_root)
            except OSError as exc:
                files=[]
                complete=False
                error='inventory failed: '+type(exc).__name__
            seen=set()
            version=str(getattr(ADAPTER_BY_TOOL[tool],'PARSER_VERSION',1))
            for path,st in files:
                seen.add(path)
                counts['files_inspected']+=1
                old=store.conn.execute('SELECT * FROM source_files WHERE location_id=? AND path=?',(location['id'],path)).fetchone()
                if not file_changed(old,st,version):
                    if old['last_error']: error=old['last_error']
                    continue
                try:
                    incremental=hasattr(ADAPTER_BY_TOOL[tool],'parse_incremental')
                    observer=append_hash_observer(old,path,st,version) if incremental else None
                    can_append=observer is not None
                    if incremental and observer is None: observer=StreamingFileHashes()
                    summary=json.loads(old['summary_json']) if can_append and old['summary_json'] else None
                    result=parse_source(tool,path,old['parsed_offset'] if can_append else 0,
                                        json.loads(old['parser_state_json']) if can_append else None,
                                        summary,context[1],observer=observer)
                    if observer is not None:
                        if observer.offset!=result['offset']: raise ValueError('hash observer checkpoint mismatch')
                        result['hashes']=observer.checkpoint()
                    commit_file(store,location,run_id,path,st,old,result,version,not can_append)
                    counts['files_parsed']+=1
                    counts['bytes_parsed']+=result['bytes_read']
                    counts['sessions_updated']+=1
                except SourceChanged:
                    # Leave the prior committed checkpoint intact; the next run resumes from it.
                    logging.getLogger('token-economics').info('deferred active file (%s)',path)
                except Exception as exc:
                    # Error diagnostics cannot contain transcript fragments from parser exceptions.
                    error='file failed: '+type(exc).__name__+' ('+path+')'
                    mark_file_error(store,location,path,st,run_id,error,version)
                    logging.getLogger('token-economics').warning('%s',error)
            with store.transaction():
                changed=False
                if complete:
                    for absent in store.conn.execute('SELECT id,path FROM source_files WHERE location_id=? AND present=1',(location['id'],)).fetchall():
                        if absent['path'] not in seen:
                            store.conn.execute('UPDATE source_files SET present=0 WHERE id=?',(absent['id'],))
                            changed=True
                current='degraded' if error else 'current'
                count=store.conn.execute('SELECT count(*),sum(present) FROM source_files WHERE location_id=?',(location['id'],)).fetchone()
                before=store.conn.execute('SELECT * FROM source_locations WHERE id=?',(location['id'],)).fetchone()
                if (before['committed_status'],before['file_count'],before['present_file_count'],before['last_error'])!=(current,count[0],count[1] or 0,error): changed=True
                store.conn.execute('''UPDATE source_locations SET status=?,committed_status=?,last_scan_finished_at=?,
                    last_full_scan_at=CASE WHEN ? THEN ? ELSE last_full_scan_at END,file_count=?,present_file_count=?,last_error=? WHERE id=?''',
                    (current,current,time.time(),not error,time.time(),count[0],count[1] or 0,error,location['id']))
                if changed: store.bump_revision()
                degraded=degraded or bool(error)
        if not root or not (Path(root)/'content-search-disabled').exists(): content_backfill(store)
        revision=store.revisions[0]
        # Pricing or aggregation changes in an update must reprice unchanged history.
        fingerprint=builder_fingerprint()
        if revision!=store.revisions[1] or fingerprint!=store.get_meta('snapshot_builder'):
            payload=(builder or default_builder)(verbose=False,summaries=store.summaries(),value_context=context,
                                                 offline=True,value_store=value_store)
            if isinstance(payload,dict) and 'transcripts' in payload:
                payload['transcripts']=store.conn.execute('SELECT COUNT(*) FROM source_files WHERE present=1').fetchone()[0]
            store.save_value_context(context,value_store)
            store.publish_snapshot(payload,run_id=run_id,expected_revision=store.revisions[0])
            with store.transaction(): store.set_meta('snapshot_builder',fingerprint)
        store.finish_run(run_id,status='degraded' if degraded else 'complete',**counts)
        return {**counts,'integrity_bytes':integrity_bytes,'revision':store.revisions[1]}
    except BaseException as exc:
        store.finish_run(run_id,status='failed',error=type(exc).__name__,**counts)
        raise
    finally: store.close()


@contextmanager
def collector_lock(root,lock_held=False):
    if lock_held:
        yield True
        return
    lock=Path(root)/'collector.lock'
    register_artifact(root,lock)
    if lock.is_symlink(): raise ValueError('collector lock is a symlink')
    fd=os.open(lock,os.O_RDWR|os.O_CREAT,0o600)
    os.fchmod(fd,0o600)
    try:
        try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally: os.close(fd)


def owned_databases(root):
    """Read the authoritative generation list only after the collector lock is held."""
    root=Path(root).resolve()
    manifest=load_manifest(root)
    generations={Path(entry['path']) for entry in manifest['artifacts'] if entry['kind']=='generation'}
    databases=[]
    for entry in manifest['artifacts']:
        if entry['kind']!='database': continue
        path=Path(entry['path'])
        if not path.exists(): continue  # Registered staging candidate will copy the scrubbed active DB later.
        generation=next((parent for parent in path.parents if parent in generations),None)
        if generation is None: raise ValueError('database has no registered generation')
        marker=generation/'.100xprism-generated'
        if marker.is_symlink() or not marker.is_file() or marker.read_text().strip()!=MARKER:
            raise ValueError('database generation is not owned')
        databases.append(str(path))
    return sorted(set(databases))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default=str(Path.home()/'.100xprism'/'token-economics'))
    parser.add_argument('--db')
    parser.add_argument('--verify-history',action='store_true')
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--enable-content-search',action='store_true')
    group.add_argument('--disable-content-search',action='store_true')
    parser.add_argument('--validate',action='store_true')
    parser.add_argument('--backup-from')
    parser.add_argument('--maintenance-dbs')
    parser.add_argument('--all-owned-dbs',action='store_true')
    parser.add_argument('--lock-held',action='store_true')
    args=parser.parse_args(argv)
    raw_root=Path(args.root).absolute()
    if raw_root.is_symlink() or raw_root.parent.is_symlink(): raise ValueError('token economics root is a symlink')
    root=raw_root.resolve()
    raw_db=Path(args.db or root/'current'/'token-economics.sqlite3').absolute()
    db=raw_db.parent.resolve()/raw_db.name
    if not db.parent.resolve().is_relative_to(root.resolve()): raise ValueError('database escapes token economics root')
    for suffix,kind in (('','database'),('-wal','sidecar'),('-shm','sidecar')):
        register_artifact(root,str(db)+suffix,kind)
    with collector_lock(root,args.lock_held) as acquired:
        if not acquired: return 0
        if args.backup_from:
            source=Path(args.backup_from).absolute()
            if not source.resolve().is_relative_to(root.resolve()): raise ValueError('backup source escapes owned root')
            manifest=json.loads((root.parent/'token-economics-manifest.json').read_text())
            if str(source) not in [e['path'] for e in manifest['artifacts'] if e['kind']=='database']:
                raise ValueError('unregistered backup source')
            db.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            original=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
            candidate=sqlite3.connect(db)
            try: original.backup(candidate)
            finally:
                original.close()
                candidate.close()
            store=TokenStore(db)
            try: store.validate()
            finally: store.close()
            return 0
        if args.validate:
            store=TokenStore(db)
            try: store.validate()
            finally: store.close()
            return 0
        if args.enable_content_search or args.disable_content_search:
            fence=root/'content-search-disabled'
            register_artifact(root,fence)
            if args.disable_content_search:
                if fence.is_symlink(): raise ValueError('search preference is a symlink')
                fd=os.open(fence,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
                os.close(fd)
            databases=owned_databases(root) if args.all_owned_dbs else (json.loads(args.maintenance_dbs) if args.maintenance_dbs else [str(db)])
            manifest=load_manifest(root)
            registered={e['path'] for e in manifest['artifacts'] if e['kind']=='database'}
            for name in databases:
                candidate=Path(name).absolute()
                if str(candidate) not in registered or not candidate.resolve().is_relative_to(root.resolve()):
                    raise ValueError('unowned search maintenance database')
                store=TokenStore(candidate)
                try:
                    if args.enable_content_search: store.enable_search()
                    else: store.disable_search()
                finally: store.close()
            if args.enable_content_search and fence.exists(): fence.unlink()
            return 0
        log=root/'collector.log'
        for p in (log,Path(str(log)+'.1'),Path(str(log)+'.2')): register_artifact(root,p)
        if any(p.is_symlink() for p in (log,Path(str(log)+'.1'),Path(str(log)+'.2'))):
            raise ValueError('collector log is a symlink')
        old_umask=os.umask(0o077)
        try:
            handler=RotatingFileHandler(log,maxBytes=BLOCK_BYTES,backupCount=2)
            logging.getLogger('token-economics').addHandler(handler)
            logging.getLogger('token-economics').setLevel(logging.INFO)
            result=collect_once(db,root=root,verify_history=args.verify_history)
            logging.getLogger('token-economics').info('collection files=%s bytes=%s',result['files_parsed'],result['bytes_parsed'])
        finally:
            os.umask(old_umask)
            if 'handler' in locals(): handler.close()
    return 0

if __name__=='__main__':
    try: sys.exit(main())
    except Exception as exc:
        # A parser's exception string may contain prompt text; log only its class.
        print('token economics collection failed: '+type(exc).__name__,file=sys.stderr)
        sys.exit(1)
