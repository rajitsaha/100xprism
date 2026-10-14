'use strict'
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const economics = require('../lib/token-economics')
test('direct macOS shell install and update defer collector work without Node', () => {
  const {spawnSync}=require('node:child_process')
  const home=fs.mkdtempSync(path.join(os.tmpdir(),'prism-no-node-'))
  const bin=path.join(home,'empty-bin'); fs.mkdirSync(bin)
  const retained=path.join(home,'retained-runtime'); fs.writeFileSync(retained,'preserved')
  const install=fs.readFileSync(path.join(__dirname,'..','install.sh'),'utf8').split('# Direct shell installs receive')[1]
  const update=fs.readFileSync(path.join(__dirname,'..','update.sh'),'utf8')
  const helper=update.slice(update.indexOf('sync_token_economics() {'),update.indexOf('\n}\n',update.indexOf('sync_token_economics() {'))+3)
  for (const [script,message] of [
    ['uname(){ echo Darwin; }\n# Direct shell installs receive'+install,/Setup deferred/],
    [helper+'\nsync_token_economics\nsync_token_economics origin/main',/Existing collector retained/],
  ]) {
    const result=spawnSync('/bin/bash',['-eu','-c',script],{encoding:'utf8',env:{...process.env,PATH:bin,REPO_DIR:home,PRISM_TOKEN_LIFECYCLE_DONE:'0'}})
    assert.equal(result.status,0,result.stderr)
    assert.match(result.stdout,message)
    assert.equal(fs.readFileSync(retained,'utf8'),'preserved')
  }
})
function fixture(platform = 'darwin') {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'prism-lifecycle-'))
  const calls = []
  const run = (command, args, options) => {
    calls.push({command, args, options})
    if (args.includes('-c') && args[1].includes('sys.executable')) return {status:0, stdout:'/opt/Homebrew Python/python3\n'}
    if (command === 'git') return {status:0, stdout:''}
    return {status:0, stdout:''}
  }
  const api = economics.createLifecycle({home, platform, run, python:'/opt/Homebrew Python/python3'})
  return {home, api, calls}
}
test('unsupported lifecycle does not create files', () => {
  const {home,api,calls} = fixture('linux')
  assert.equal(api.install(__dirname).supported, false)
  assert.equal(fs.existsSync(path.join(home,'.100xprism')),false)
  assert.equal(calls.length,0)
})
test('plist uses exact interpreter and stable launcher with background scheduling', () => {
  const {api} = fixture()
  const plist = api.plist('/opt/Homebrew Python/python3')
  assert.match(plist, /\/opt\/Homebrew Python\/python3/)
  assert.match(plist, /launcher.py/)
  assert.match(plist, /<integer>60<\/integer>/)
  assert.doesNotMatch(plist,/KeepAlive/)
})
test('foreign manifests and symlink escapes refuse ownership', () => {
  const {home,api} = fixture()
  api.initialize()
  const outside = path.join(home,'outside'); fs.mkdirSync(outside)
  fs.symlinkSync(outside,path.join(api.root,'escape'))
  assert.throws(()=>api.register(path.join(api.root,'escape','x'),'file'),/escape|symlink/)
  const m=JSON.parse(fs.readFileSync(api.manifest,'utf8')); m.artifacts.push({path:outside,kind:'file'})
  fs.writeFileSync(api.manifest,JSON.stringify(m))
  assert.throws(()=>api.purge(),/outside|contained/)
  assert.ok(fs.existsSync(outside))
})
test('foreign launch agent survives uninstall and npm hook never opts in', () => {
  const {api,calls} = fixture(); fs.mkdirSync(path.dirname(api.agent),{recursive:true}); fs.writeFileSync(api.agent,'foreign')
  assert.equal(api.uninstall().removed,false)
  assert.throws(()=>api.install(__dirname),/foreign/)
  assert.equal(api.postinstall(),false)
  assert.equal(calls.filter(c=>c.command==='launchctl').length,0)
})
test('dynamic registration precedes file creation and unknown files prevent directory purge', () => {
  const {api} = fixture(); api.initialize()
  const f=path.join(api.root,'owned.log'); api.register(f,'file'); fs.writeFileSync(f,'')
  fs.writeFileSync(path.join(api.root,'user-file'),'keep')
  assert.throws(()=>api.purge(true),/not empty|ENOTEMPTY/)
  assert.equal(fs.readFileSync(path.join(api.root,'user-file'),'utf8'),'keep')
  assert.ok(fs.existsSync(api.manifest))
})
test('dirty clone refusal considers untracked changes and never cleans ignored files', () => {
  const {home}=fixture(); const clone=path.join(home,'clone'); fs.mkdirSync(clone)
  const api=economics.createLifecycle({home,platform:'darwin',run:()=>({status:0,stdout:'?? notes\n'})})
  assert.throws(()=>api.assertClean(clone),/tracked or untracked/)
})
test('dirty clone refusal lists the changes and how to keep them, without a stack trace', () => {
  const {home}=fixture(); const clone=path.join(home,'clone'); fs.mkdirSync(clone)
  const stdout=[' M install.sh',...Array.from({length:12},(_,i)=>`?? notes-${i}`)].join('\n')+'\n'
  const api=economics.createLifecycle({home,platform:'darwin',run:()=>({status:0,stdout})})
  assert.throws(()=>api.assertClean(clone),e=>e.userFacing===true && /M install\.sh/.test(e.message)
    && /and 3 more/.test(e.message) && /stash -u/.test(e.message))
})

