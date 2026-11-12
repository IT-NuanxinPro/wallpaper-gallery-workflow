# 视频流水线实现与验收记录

日期：2026-11-12。接口权威为 Studio 的 `docs/video-ingest-contract.md`，本文记录工作流部分的具体实现及双方约定的补充。

## 实现范围

新增 `scripts/video/`、`.github/workflows/video-process.yml`、`.github/workflows/video-publish.yml`。未运行 Git 提交、推送、分支操作、远程部署或生产 API。原有静态脚本由另一分工维护，本实现未编辑这些脚本。

`media.py` 负责媒体处理和验收，`runner.py` 负责处理任务租约、心跳和断点恢复，`catalog.py` 只处理视频 JSON，`publish.py` 负责对象发布、GitHub 原子提交及部署追踪，`api.py` 封装不泄漏 URL/token 的 HTTP 传输，`__main__.py` 提供离线和 Actions 入口。没有新增 Node 依赖；Python 标准库以外只有可选的 Pillow WebP 编码依赖。

## 媒体处理

1. 源文件按任务声明的大小和 SHA-256 验收，源文件上限 250,000,000 字节。
2. H264、yuv420p、逐行视频优先 `-c:v copy`，其他 SDR 输入使用 libx264、CRF18、yuv420p；仅保留第一条视频和可选第一条音轨，AAC 音轨 copy，其他音轨转 AAC 192k。输出 MP4 `+faststart`，原分辨率和完整时长必须通过验收。
3. 完整预览长边最多 1920、不放大、尺寸取偶数、libx264 CRF24、静音、faststart。
4. 下载视频的 10%、30%、50%、70%、90% 各生成一张完整分辨率 JPEG。按亮度曝光、直方图熵、对比度和邻近像素差评分，最高分为默认封面；评分只是可解释的自动建议，最终由用户审核选择。
5. 每张候选帧生成长边最多 480 的 WebP，不放大。优先 FFmpeg libwebp；本机 FFmpeg 不含 libwebp，实际验证使用 Pillow/libwebp 备用编码。
6. MP4 验收检查 H264/yuv420p、显示尺寸、时长误差上限 `max(0.25秒, 0.5%)`、预览无音轨、moov 位于 mdat 前，并完整解码视频流以拒绝截断/损坏媒体。所有媒体产物保存实际字节数、SHA-256；缩略图另验收最长边。

HDR 策略是明确失败：PQ、HLG、Dolby Vision、mastering-display/content-light 元数据或无法证明 SDR 的 BT.2020 输入不会被静默降到 8bit。当前没有实施 HDR→SDR tone mapping，这属于契约允许的“正确映射或明确失败”中的后者。

单任务 POSIX 定时器限制 30 分钟，FFmpeg 子进程另有同一截止时间，超时会终止子进程并回调失败。目标运行环境为 Actions Linux 和本地 macOS；未承诺原生 Windows 信号兼容。

## 任务领取和重跑

- `claim` 使用 `kind: process` 和数字 `GITHUB_RUN_ID`；任务领取后立即 heartbeat，之后每 20 秒续租。源、产物 PUT 地址均由 Worker 临时签名。
- 源和产物只经过私有 R2 暂存；Actions dispatch 仅传任务唤醒提示，不传媒体或大 JSON。没有启用 GitHub Actions cache/artifact 来持久化未审核媒体。
- FFmpeg 完成后、产物上传前，先保存私有 checkpoint manifest。完整上传后 `/complete` 使 Worker 核验对象再转入 awaiting_ai。
- 同目录重跑根据 pipelineVersion、jobId、源 SHA256、全部产物大小/摘要判断能否复用；复用仍进行 ffprobe 和完整视频解码。
- 换 runner 后，Worker source 返回 checkpoint 以及已存在、HEAD 大小正确的私有产物 GET 签名；runner 下载后逐个核验 SHA256，并重建本地 result manifest。所有产物齐全才复用，缺少任何产物则重新处理。
- 每个 HTTP 非 2xx 都抛错。业务失败回调携带原租约和固定 stage；失败回调本身失败也会使 Actions 非零退出。日志不输出 token、签名 URL 或远端错误正文。

