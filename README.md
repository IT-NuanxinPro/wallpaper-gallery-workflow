# 🔄 Wallpaper Gallery Workflow

自动化处理壁纸图床的 GitHub Actions 工作流仓库。

## 功能

- 🔍 自动检测 `nuanXinProPic` 仓库中新增的壁纸
- 🖼️ 自动生成缩略图（带水印）和预览图
- 🏷️ 自动创建版本 Tag 和 GitHub Release
- 📝 自动更新时间戳和 metadata 文件
- 👤 记录发布者信息到 stats.json
- 🔒 并发控制和中断恢复机制
- ✅ 数据完整性验证

## 项目结构

```
wallpaper-gallery-workflow/
├── .github/workflows/
│   └── process-wallpapers.yml    # 工作流配置
├── scripts/
│   ├── process-new-images.sh     # 处理新图片（生成缩略图/预览图）
│   ├── create-tag.sh             # 创建 Tag 并推送
│   ├── update-timestamps.sh      # 更新时间戳文件
│   ├── verify-timestamps.sh      # 校验时间戳一致性
│   ├── verify-integrity.sh       # 数据完整性验证
│   ├── process-metadata.js       # 处理 metadata-pending
│   ├── publish-release.sh        # 更新 stats + 发布 Release
│   └── rollback.sh               # 回滚脚本
└── README.md
```

## 工作流执行顺序

```
1. process-new-images.sh   → 生成缩略图/预览图
        ↓
2. create-tag.sh           → 提交 + 创建 tag + 推送
        ↓
3. update-timestamps.sh    → 更新时间戳文件
        ↓
4. verify-timestamps.sh    → 校验时间戳一致性
        ↓
5. verify-integrity.sh     → 数据完整性验证
        ↓
6. process-metadata.js     → 处理 metadata-pending
        ↓
7. publish-release.sh      → 更新 stats + 发布 Release
```

## 脚本说明

### process-new-images.sh

检测新增图片并生成缩略图/预览图：

- 缩略图：350px 宽，带水印，WebP 格式
- 预览图：1920px 宽（mobile 1080px），无水印，WebP 格式
- 自动追踪处理失败的图片

```bash
./scripts/process-new-images.sh <图床仓库路径>
```

### create-tag.sh

提交更改并创建 tag，支持中断恢复：

```bash
./scripts/create-tag.sh <图床仓库路径> [提交信息]
```

### update-timestamps.sh

为新增图片添加时间戳记录，跳过处理失败的图片：

```bash
./scripts/update-timestamps.sh <图床仓库路径> [新tag]
```

### verify-integrity.sh

验证数据完整性（孤儿文件检测、metadata 一致性等）：

```bash
./scripts/verify-integrity.sh <图床仓库路径> [--fix]
```

### publish-release.sh

更新 stats.json 并创建 GitHub Release：

```bash
./scripts/publish-release.sh <图床仓库路径> [提交信息] [发布者]
```

## 触发方式

### 1. 手动触发

在 GitHub Actions 页面点击 "Run workflow"

### 2. API 触发（从上传管理系统调用）

```bash
curl -X POST \
  -H "Authorization: token YOUR_PAT_TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  https://api.github.com/repos/IT-NuanxinPro/wallpaper-gallery-workflow/dispatches \
  -d '{"event_type":"process-wallpapers","client_payload":{"message":"feat: 新增壁纸","publisher":"username"}}'
```

## 配置

### 必需的 Secrets

在仓库 Settings → Secrets and variables → Actions 中添加：

| Secret | 说明 |
|--------|------|
| `PAT_TOKEN` | GitHub Personal Access Token，需要 `repo` 权限 |

### 创建 PAT Token

1. 访问 https://github.com/settings/tokens
2. 点击 "Generate new token (classic)"
3. 勾选 `repo` 权限
4. 生成并复制 Token
5. 添加到本仓库的 Secrets 中

## 工作流程

```
上传管理系统 → 触发工作流(含发布者) → 检测新图片 → 生成缩略图/预览图 → 创建 Tag/Release → 更新时间戳 → 记录发布者
```

> 静态图片继续沿用现有 JSON 生成方式；视频采用下方独立流水线和增量 JSON 生成器。

## 本地测试

脚本可以在本地直接运行测试：

```bash
# 克隆图床仓库
git clone https://github.com/IT-NuanxinPro/nuanXinProPic.git

# 处理新图片
./scripts/process-new-images.sh nuanXinProPic

# 创建发布（需要 gh CLI）
./scripts/create-release.sh nuanXinProPic "feat: 新增壁纸"
```

## 相关项目

| 项目 | 说明 |
|------|------|
| [nuanXinProPic](https://github.com/IT-NuanxinPro/nuanXinProPic) | 壁纸图床仓库 |
| wallpaper-gallery | 前端展示网站 |
| wallpaper-gallery-upload | 上传管理系统 |

## 视频上传与发布

视频使用两个独立串行工作流，均从 Worker 持久队列领取租约，每 10 分钟补扫一次：

- `video-process.yml`：接收 `process-videos` dispatch，下载私有素材，生成兼容 MP4、完整静音预览、5 张候选封面与 480px WebP，验收后进入审核。
- `video-publish.yml`：接收 `publish-videos` dispatch，将冻结审核资源流式复制到正式桶并验收，从 GitHub 最新 HEAD 原子合并视频 JSON，显式镜像到 R2，等待 gallery 真实部署结果。

必须配置 `VIDEO_API_URL`（Actions variable 或 secret）、`VIDEO_SERVICE_TOKEN`、`REPO_TOKEN`；发布另需 `VIDEO_GALLERY_WORKFLOW` variable。图床/前端仓库及分支可通过 `VIDEO_DATA_REPOSITORY`、`VIDEO_DATA_BRANCH`、`VIDEO_GALLERY_REPOSITORY`、`VIDEO_GALLERY_REF` variables 覆盖。

gallery 接入须支持 `publication_id`、`minimum_data_ref`，使用 `video:<publication_id>:<minimum_data_ref>` run-name，并上传 `video-publication-result` artifact，记录真实数据 SHA 和 Pages deployment ID。未提供真实结果时发布不会标记成功。当前 gallery 接入留待用户前端提交后完成。

本地仅验收媒体或离线生成 JSON，不需要远程凭据：

```bash
PYTHONPATH="scripts" python3 -m unittest discover -s "scripts/video/tests" -v
PYTHONPATH="scripts" python3 -m video local "/path/to/input.mp4" "/path/to/output"
PYTHONPATH="scripts" python3 -m video catalog "/path/to/nuanXinProPic" "/path/to/offline-output"
```

运行环境为 Linux/macOS 的 Python 3.9+、FFmpeg/ffprobe；FFmpeg 没有 libwebp 时使用 `scripts/video/requirements.txt` 中的 Pillow。重跑通过私有 R2 checkpoint 校验并恢复产物，不使用 Actions cache 保存未审核媒体。HDR/PQ/HLG/Dolby Vision 明确拒绝，避免错误色彩转换。

完整实现、接口扩展、恢复策略及真实素材验证记录见 [视频实现报告](docs/video-implementation.md)。

## License

MIT
