#!/bin/bash
# ========================================
# 发布 Release 脚本（最后一步）
# ========================================
#
# 功能：更新 stats.json，提交时间戳文件，发布 GitHub Release
#
# 前置条件：
#   - create-tag.sh 已执行（/tmp/new_tag.txt 存在）
#   - update-timestamps.sh 已执行（时间戳文件已更新）
#
# 用法：
#   ./scripts/publish-release.sh <图床仓库路径> [提交信息] [发布者]
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
    local publisher="${10}"

    # 如果 stats.json 不存在，创建初始结构
    if [ ! -f "$stats_file" ]; then
        echo '{"total":{},"releases":[]}' > "$stats_file"
    fi

    # 使用 jq 更新（如果可用），否则用 node
    if command -v jq &>/dev/null; then
        local new_release="{\"tag\":\"$new_tag\",\"date\":\"$today\",\"added\":{\"desktop\":$added_desktop,\"mobile\":$added_mobile,\"avatar\":$added_avatar},\"publisher\":\"$publisher\"}"
        
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
  { tag: '$new_tag', date: '$today', added: { desktop: $added_desktop, mobile: $added_mobile, avatar: $added_avatar }, publisher: '$publisher' },
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
    local commit_msg="${2:-chore: update stats}"
    local publisher="${3:-}"

    cd "$project_root"

    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}发布 Release${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""

    local timestamp_file="timestamps-backup-all.txt"
    local stats_file="stats.json"

    # 读取新 tag（由 create-tag.sh 生成）
    local new_tag=""
    if [ -f /tmp/new_tag.txt ]; then
        new_tag=$(cat /tmp/new_tag.txt)
    fi
    
    if [ -z "$new_tag" ]; then
        echo -e "${RED}错误: 未找到新 tag，请先运行 create-tag.sh${NC}"
        exit 1
    fi

    echo -e "📦 当前 Tag: ${GREEN}${new_tag}${NC}"
    echo ""

    # 统计当前壁纸总数
    local desktop_count=$(grep '^desktop|' "$timestamp_file" 2>/dev/null | wc -l | tr -d ' ')
    local mobile_count=$(grep '^mobile|' "$timestamp_file" 2>/dev/null | wc -l | tr -d ' ')
    local avatar_count=$(grep '^avatar|' "$timestamp_file" 2>/dev/null | wc -l | tr -d ' ')

    # 计算增量：统计带有新 tag 的记录数量
    local added_desktop=$(grep '^desktop|' "$timestamp_file" 2>/dev/null | grep "|${new_tag}$" | wc -l | tr -d ' ')
    local added_mobile=$(grep '^mobile|' "$timestamp_file" 2>/dev/null | grep "|${new_tag}$" | wc -l | tr -d ' ')
    local added_avatar=$(grep '^avatar|' "$timestamp_file" 2>/dev/null | grep "|${new_tag}$" | wc -l | tr -d ' ')

    echo -e "📊 壁纸统计:"
    echo -e "  🖥️  Desktop: ${GREEN}${desktop_count}${NC} $([ $added_desktop -gt 0 ] && echo -e "(${GREEN}+${added_desktop}${NC})")"
    echo -e "  📱 Mobile: ${GREEN}${mobile_count}${NC} $([ $added_mobile -gt 0 ] && echo -e "(${GREEN}+${added_mobile}${NC})")"
    echo -e "  👤 Avatar: ${GREEN}${avatar_count}${NC} $([ $added_avatar -gt 0 ] && echo -e "(${GREEN}+${added_avatar}${NC})")"
    echo ""

    local today=$(TZ='Asia/Shanghai' date +'%Y-%m-%d')

    # 更新 stats.json
    update_stats "$stats_file" "$new_tag" "$desktop_count" "$mobile_count" "$avatar_count" \
                 "$added_desktop" "$added_mobile" "$added_avatar" "$today" "$publisher"

    # 提交时间戳文件和 stats.json
    if [ -n "$(git status --porcelain)" ]; then
        echo -e "${BLUE}📥 提交统计文件...${NC}"
        git config user.name "github-actions[bot]"
        git config user.email "github-actions[bot]@users.noreply.github.com"
        git add .
        git commit -m "chore: update stats for $new_tag"
        git push
    fi

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
            --title "$new_tag" \
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
}

main "$@"
