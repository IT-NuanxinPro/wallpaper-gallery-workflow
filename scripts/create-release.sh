#!/bin/bash
# ========================================
# 创建 Tag 和 Release 脚本
# ========================================
#
# 功能：自动递增版本号，创建 tag 和 GitHub Release
#       复用 nuanXinProPic/scripts/release.sh 的逻辑
#
# 用法：
#   ./scripts/create-release.sh <图床仓库路径> [提交信息]
#
# 环境变量：
#   GH_TOKEN - GitHub Token（用于创建 Release）
#
# ========================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

main() {
    local project_root="${1:-.}"
    local commit_msg="${2:-chore: update wallpapers [$(TZ='Asia/Shanghai' date +'%Y-%m-%d')]}"

    cd "$project_root"

    # 检查是否有更改
    if [ -z "$(git status --porcelain)" ]; then
        echo -e "${YELLOW}没有检测到更改，无需发布${NC}"
        exit 0
    fi

    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}创建 Tag 和 Release${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""

    # 统计壁纸数量
    local timestamp_file="timestamps-backup-all.txt"
    local desktop_count=$(grep '^desktop|' "$timestamp_file" 2>/dev/null | wc -l | tr -d ' ')
    local mobile_count=$(grep '^mobile|' "$timestamp_file" 2>/dev/null | wc -l | tr -d ' ')
    local avatar_count=$(grep '^avatar|' "$timestamp_file" 2>/dev/null | wc -l | tr -d ' ')

    echo -e "📊 壁纸统计:"
    echo -e "  🖥️  Desktop: ${GREEN}${desktop_count}${NC}"
    echo -e "  📱 Mobile: ${GREEN}${mobile_count}${NC}"
    echo -e "  👤 Avatar: ${GREEN}${avatar_count}${NC}"
    echo ""

    # 获取最新 tag 并计算新版本号
    git fetch --tags --quiet 2>/dev/null || true
    local latest_tag=$(git tag -l 'v*' --sort=-version:refname | head -1)

    if [ -z "$latest_tag" ]; then
        local new_tag="v1.0.1"
    else
        local version=${latest_tag#v}
        IFS='.' read -r major minor patch <<< "$version"
        local new_patch=$((patch + 1))
        local new_tag="v${major}.${minor}.${new_patch}"
    fi

    echo -e "📦 版本号: ${latest_tag:-无} → ${GREEN}${new_tag}${NC}"
    echo ""

    # 配置 git
    git config user.name "github-actions[bot]"
    git config user.email "github-actions[bot]@users.noreply.github.com"

    # 提交更改
    echo -e "${BLUE}📥 提交更改...${NC}"
    git add .
    git commit -m "$commit_msg"

    # 创建 tag
    echo -e "${BLUE}🏷️  创建 tag: ${new_tag}${NC}"
    git tag -a "$new_tag" -m "Release $new_tag - $(TZ='Asia/Shanghai' date +'%Y-%m-%d')"

    # 推送
    echo -e "${BLUE}🚀 推送到远程...${NC}"
    git push
    git push origin "$new_tag"

    # 创建 GitHub Release（如果有 gh 或 GH_TOKEN）
    if command -v gh &>/dev/null || [ -n "$GH_TOKEN" ]; then
        echo -e "${BLUE}📦 创建 GitHub Release...${NC}"

        local today=$(TZ='Asia/Shanghai' date +'%Y-%m-%d')
        local body="## 📅 壁纸同步 - $today

### 📊 统计
| 系列 | 总数 |
|------|------|
| 🖥️ Desktop | $desktop_count |
| 📱 Mobile | $mobile_count |
| 👤 Avatar | $avatar_count |

### 📝 提交信息
\`\`\`
$commit_msg
\`\`\`

---
*自动发布 by GitHub Actions*"

        gh release create "$new_tag" \
            --title "🎨 壁纸同步 - $today ($new_tag)" \
            --notes "$body" \
            --latest

        echo -e "${GREEN}✅ Release 创建成功${NC}"
    else
        echo -e "${YELLOW}⚠️  跳过 Release 创建（未配置 gh CLI 或 GH_TOKEN）${NC}"
    fi

    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}✅ 发布成功!${NC}"
    echo -e "${GREEN}   标签: ${new_tag}${NC}"
    echo -e "${GREEN}========================================${NC}"

    # 输出新 tag 供后续使用
    echo "$new_tag" > /tmp/new_tag.txt
}

main "$@"