## 视频 JSON 和并发提交

实际读取图床后确认 `metadata/video.json` 和完整 `data/video.json` 均有 **61 条**历史视频。离线验证未检查本地媒体是否存在。

生成器取 metadata 与完整已发布视频列表的并集，保留历史稳定 ID、路径和所有现有条目字段；metadata 根字段、每条元数据未知字段、既有 JSON envelope 的未知字段均保留。新视频稳定 ID 为 `video-<jobId>`，正式路径按冻结 finalKeys 确认。路径冲突、身份冲突、路径遍历均报错。

输出包括 `metadata/video.json`、`data/video.json`（旧单文件）、`data/video/index.json`、`data/video/latest.json`（40条）、全部分类 JSON，以及 `stats.json`。沿用现有 base64 字符置换与反转的 `v1.` 编码，schema/envelope 保持旧消费者兼容。

实际 `stats.json` 的视频字段位于 `total.video`、`series.video`；只更新这两处及 lastUpdated，完整保留静态分类计数、历史 releases 和未知根字段。没有新增不兼容的顶层 `stats.video`。

正式媒体全部验收后，GitHub REST 读取最新分支 HEAD、完整视频相关 JSON 和 stats；构建新 blobs/tree/commit，一次非 force 更新 ref。遇 409/422 并发冲突重新读取 HEAD 并重新合并，最多 5 次。未修改静态 JSON、未重写整个仓库 tree、未 force push。发布重试先验证最新 HEAD 的 metadata 和完整视频数据仍含全部冻结审核字段，不能仅凭上次 metadataCommitSha 跳过验证。

GitHub 提交完成后，逐个显式 PUT `data/video.json`、`data/video/**` 到正式 R2 镜像；没有假设图床提交会自动同步对象桶。

## 真实部署结果约束

Gallery 部署接口已在用户提交 `62ed916` 后授权接入。只修改部署工作流、数据缓存版本和同步校验脚本，页面、路由与用户界面提交未修改；尚未触发真实部署。

```yaml
run-name: 'video:${{ inputs.publication_id }}:${{ inputs.minimum_data_ref }}'
on:
  workflow_dispatch:
    inputs:
      publication_id: { required: true, type: string }
      minimum_data_ref: { required: true, type: string }
```

部署 workflow 完成后必须上传名为 `video-publication-result` 的 artifact。ZIP 内必须且只能有一个不超过 1 MiB 的 JSON（建议名 `publication-result.json`）：

```json
{
  "publicationId": "冻结发布任务ID",
  "minimumDataRef": "本次要求至少包含的图床commit SHA",
  "dataSha": "实际部署读取的图床40位commit SHA",
  "pagesDeploymentId": "实际Cloudflare Pages部署ID"
}
```

发布器用唯一 run-name 找回实际 run，保存 `deploymentRunId` 供重试，只有 run completed/success 才下载 artifact。它校验 publicationId/minimumDataRef、actual dataSha 形状、Pages ID，并用图床 GitHub compare 验证 actual dataSha 包含最低要求 commit。`/complete` 的 `deploymentId` 来自真实 Pages ID，`dataSha` 来自 artifact；不把 GitHub run ID 或最低要求 SHA 冒充真实部署结果。成功 run 缺失 artifact、部署失败、数据 SHA 不包含最低要求都会失败，保留已完成对象和 JSON 阶段。

Artifact 的 GitHub token 仅用于 GitHub API；跨域临时下载地址用独立无 Authorization 请求，避免把仓库凭据带到对象存储地址。

## 配置和运行

| 配置 | 类型 | 说明 |
|---|---|---|
| VIDEO_API_URL | Actions variable 或 secret | Worker 基地址 |
| VIDEO_SERVICE_TOKEN | secret | Worker 内部接口共享服务令牌 |
| REPO_TOKEN | secret | 图床仓库读取/写入、gallery workflow 触发、run/artifact 读取权限 |
| VIDEO_DATA_REPOSITORY | variable | 默认 IT-NuanxinPro/nuanXinProPic |
| VIDEO_DATA_BRANCH | variable | 默认 main |
| VIDEO_GALLERY_REPOSITORY | variable | 默认 IT-NuanxinPro/wallpaper-gallery |
| VIDEO_GALLERY_WORKFLOW | variable，必填 | gallery部署 workflow 文件名/ID，此实现为 deploy.yml |
| VIDEO_GALLERY_REF | variable | 默认 main |

