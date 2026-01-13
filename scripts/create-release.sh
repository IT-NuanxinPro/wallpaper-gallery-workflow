#!/bin/bash
# ========================================
# 创建 Tag 和 Release 脚本
# ========================================
#
# 功能：自动递增版本号，创建 tag 和 GitHub Release
#       更新 stats.json 统计文件
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

# 更新 stats.json
update_stats() {
    local stats_file="$1"
    local new_tag="$2"
    local desktop_count="$3"
    local mobile_count="$4"
    local avatar_count="$5"
    local added_desktop="$6"
    local added_mobile="$7"
    local added_avatar="$8"
    local today="$9"

    # 如果 stats.json 不存在，创建初始结构
    if [ ! -f "$stats_file" ]; then
        echo '{"total":{},"releases":[]}' > "$stats_file"
    fi

    # 使用 jq 更新（如果可用），否则用 node
    if command -v jq &>/dev/null; then
        local new_release="{\"tag\":\"$new_tag\",\"date\":\"$today\",\"added\":{\"desktop\":$added_desktop,\"mobile\":$added_mobile,\"avatar\":$added_avatar}}"
        
        jq --argjson release "$new_release" \
           --argjson desktop "$desktop_count" \
           --argjson mobile "$mobile_count" \
           --argjson avatar "$avatar_count" \
           '.total = {"desktop": $desktop, "mobile": $mobile, "avatar": $avatar} | .lastUpdated = now | .releases = [$release] + .releases' \
           "$stats_file" > "${stats_file}.tmp" && mv "${stats_file}.tmp" "$stats_file"
    elif command -v node &>/dev/null; then
        node -e "
const fs = require('fs');
const stats = JSON.parse(fs.readFileSync('$stats_file', 'utf8'));
stats.total = { desktop: $desktop_count, mobile: $mobile_count, avatar: $avatar_count };
stats.lastUpdated = new Date().toISOString();
stats.releases = [
  { tag: '$new_tag', date: '$today', added: { desktop: $added_desktop, mobile: $added_mobile, avatar: $added_avatar } },
  ...(stats.releases || [])
].slice(0, 50);
fs.writeFileSync('$stats_file', JSON.stringify(stats, null, 2));
"
    else
        echo -e "${YELLOW}⚠️  跳过 stats.json 更新（未找到 jq 或 node）${NC}"
        return
    fi

    echo -e "${GREEN}✅ stats.json 已更新${NC}"
}

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

    local timestamp_file="timestamps-backup-all.txt"
    local stats_file="stats.json"

    # 获取最新 tag
    git fetch --tags --quiet 2>/dev/null || true
    local latest_tag=$(git tag -l 'v*' --sort=-version:refname | head -1)

    # 统计当前壁纸总数
    local desktop_count=$(grep '^desktop|' "$timestamp_file" 2>/dev/null | wc -l | tr -d ' ')
    local mobile_count=$(grep '^mobile|' "$timestamp_file" 2>/dev/null | wc -l | tr -d ' ')
    local avatar_count=$(grep '^avatar|' "$timestamp_file" 2>/dev/null | wc -l | tr -d ' ')

    # 获取上次的统计（从 stats.json 或计算）
    local prev_desktop=0 prev_mobile=0 prev_avatar=0
    if [ -f "$stats_file" ] && command -v jq &>/dev/null; then
        prev_desktop=$(jq -r '.total.desktop // 0' "$stats_file")
        prev_mobile=$(jq -r '.total.mobile // 0' "$stats_file")
        prev_avatar=$(jq -r '.total.avatar // 0' "$stats_file")
    elif [ -n "$latest_tag" ]; then
        # 从时间戳文件计算（排除当前 tag 的）
        prev_desktop=$(grep '^desktop|' "$timestamp_file" 2>/dev/null | grep -v "|$latest_tag$" | wc -l | tr -d ' ')
        prev_mobile=$(grep '^mobile|' "$timestamp_file" 2>/dev/null | grep -v "|$latest_tag$" | wc -l | tr -d ' ')
        prev_avatar=$(grep '^avatar|' "$timestamp_file" 2>/dev/null | grep -v "|$latest_tag$" | wc -l | tr -d ' ')
    fi

    # 计算增量
    local added_desktop=$((desktop_count - prev_desktop))
    local added_mobile=$((mobile_count - prev_mobile))
    local added_avatar=$((avatar_count - prev_avatar))

    # 确保增量不为负数
    [ "$added_desktop" -lt 0 ] && added_desktop=0
    [ "$added_mobile" -lt 0 ] && added_mobile=0
    [ "$added_avatar" -lt 0 ] && added_avatar=0

    echo -e "📊 壁纸统计:"
    echo -e "  🖥️  Desktop: ${GREEN}${desktop_count}${NC} $([ $added_desktop -gt 0 ] && echo -e "(${GREEN}+${added_desktop}${NC})")"
    echo -e "  📱 Mobile: ${GREEN}${mobile_count}${NC} $([ $added_mobile -gt 0 ] && echo -e "(${GREEN}+${added_mobile}${NC})")"
    echo -e "  👤 Avatar: ${GREEN}${avatar_count}${NC} $([ $added_avatar -gt 0 ] && echo -e "(${GREEN}+${added_avatar}${NC})")"
    echo ""

    # 计算新版本号
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

    local today=$(TZ='Asia/Shanghai' date +'%Y-%m-%d')

    # 更新 stats.json
    update_stats "$stats_file" "$new_tag" "$desktop_count" "$mobile_count" "$avatar_count" \
                 "$added_desktop" "$added_mobile" "$added_avatar" "$today"

    # 配置 git
    git config user.name "github-actions[bot]"
    git config user.email "github-actions[bot]@users.noreply.github.com"

    # 提交更改（包括 stats.json）
    echo -e "${BLUE}📥 提交更改...${NC}"
    git add .
    git commit -m "$commit_msg"

    # 创建 tag
    echo -e "${BLUE}🏷️  创建 tag: ${new_tag}${NC}"
    git tag -a "$new_tag" -m "Release $new_tag - $today"

    # 推送
    echo -e "${BLUE}🚀 推送到远程...${NC}"
    git push
    git push origin "$new_tag"

    # 创建 GitHub Release
    if command -v gh &>/dev/null || [ -n "$GH_TOKEN" ]; then
        echo -e "${BLUE}📦 创建 GitHub Release...${NC}"

        # 构建增量显示
        local desktop_delta="" mobile_delta="" avatar_delta=""
        [ "$added_desktop" -gt 0 ] && desktop_delta="+$added_desktop" || desktop_delta="-"
        [ "$added_mobile" -gt 0 ] && mobile_delta="+$added_mobile" || mobile_delta="-"
        [ "$added_avatar" -gt 0 ] && avatar_delta="+$added_avatar" || avatar_delta="-"

        local body="## 📅 壁纸同步 - $today

### 📊 统计
| 系列 | 总数 | 本次增量 |
|------|------|----------|
| 🖥️ Desktop | $desktop_count | $desktop_delta |
| 📱 Mobile | $mobile_count | $mobile_delta |
| 👤 Avatar | $avatar_count | $avatar_delta |

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
