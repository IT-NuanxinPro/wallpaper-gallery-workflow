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

> 注：JSON 数据文件由前端部署时自动生成，工作流不再负责生成

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

## License

MIT