两个独立 concurrency group 都 `cancel-in-progress: false`。process 每 10 分钟、publish 错开 5 分钟补扫；分别接收 repository_dispatch `process-videos` / `publish-videos`，手动触发仅有可选 job_id / publication_id 小参数。Worker 持久队列决定实际领取顺序。

process 每次最多 8 条、单条 30 分钟，Actions job 上限 360 分钟。publish 每次 1 批，阶段总等待上限 90 分钟，Actions job 上限 100 分钟。未配置 gallery workflow 时在领取发布任务前明确失败，防止创建无法完成的发布。

本地执行：

```bash
PYTHONPATH="scripts" python3 -m unittest discover -s "scripts/video/tests" -v
PYTHONPATH="scripts" python3 -m video local "/path/to/source.mp4" "/path/to/output"
PYTHONPATH="scripts" python3 -m video catalog "/path/to/nuanXinProPic" "/path/to/output"
```

离线 catalog 强制输出到源图床仓库外，防止误覆盖真实 JSON。目录中 JSON 遍历仅用于读取完整已发布数据，不扫描本地媒体。

## 2026-11-12 验收证据

单元/集成测试覆盖 codec、61条历史对应的数据保留策略、metadata/published 并集、未知字段、路径安全、copy/CRF18、HDR 拒绝、封面评分、损坏产物、错误回调、超时、GitHub 乐观冲突、真实本机 HTTP 源下载和12产物 PUT、私有 checkpoint 换目录恢复、正式对象流式复制、R2 JSON 镜像、部署失败重试、成功 run 缺 artifact 拒绝成功。

最终完整验证：`PYTHONPATH="scripts" python3 -m unittest discover -s "scripts/video/tests" -v` **28项全部通过，6.569秒**。另有 `git diff --check`、`python3 -m compileall -q scripts/video`、Ruby YAML 解析两个新增 workflow 均通过。最终历史回读仍为61条且逐字段保留。排序兼容测试确认按日期降序、同日文件名升序。

真实媒体使用用户指定 `/Users/nuanxinpro/Downloads/发光-宝可梦.mp4`；原片未修改。产物在 Studio 的 `output/video-e2e-20261112/media/`：

| 项目 | 实测结果 |
|---|---|
| 源文件 | 13,654,428 bytes，3840×2160，H264/yuv420p/BT.709 |
| 下载 MP4 | 13,654,428 bytes，3840×2160，11.516667秒，faststart |
| 完整静音预览 | 2,326,423 bytes，1920×1080，11.516667秒，H264/yuv420p，faststart |
| 候选帧 | 5张完整尺寸 JPEG，时间点10/30/50/70/90% |
| 缩略图 | 5张480×270 WebP；默认frame-4缩略图15,460 bytes |
| 默认候选 | frame-4，评分130.728317 |
| 首次成功处理耗时 | 12.54秒（本机） |
| 校验后重跑 | 2.6秒，所有媒体修改时间保持一致，未重新编码 |

源与下载的编码视频 packet SHA256 均为 `79f24b773d1421ac886adba3dc7532125e88ce64c48239150bb37fc52b0d0320`，证明真实测试走视频 copy 分支。下载容器 SHA256 为 `a24b613ee1998e368d5839c92c3ee0a6fe06a47ead4151b1185c0d14b0f858e2`，预览为 `702ff5ece7b94fd9d350c3b1fc72e9874f723d0e9f3d6f9ed2fa1c6572f13395`。

同一输出根下还包含：

- `media/result.json`：完整结果及摘要 manifest。
- `media/local-validation.json`、`media/reuse-validation.json`：真实处理/复用证据。
- `catalog-baseline/`：历史61条离线生成，逐字段比较确认既有字段和非视频 stats 不变。
- `catalog-addition/`：只在离线输出中加入1条人工审核示例后共62条，历史61条所有字段和路径仍保留。
- `local-publication.json`、`catalog-validation.json`：离线示例与保留验证结果；不是 AI 分析结果，也不是已发布记录。

