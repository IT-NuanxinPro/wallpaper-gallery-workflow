# 🔄 Wallpaper Gallery Workflow

自动化处理壁纸图床的 GitHub Actions 工作流仓库。

## 功能

- 🔍 自动检测 `nuanXinProPic` 仓库中新增的壁纸
- 🖼️ 自动生成缩略图（带水印）和预览图
- 🏷️ 自动创建版本 Tag 和 GitHub Release
- 📝 自动更新时间戳文件
- 📊 自动生成 JSON 数据文件

## 项目结构

```
wallpaper-gallery-workflow/
├── .github/workflows/
│   └── process-wallpapers.yml    # 工作流配置（只负责调度）
├── scripts/
│   ├── process-new-images.sh     # 处理新图片（生成缩略图/预览图）
│   ├── create-release.sh         # 创建 Tag 和 Release
│   └── update-timestamps.sh      # 更新时间戳文件
└── README.md
```

## 脚本说明

### process-new-images.sh

检测新增图片并生成缩略图/预览图，复用 `nuanXinProPic/scripts/local-process.sh` 的配置：

- 缩略图：350px 宽，带水印，WebP 格式
- 预览图：1920px 宽（mobile 1080px），无水印，WebP 格式

```bash
./scripts/process-new-images.sh <图床仓库路径>
```

### create-release.sh

自动递增版本号，创建 tag 和 GitHub Release：

```bash
./scripts/create-release.sh <图床仓库路径> [提交信息]
```

### update-timestamps.sh

为新增图片添加时间戳记录：

```bash
./scripts/update-timestamps.sh <图床仓库路径> [新tag]
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
  -d '{"event_type":"process-wallpapers","client_payload":{"message":"feat: 新增壁纸"}}'
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
上传管理系统 → 触发工作流 → 检测新图片 → 生成缩略图/预览图 → 创建 Tag/Release → 更新时间戳 → 生成数据文件
```

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
