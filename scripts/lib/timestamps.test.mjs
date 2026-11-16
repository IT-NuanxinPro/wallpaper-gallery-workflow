import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { parseTimestamp, reconcileTimestamps, releaseStatistics } from './timestamps.mjs'

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wallpaper-stats-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  for (const series of ['desktop', 'mobile', 'avatar']) fs.mkdirSync(path.join(root, 'wallpaper', series), { recursive: true })
  const file = name => { const target = path.join(root, 'wallpaper', name); fs.mkdirSync(path.dirname(target), { recursive: true }); fs.writeFileSync(target, name) }
  const git = (...args) => execFileSync('git', args, { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()
  return { root, file, git }
}

test('timestamp parser preserves pipes, brackets and Chinese spaces', () => {
  assert.deepEqual(parseTimestamp('mobile|人像/图 || [123].jpg|100|v1.0.1'), { series:'mobile', relativePath:'人像/图 || [123].jpg', timestamp:100, tag:'v1.0.1' })
  assert.equal(parseTimestamp('mobile|bad|oops|v1'), null)
  assert.deepEqual(parseTimestamp('mobile|人像/旧||图.jpg|100'), {series:'mobile',relativePath:'人像/旧||图.jpg',timestamp:100,tag:''})
  assert.deepEqual(parseTimestamp('旧图.jpg|100'), {series:'desktop',relativePath:'旧图.jpg',timestamp:100,tag:''})
})

test('repair removes duplicates preserving earliest original tag and is idempotent', t => {
  const {root,file} = fixture(t)
  file('mobile/人像/图||..._0.jpg'); file('desktop/动漫/角色 [123].png')
  fs.writeFileSync(path.join(root,'timestamps-backup-all.txt'), [
    'mobile|人像/图||..._0.jpg|100|v1.0.1',
    'desktop|动漫/角色 [123].png|200|v1.0.2',
    'desktop|动漫/角色 [123].png|100|v1.0.1',
    'desktop|不存在.jpg|100|v1.0.1',
    'video|remote.mp4|100|v1.0.1'
  ].join('\n')+'\n')
  const first=reconcileTimestamps(root,{tag:'v1.0.3',fix:true})
  assert.equal(first.duplicates,1); assert.equal(first.orphans,1)
  const before=fs.readFileSync(path.join(root,'timestamps-backup-all.txt'),'utf8')
  assert.match(before,/角色 \[123\]\.png\|100\|v1\.0\.1/)
  assert.match(before,/video\|remote.mp4/)
  assert.equal(reconcileTimestamps(root,{tag:'v1.0.4',fix:true}).changed,false)
  assert.equal(fs.readFileSync(path.join(root,'timestamps-backup-all.txt'),'utf8'),before)
})

test('release increments use unique added Git paths, never repaired timestamp tags or rename paths', t => {
 const {root,file,git}=fixture(t)
 git('init','-q'); git('config','user.email','test@example.test');git('config','user.name','Test')
 file('mobile/旧图||.jpg');file('desktop/旧 [123].png')
 git('add','.');git('commit','-qm','base');git('tag','v1.0.1')
 git('mv','wallpaper/desktop/旧 [123].png','wallpaper/desktop/新名字.png')
 file('desktop/新图.jpg');file('desktop/new.webp')
 git('add','.');git('commit','-qm','new');git('tag','v1.0.2')
 fs.writeFileSync(path.join(root,'timestamps-backup-all.txt'),'mobile|旧图||.jpg|500|v1.0.2\nmobile|旧图||.jpg|500|v1.0.2\n')
 const stats=releaseStatistics(root,'v1.0.2')
 assert.equal(stats.previousTag,'v1.0.1')
 assert.deepEqual(stats.total,{desktop:3,mobile:1,avatar:0})
 assert.deepEqual(stats.added,{desktop:2,mobile:0,avatar:0})
 assert.deepEqual(releaseStatistics(root,'v1.0.2'),stats)
})

test('update shell accepts relative project root and repeated execution never retags old images', t => {
 const {root,file}=fixture(t)
 file('mobile/旧图||.jpg');file('desktop/角色 [123].png')
 fs.writeFileSync(path.join(root,'timestamps-backup-all.txt'),'mobile|旧图||.jpg|100|v1.0.1\ndesktop|角色 [123].png|100|v1.0.1\n')
 const script=path.resolve('scripts/update-timestamps.sh')
 execFileSync('bash',[script,path.basename(root),'v1.0.2'],{cwd:path.dirname(root),env:{...process.env,NEW_TAG_FILE:path.join(root,'new-tag.txt'),FAILED_IMAGES_FILE:path.join(root,'none')}})
 execFileSync('bash',[script,path.basename(root),'v1.0.3'],{cwd:path.dirname(root),env:{...process.env,NEW_TAG_FILE:path.join(root,'new-tag.txt'),FAILED_IMAGES_FILE:path.join(root,'none')}})
 assert.equal(fs.readFileSync(path.join(root,'timestamps-backup-all.txt'),'utf8').split('\n').filter(Boolean).length,2)
 assert.ok(!fs.readFileSync(path.join(root,'timestamps-backup-all.txt'),'utf8').includes('v1.0.3'))
})

test('verify shell repairs pipe paths and duplicates without classifying old images as new', t => {
  const {root,file}=fixture(t)
  file('mobile/人像/旧||图.jpg');file('desktop/角色 [1].png')
  fs.writeFileSync(path.join(root,'timestamps-backup-all.txt'), 'mobile|人像/旧||图.jpg|100|v1.0.1\ndesktop|角色 [1].png|200|v1.0.2\ndesktop|角色 [1].png|100|v1.0.1\n')
  const script=path.resolve('scripts/verify-timestamps.sh')
  execFileSync('bash',[script,root,'--fix','v1.0.3'],{env:{...process.env,FAILED_IMAGES_FILE:path.join(root,'none')}})
  const lines=fs.readFileSync(path.join(root,'timestamps-backup-all.txt'),'utf8').trim().split('\n')
  assert.equal(lines.length,2)
  assert.ok(lines.every(line=>line.endsWith('|v1.0.1')))
  execFileSync('bash',[script,root,'','v1.0.4'],{env:{...process.env,FAILED_IMAGES_FILE:path.join(root,'none')}})
})

test('repair skips known failed images and refuses malformed records without writing', t => {
 const {root,file}=fixture(t)
 file('desktop/成功.jpg');file('mobile/失败.jpg')
 const target=path.join(root,'timestamps-backup-all.txt')
 reconcileTimestamps(root,{tag:'v1.0.1',fix:true,failedPaths:new Set(['wallpaper/mobile/失败.jpg'])})
 assert.match(fs.readFileSync(target,'utf8'), /desktop\|成功/)
 assert.doesNotMatch(fs.readFileSync(target,'utf8'), /失败/)
 fs.writeFileSync(target,'invalid record\n')
 assert.throws(()=>reconcileTimestamps(root,{tag:'v1.0.2',fix:true}),/无法解析/)
 assert.equal(fs.readFileSync(target,'utf8'),'invalid record\n')
})

test('publish retry upserts stats tag and edits existing release without touching attachments', t => {
 const {root}=fixture(t)
 const stats=path.join(root,'stats.json')
 fs.writeFileSync(stats,JSON.stringify({total:{video:72,bing:2729},releases:[{tag:'v1.0.2',added:{mobile:8,bing:1},custom:'preserved'},{tag:'v1.0.1',added:{desktop:1}}]}))
 const script=path.resolve('scripts/publish-release.sh')
 execFileSync('bash',['-c','source "$1"; update_stats "$2" v1.0.2 12 943 658 1 0 0 2026-11-16 tester; update_stats "$2" v1.0.2 12 943 658 1 0 0 2026-11-16 tester','test',script,stats])
 const result=JSON.parse(fs.readFileSync(stats,'utf8'))
 assert.equal(result.releases.filter(r=>r.tag==='v1.0.2').length,1)
 assert.equal(result.releases[0].added.mobile,0)
 assert.equal(result.releases[0].added.bing,1)
 assert.equal(result.releases[0].custom,'preserved')
 assert.equal(result.total.video,72);assert.equal(result.total.bing,2729)
 const bin=path.join(root,'bin');fs.mkdirSync(bin)
 const log=path.join(root,'gh.log')
 fs.writeFileSync(path.join(bin,'gh'),'#!/bin/bash\nprintf "%s\\n" "$*" >> "$GH_TEST_LOG"\nif [ "$1 $2" = "release view" ]; then exit "${GH_VIEW_STATUS:-0}"; fi\n',{mode:0o755})
 for(const status of ['0','1'])execFileSync('bash',['-c','source "$1"; publish_github_release v1.0.2 "test body"','test',script],{env:{...process.env,PATH:bin+':'+process.env.PATH,GH_TEST_LOG:log,GH_VIEW_STATUS:status}})
 const lines=fs.readFileSync(log,'utf8')
 assert.match(lines,/release edit v1.0.2 --title v1.0.2 --notes test body --latest/)
 assert.match(lines,/release create v1.0.2 --title v1.0.2 --notes test body --latest/)
 assert.doesNotMatch(lines,/delete|upload/)
})

test('integrity and rollback preserve complete pipe paths', t => {
 const {root,file,git}=fixture(t)
 file('mobile/人像/目标||图 [12].jpg');file('mobile/人像/保留||图.jpg')
 fs.writeFileSync(path.join(root,'timestamps-backup-all.txt'),'mobile|人像/目标||图 [12].jpg|200|v1.0.2\nmobile|人像/保留||图.jpg|100|v1.0.1\n')
 fs.writeFileSync(path.join(root,'stats.json'),JSON.stringify({total:{video:72},releases:[{tag:'v1.0.2',added:{mobile:1}},{tag:'v1.0.1',added:{mobile:1}}]}))
 const integrity=path.resolve('scripts/verify-integrity.sh')
 execFileSync('bash',['-c','source "$1"; check_timestamps_consistency "$2" false; test "$TIMESTAMP_ISSUES" -eq 0','test',integrity,root])
 git('init','-q');git('config','user.email','test@example.test');git('config','user.name','Test');git('add','.');git('commit','-qm','base');git('tag','v1.0.1');git('tag','v1.0.2')
 const gitPath=execFileSync('which',['git'],{encoding:'utf8'}).trim()
 const bin=path.join(root,'stub-bin');fs.mkdirSync(bin)
 fs.writeFileSync(path.join(bin,'git'),'#!/bin/bash\nif [ "$1" = fetch ] || [ "$1" = push ]; then exit 0; fi\nexec "$REAL_GIT" "$@"\n',{mode:0o755})
 fs.writeFileSync(path.join(bin,'gh'),'#!/bin/bash\nexit 0\n',{mode:0o755})
 execFileSync('bash',[path.resolve('scripts/rollback.sh'),root,'v1.0.2'],{env:{...process.env,PATH:bin+':'+process.env.PATH,REAL_GIT:gitPath,ROLLBACK_RESULT_FILE:path.join(root,'result.txt')}})
 assert.equal(fs.existsSync(path.join(root,'wallpaper/mobile/人像/目标||图 [12].jpg')),false)
 assert.equal(fs.existsSync(path.join(root,'wallpaper/mobile/人像/保留||图.jpg')),true)
 assert.equal(fs.readFileSync(path.join(root,'timestamps-backup-all.txt'),'utf8'),'mobile|人像/保留||图.jpg|100|v1.0.1\n')
 assert.equal(JSON.parse(fs.readFileSync(path.join(root,'stats.json'),'utf8')).total.video,72)
})

test('CLI resolves symlinked script locations and emits statistics', t => {
 const {root,file,git}=fixture(t)
 git('init','-q');git('config','user.email','test@example.test');git('config','user.name','Test')
 file('mobile/一张.jpg');git('add','.');git('commit','-qm','first');git('tag','v1.0.1')
 const link=path.join(root,'helper.mjs')
 fs.symlinkSync(path.resolve('scripts/lib/timestamps.mjs'),link)
 const result=JSON.parse(execFileSync(process.execPath,[link,'stats',root,'v1.0.1'],{encoding:'utf8'}))
 assert.equal(result.total.mobile,1)
})

test('invalid or empty statistics stops publish before any stats mutation or release command', t => {
 const {root}=fixture(t)
 const scripts=path.join(root,'scripts');const lib=path.join(scripts,'lib');fs.mkdirSync(lib,{recursive:true})
 fs.copyFileSync(path.resolve('scripts/publish-release.sh'),path.join(scripts,'publish-release.sh'))
 const tempTag=path.join(root,'tag.txt');fs.writeFileSync(tempTag,'v1.0.2')
 let script=fs.readFileSync(path.join(scripts,'publish-release.sh'),'utf8').replaceAll('/tmp/new_tag.txt',tempTag)
 fs.writeFileSync(path.join(scripts,'publish-release.sh'),script)
 const stats=path.join(root,'stats.json');fs.writeFileSync(stats,'{"total":{"mobile":943},"releases":[]}')
 for(const output of ['', 'console.log(JSON.stringify({total:{desktop:1,mobile:943,avatar:0},added:{desktop:1,mobile:"8",avatar:0}}))']) {
   fs.writeFileSync(path.join(lib,'timestamps.mjs'),output)
   assert.throws(()=>execFileSync('bash',[path.join(scripts,'publish-release.sh'),root],{stdio:'pipe'}))
   assert.equal(fs.readFileSync(stats,'utf8'),'{"total":{"mobile":943},"releases":[]}')
 }
})


test('untagged historical records remain untagged through normalization', t => {
 const {root,file}=fixture(t)
 file('desktop/旧图.jpg');file('mobile/旧||图.jpg')
 fs.writeFileSync(path.join(root,'timestamps-backup-all.txt'),'旧图.jpg|100\nmobile|旧||图.jpg|100\n')
 reconcileTimestamps(root,{tag:'v1.0.9',fix:true})
 const data=fs.readFileSync(path.join(root,'timestamps-backup-all.txt'),'utf8')
 assert.equal(data,'desktop|旧图.jpg|100\nmobile|旧||图.jpg|100\n')
 assert.equal(reconcileTimestamps(root,{tag:'v1.0.10',fix:true}).changed,false)
})