真实云端 Worker/R2/GitHub Actions/Pages 端到端尚未运行。所有 HTTP 集成测试只连接 `127.0.0.1` 临时服务器；GitHub 的并发和部署状态使用隔离的内存实现，不冒充真实远程验收。

主代理另完成真实工作台上传 → 本地 Worker/SQLite → R2 模拟对象存储 → 同一 process runner 的联调。已在 `queue-media/bc19e4c3-7e32-41da-a3b2-b04107a64467/result.json` 复核真实结果为3840×2160、11.516667秒、H264/yuv420p。这个验证比内存 API 单测更接近完整业务链路，但仍是本地联调，不代表云端 R2、Actions 或 Pages 验收。

## UI / Worker 对接审查

已只读检查 Studio 的 api/uploader/review-service/workspace 与 Worker 的 public/internal API：

- UI 业务请求使用 GitHub bearer，签名对象上传/下载不携带业务 token；分块 PUT 读取 ETag，服务端对8 MiB块号、大小、ETag和合并后大小核验。必须实际配置 R2 浏览器 CORS，允许工作台来源 GET/PUT，暴露 ETag，否则会得到界面明确错误。
- AI 分析携带领取后的 revision 和 leaseToken，记录当前 frameId；人工保存按 revision 防冲突，发布使用已保存 revision 冻结；发布错误在 UI 通过 publication 重试。前后端字段一致。
- 发现服务端通用 job retry 路径仅检查 failed 状态，尚需显式拒绝已有 publicationId 的任务，防止旧客户端绕过 UI 把发布失败任务重新排入处理队列。该发现已发给控制服务主代理补拒绝和测试，不在本工作流目录越界修改。
- 发布 runner 配置缺失在 claim 前失败；gallery 已在用户提交后接入，真实云端配置与部署仍需单独验收，不能通过省略artifact验收绕过。
- 正式桶 `/verify` 根据 HEAD 字节数/Content-Type验收；首次复制和源 checkpoint 恢复额外核对 SHA256。它不声称 R2 HEAD 提供端到端内容摘要。

独立复审另外复现并修复了两类阻断问题：

1. 新任务若与没有 jobId 的历史记录碰到同一正式路径，原逻辑会沿用旧ID却覆盖标题、SHA和元数据。现改为只有同一 jobId 且全部冻结审核/资产字段一致才允许幂等复用；历史路径或冻结字段冲突直接失败。发布器在申请正式对象 PUT 前读取最新HEAD并完整预检，因此拒绝冲突时没有对象上传；GitHub提交阶段仍再次校验。
2. 部署失败后重试 dispatch 新run，但GitHub列表存在可见性延迟时，原逻辑会重新选择旧失败run并立即失败。现以dispatch前已知最大run ID为界，只接收更新的同marker运行；延迟列表回归测试确认会等待新成功run。

本轮没有重复处理用户视频，仅运行短小的合成媒体回归与本机HTTP测试；未编辑gallery。

后续按主代理新指令补齐 metadata 的 `storage:'r2'`、明确 MP4 previewPath / WebP thumbnailPath；每批新增记录同时保存 publicationId 与 publicationJobCount。相同job的幂等重试也必须保持publication归属不变，旧61条的字段和路径继续逐项保留。metadata-only历史条目已有显式衍生资源路径时优先沿用，避免推导路径覆盖。

短视频另加真实0.1秒单帧回归：候选seek无图或编码失败时尝试中间/0秒兜底，JPEG明确使用全范围像素格式，按JPEG SHA256去重，保留至少1帧、最多5帧。用户原11秒素材未为该修复重新编码。

## Gallery数据部署接入（用户提交之后）

接入基线为 `/Users/nuanxinpro/frontProject/gihtub/wallpaper/wallpaper-gallery` 的 `62ed916`，提交时间2026-11-12 19:25:46 +0800。实际改动只包括：

