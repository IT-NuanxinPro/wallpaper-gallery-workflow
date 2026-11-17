import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)
const {
  getDerivativeNames,
  processPendingFile,
  refreshProcessedAssetTags
} = require('../process-metadata.js')
const helper = path.resolve('scripts/lib/derivative-name.sh')

test('same-stem image formats get distinct derivative paths', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wallpaper-derivative-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  const sourceDir = path.join(root, 'wallpaper', 'mobile', '风景', '花卉')
  fs.mkdirSync(sourceDir, { recursive: true })
  for (const filename of ['1.jpg', '1.png', '夜空.jpg']) {
    fs.writeFileSync(path.join(sourceDir, filename), filename)
  }

  const shellName = filename => execFileSync('bash', [
    '-c', 'source "$1"; derivative_filename "$2"', 'test', helper, path.join(sourceDir, filename)
  ], { encoding: 'utf8' })
  assert.equal(shellName('1.jpg'), '1.jpg.webp')
  assert.equal(shellName('1.png'), '1.png.webp')
  assert.equal(shellName('夜空.jpg'), '夜空.webp')

  const paths = ['1.jpg', '1.png', '夜空.jpg'].map(name => `wallpaper/mobile/风景/花卉/${name}`)
  const names = getDerivativeNames(paths)
  assert.deepEqual(paths.map(image => names.get(image)), [
    '1.jpg.webp', '1.png.webp', '夜空.webp'
  ])

  for (const name of ['1.jpg.webp', '1.png.webp', '夜空.webp']) {
    const result = execFileSync('bash', [
      '-c', 'source "$1"; original_exists_for_derivative "$2" "$3"',
      'test', helper, sourceDir, name
    ])
    assert.equal(result.length, 0)
  }
})

test('pending metadata refreshes only selected asset tags and preserves AI', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wallpaper-metadata-'))
  const previousCwd = process.cwd()
  t.after(() => {
    process.chdir(previousCwd)
    fs.rmSync(root, { recursive: true, force: true })
  })
  process.chdir(root)
  const pendingPath = path.join(root, 'pending.json')
  const pngPath = 'wallpaper/mobile/风景/花卉/1.png'
  const jpgPath = 'wallpaper/mobile/风景/花卉/1.jpg'
  const otherPath = 'wallpaper/mobile/风景/花卉/夜空.jpg'
  const images = Object.fromEntries([pngPath, jpgPath, otherPath].map(relativePath => [
    relativePath, { cdnTag: 'v1.5.19', ai: { keywords: ['原有'], description: '原有描述' } }
  ]))
  fs.writeFileSync(pendingPath, JSON.stringify({ images: [
    { series: 'mobile', relativePath: pngPath, refreshAssets: true },
    { series: 'mobile', relativePath: jpgPath, refreshAssets: true, ai: {
      keywords: ['白玫瑰'], description: '夜色中手持白玫瑰', displayTitle: '夜色里的一束白玫瑰'
    } }
  ] }))

  assert.throws(() => processPendingFile(pendingPath, { mobile: { images } }, 'v1.5.20'),
    /待刷新资源缺少 thumbnail/)
  assert.equal(images[pngPath].cdnTag, 'v1.5.19')

  for (const kind of ['thumbnail', 'preview']) {
    const directory = path.join(root, kind, 'mobile', '风景', '花卉')
    fs.mkdirSync(directory, { recursive: true })
    for (const name of ['1.jpg.webp', '1.png.webp']) {
      fs.writeFileSync(path.join(directory, name), name)
    }
  }

  assert.equal(processPendingFile(pendingPath, { mobile: { images } }, 'v1.5.20'), 2)
  assert.equal(images[pngPath].cdnTag, 'v1.5.20')
  assert.deepEqual(images[pngPath].ai, { keywords: ['原有'], description: '原有描述' })
  assert.equal(images[jpgPath].cdnTag, 'v1.5.20')
  assert.equal(images[jpgPath].ai.displayTitle, '夜色里的一束白玫瑰')
  assert.equal(images[otherPath].cdnTag, 'v1.5.19')

  images[pngPath].cdnTag = 'v1.5.19'
  images[jpgPath].cdnTag = 'v1.5.19'
  assert.equal(refreshProcessedAssetTags({ mobile: { images } }, [pngPath, jpgPath], 'v1.5.20'), 2)
  assert.equal(images[pngPath].cdnTag, 'v1.5.20')
  assert.equal(images[jpgPath].cdnTag, 'v1.5.20')
  assert.equal(images[otherPath].cdnTag, 'v1.5.19')
})