function realFixture() {
  const {spawnSync}=require('node:child_process')
  const home=fs.mkdtempSync(path.join(os.tmpdir(),'prism-real-lifecycle-'))
  const source=path.join(home,'source'); fs.mkdirSync(path.join(source,'scripts'),{recursive:true})
  const collector=`import argparse, json, pathlib, sqlite3, sys
p=argparse.ArgumentParser()
p.add_argument('--db'); p.add_argument('--root'); p.add_argument('--backup-from'); p.add_argument('--validate',action='store_true'); p.add_argument('--lock-held',action='store_true')
a=p.parse_args()
m=json.loads((pathlib.Path(a.root).parent/'token-economics-manifest.json').read_text())
assert any(e['path']==a.db and e['kind']=='database' for e in m['artifacts'])
db=sqlite3.connect(a.db)
if a.backup_from:
 old=sqlite3.connect(a.backup_from); old.backup(db); old.close()
db.execute('CREATE TABLE IF NOT EXISTS history(value TEXT)')
if not db.execute('SELECT COUNT(*) FROM history').fetchone()[0]: db.execute("INSERT INTO history VALUES ('preserved')")
db.commit(); db.close()
if a.validate and pathlib.Path(__file__).with_name('fail-validation').exists(): sys.exit(17)
if a.validate and pathlib.Path(__file__).with_name('leave-wal').exists(): pathlib.Path(a.db+'-wal').write_bytes(b'frames')
if not a.lock_held and pathlib.Path(__file__).with_name('fail-runtime').exists(): sys.exit(18)
`
  fs.writeFileSync(path.join(source,'scripts','token-collector.py'),collector)
  fs.writeFileSync(path.join(source,'scripts','token-dashboard.py'),'# test server\n')
  const calls=[]; let loaded=false
  const run=(command,args,opts)=> {
    calls.push({command,args})
    if (command==='launchctl') {
      if (args[0]==='print') return {status:loaded?0:1,stdout:''}
      if (args[0]==='bootstrap') loaded=true
      if (args[0]==='bootout') loaded=false
      return {status:0,stdout:''}
    }
    if (command==='git') return {status:0,stdout:''}
    return spawnSync(command,args,opts)
  }
  const api=economics.createLifecycle({home,platform:'darwin',run})
  return {home,source,api,calls}
}
test('real cutover preserves history and preceding runnable generation, no candidate sidecars', () => {
  const {source,api,calls}=realFixture()
  const first=api.install(source)
  assert.equal(api.active(),first.generation)
  const launcher=fs.readFileSync(path.join(api.root,'launcher.py'),'utf8')
  const second=api.update(source)
  assert.equal(api.active(),second.generation)
  assert.equal(fs.readFileSync(path.join(api.root,'launcher.py'),'utf8'),launcher)
  assert.ok(fs.existsSync(path.join(first.generation,'token-economics.sqlite3')))
  assert.ok(!fs.existsSync(path.join(second.generation,'token-economics.sqlite3-wal')))
  assert.equal(calls.filter(c=>c.command==='launchctl'&&c.args[0]==='bootout').length,0)
  assert.ok(calls.some(c=>c.command==='launchctl'&&c.args[0]==='kickstart'))
  // Upgrades end an in-flight background pass instead of silently waiting on its lock.
  assert.ok(calls.some(c=>c.command==='launchctl'&&c.args[0]==='kill'&&c.args[1]==='SIGTERM'))
  api.uninstall()
  assert.ok(fs.existsSync(path.join(second.generation,'token-economics.sqlite3')))
  api.purge()
  assert.equal(fs.existsSync(api.root),false)
  assert.equal(fs.existsSync(api.manifest),false)
})
test('candidate validation failure leaves the old complete runtime active', () => {
  const {source,api}=realFixture(); const first=api.install(source)
  fs.writeFileSync(path.join(source,'scripts','fail-validation'),'')
  assert.throws(()=>api.update(source),/failed/)
  assert.equal(api.active(),first.generation)
  assert.ok(fs.existsSync(path.join(first.generation,'token-economics.sqlite3')))
})
test('remaining candidate WAL prevents atomic cutover', () => {
  const {source,api}=realFixture(); const first=api.install(source)
  fs.writeFileSync(path.join(source,'scripts','leave-wal'),'')
  assert.throws(()=>api.update(source),/sidecars remain/)
  assert.equal(api.active(),first.generation)
})
test('missing interpreter unloads owned job and preserves its file for repair', () => {
  const {source,api,home}=realFixture(); api.install(source)
  const broken=economics.createLifecycle({home,platform:'darwin',python:'/no-such-python',run:require('node:child_process').spawnSync})
  assert.throws(()=>broken.python(),/Restore python3/)
  assert.ok(fs.existsSync(api.agent))
})
test('purge refuses symlinked database before removing any owned data', () => {
  const {source,api,home}=realFixture(); const first=api.install(source)
  const db=path.join(first.generation,'token-economics.sqlite3'), foreign=path.join(home,'foreign.sqlite3')
  fs.renameSync(db,foreign); fs.symlinkSync(foreign,db)
  assert.throws(()=>api.purge(),/symlink/)
  assert.ok(fs.existsSync(api.agent)); assert.ok(fs.existsSync(foreign)); assert.ok(fs.existsSync(api.manifest))
})
test('npm preuninstall retains owned collector and a live dashboard reader', () => {
  const {spawn,spawnSync}=require('node:child_process')
  const {source,api,home}=realFixture(); const generation=api.install(source).generation
  const script=path.join(generation,'scripts','token-dashboard.py')
  fs.writeFileSync(script,'import time\ntime.sleep(30)\n')
  const child=spawn('python3',['-B',script],{stdio:'ignore'})
  try {
    fs.writeFileSync(api.runtime,JSON.stringify({pid:child.pid,port:8787,token:'a'.repeat(64),script}),{mode:0o600})
    const result=spawnSync(process.execPath,[path.resolve(__dirname,'../lib/uninstall.js'),'--remove-live-links'],
      {env:{...process.env,HOME:home,USERPROFILE:home,npm_lifecycle_event:'preuninstall'},encoding:'utf8'})
    assert.equal(result.status,0,result.stderr)
    assert.ok(fs.existsSync(api.agent)); assert.ok(fs.existsSync(api.runtime))
    process.kill(child.pid,0)
  } finally {child.kill('SIGTERM')}
})
test('owned stale runtime credentials are removed and fragments contain bootstrap token', () => {
  const {source,api}=realFixture(); const generation=api.install(source).generation
  const dashboard=require('../lib/dashboard')
  const record={pid:2147483647,port:9898,token:'x'.repeat(64),script:path.join(generation,'scripts','token-dashboard.py')}
  fs.writeFileSync(api.runtime,JSON.stringify(record),{mode:0o600})
  assert.equal(dashboard.readRuntime(api),null)
  assert.equal(fs.existsSync(api.runtime),false)
  assert.equal(dashboard.runtimeUrl(record),`http://127.0.0.1:9898/#token=${record.token}`)
})
test('unsupported search flags show banner and make no lifecycle writes', () => {
  const {spawnSync}=require('node:child_process')
  const home=fs.mkdtempSync(path.join(os.tmpdir(),'prism-unsupported-'))
  const result=spawnSync(process.execPath,['-e',`Object.defineProperty(process,'platform',{value:'linux'}); require('./lib/tokens').runDashboard(['--disable-content-search'])`],
    {cwd:path.resolve(__dirname,'..'),env:{...process.env,HOME:home,USERPROFILE:home},encoding:'utf8'})
  assert.equal(result.status,0,result.stderr)
  assert.match(result.stderr,/Background token economics is coming soon/)
  assert.equal(fs.existsSync(path.join(home,'.100xprism')),false)
})
test('first new-runtime failure rolls back only after preceding schema validation', () => {
  const {source,api}=realFixture(); const first=api.install(source)
  fs.writeFileSync(path.join(source,'scripts','fail-runtime'),'')
  assert.throws(()=>api.update(source),/failed/)
  assert.equal(api.active(),first.generation)
})
test('failed search maintenance remains fenced and resumes launchd in finally', () => {
  const {source,api,calls}=realFixture(); api.install(source)
  assert.throws(()=>api.contentSearch(false),/failed/)
  assert.ok(fs.existsSync(path.join(api.root,'content-search-disabled')))
  assert.ok(calls.some(c=>c.command==='launchctl'&&c.args[0]==='bootout'))
  const last=calls.filter(c=>c.command==='launchctl').at(-1)
  assert.equal(last.args[0],'kickstart')
})
test('real git dirty-check rejects untracked files while preserving ignored files', () => {
  const {spawnSync}=require('node:child_process')
  const home=fs.mkdtempSync(path.join(os.tmpdir(),'prism-clean-clone-'))
  const clone=path.join(home,'clone'); fs.mkdirSync(clone)
  assert.equal(spawnSync('git',['init',clone],{stdio:'ignore'}).status,0)
  fs.writeFileSync(path.join(clone,'.git','info','exclude'),'ignored.bin\n')
  fs.writeFileSync(path.join(clone,'ignored.bin'),'user data')
  const api=economics.createLifecycle({home,platform:'darwin'})
  api.assertClean(clone)
  fs.writeFileSync(path.join(clone,'notes'),'local untracked')
  assert.throws(()=>api.assertClean(clone),/tracked or untracked/)
  assert.equal(fs.readFileSync(path.join(clone,'ignored.bin'),'utf8'),'user data')
  assert.equal(fs.readFileSync(path.join(clone,'notes'),'utf8'),'local untracked')
})
test('manifest registration uses resolved interpreter with an empty shell PATH', () => {
  const {spawnSync}=require('node:child_process')
  const interpreter=spawnSync('python3',['-c','import sys; print(sys.executable)'],{encoding:'utf8'}).stdout.trim()
  const home=fs.mkdtempSync(path.join(os.tmpdir(),'prism-python-path-'))
  const api=economics.createLifecycle({home,platform:'darwin',python:interpreter})
  api.initialize()
  const previous=process.env.PATH
  try {
    process.env.PATH=''
    assert.equal(api.python(),interpreter)
    const artifact=path.join(api.root,'registered-before-creation')
    api.register(artifact)
    assert.ok(api.readManifest().artifacts.some(a=>a.path===artifact))
  } finally {process.env.PATH=previous}
})
test('purge removes a registered empty database left by interrupted creation', () => {
  const {source,api}=realFixture(); const generation=api.prepare(source)
  fs.writeFileSync(path.join(generation,'token-economics.sqlite3'),'')
  api.purge()
  assert.equal(fs.existsSync(api.root),false)
  assert.equal(fs.existsSync(api.manifest),false)
})
test('registered private partial manifest temp recovers after interrupted writes', () => {
  const {api}=fixture(); api.initialize()
  const temp=path.join(api.root,'manifest-next.json')
  fs.writeFileSync(temp,'{"schema":',{mode:0o600})
  const target=path.join(api.root,'next-registration')
  api.register(target)
  assert.ok(api.readManifest().artifacts.some(a=>a.path===target))
  assert.equal(fs.existsSync(temp),false)
})
test('manifest mutation refuses unregistered or public temporary artifacts', () => {
  const {api}=fixture(); api.initialize()
  const temp=path.join(api.root,'manifest-next.json')
  fs.writeFileSync(temp,'foreign',{mode:0o644})
  assert.throws(()=>api.register(path.join(api.root,'new-file')),/not private/)
  assert.equal(fs.readFileSync(temp,'utf8'),'foreign')
  const m=api.readManifest();m.artifacts=m.artifacts.filter(a=>a.path!==temp)
  fs.writeFileSync(api.manifest,JSON.stringify(m)); fs.chmodSync(temp,0o600)
  assert.throws(()=>api.register(path.join(api.root,'new-file')),/Unregistered/)
  assert.equal(fs.readFileSync(temp,'utf8'),'foreign')
})