- `.github/workflows/deploy.yml`：两项输入和唯一run-name；所有部署共享pages并发组，queue:max、cancel-in-progress:false；拿到执行权后checkout最新main，解析最新图床main；完整数据验收→测试→构建→资产同步→Pages→精确结果验收→成功artifact。
- `scripts/prepare-deploy-data.js` / `scripts/lib/deploymentSnapshot.js`：使用GitHub API固定最新HEAD，验证minimumRef为祖先；仅下载data/**、bing/meta/**和metadata/video.json的JSON，逐blob核对Git SHA1，拒绝truncated tree和缺失引用，无pnpm sync回退。先在临时目录完整验证，再替换干净public/data。
- `scripts/sync-wallpaper-assets.js`：所有外部查询/写入前校验完整五系列快照；视频发布upsert-only，不查询删除差集、不标removed；普通部署保留mark-missing默认行为。
- `scripts/record-pages-deployment.js`：Cloudflare API读取匹配本次前端SHA、production/main及唯一commit_message标记（图床SHA+GitHub run+attempt）的成功deploy，拒绝“最近一个部署”猜测；只有精确验收后写artifact所需JSON。
- `src/utils/config/constants.js`：DATA_REVISION使用VITE_DATA_REVISION，数据缓存key基于实际图床SHA；CDN_VERSION保留静态tag语义。
- `tests/deployment-snapshot.test.js`：19项独立边界测试。

完整性校验要求四普通系列index/category/latest/legacy的引用、数量、身份与每条记录内容一致（忽略对象键顺序）；Bing所有year引用和count/total/latest完整。publication条目数量必须等于metadata中的publicationJobCount，完整数据中的jobId/paths/sha/审核字段必须匹配metadata，避免分类文件仍残留旧预览或标题。

实际图床Bing历史含6条2019旧urlbase与4条同日多来源记录。本接入验证结构和完整计数，保留这些历史字段，不把部署校验扩成未经授权的数据清洗；现有详情生成器仍按既有行为跳过6条无效Bing详情。

验证记录：

- gallery `pnpm test`：**707通过、7项原有跳过，共71文件，4.75秒**；新19项均通过。
- gallery原目录 `VITE_DATA_REVISION=cccccccccccccccccccccccccccccccccccccccc pnpm build:seo`：成功，七页面prerender完成；只恢复该构建自动产生的public/version.json时间戳至已检查基线，未回滚任何用户修改。
- 为验证当前实际图床完整快照，在隔离副本 `/var/folders/wl/zsdv510907j78pw7tlpwwwn40000gn/T/gallery-video-build-s7dbwkjw` 放入完整图床JSON及bing/meta，关闭Supabase配置后 `pnpm build:seo` **exit0**；desktop955/mobile943/avatar658/video61/bing2725，七页面prerender完成。完整构建日志保存在该目录build.log，原仓public/data未改。
- 将真实用户素材已有结果与61条历史合并为隔离62视频publication后，gallery严格publication/分类/full/latest校验通过，计数955/943/658/**62**/2725；没有为此重新编码媒体。
- 新增/修改的gallery脚本ESLint无错误，deploy.yml YAML解析、两仓diff-check通过。

未调用生产Supabase资产写入、未调用Cloudflare部署、未提交/推送；Pages artifact解析和匹配通过mock测试，仍需真实云端阶段证明配置及运行效果。

## 相对初始契约的明确补充

1. 新增 `POST /internal/video/jobs/:id/checkpoint {leaseToken,result}`；source 响应可增加 `{checkpoint:{result,objects:[{key,url,size}]}}`，用于私有 R2 产物复用。
2. 冻结 publication.jobs 增加 `finalKeys:{download,preview,thumbnail}`；发布器再次核对它与冻结审核路径一致。
3. frames 增加 JPEG 的 `size/sha256/contentType`、缩略图 `thumbnailSize/thumbnailSha256`，供恢复和正式复制验收。
4. publication progress 增加 `deploymentRunId`，与最终 Pages `deploymentId` 分开保存。
5. gallery 增加唯一 run-name 和 `video-publication-result` artifact；minimum_data_ref 是最低包含版本，最终 dataSha 必须取实际部署结果。
6. 当前 stats 的“stats.video 定点合并”按既有结构落实为 `total.video` 与 `series.video`，保持原消费者兼容。

这些补充已与控制服务分工同步；没有悄悄替换原路径、旧视频身份或前端 JSON 编码。
