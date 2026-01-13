#!/bin/bash
# ========================================
# 初始化 stats.json 脚本
# ========================================
#
# 功能：统计现有所有图片，生成初始 stats.json
#       包含历史 tag 的统计信息
#
# 用法：
#   ./scripts/init-stats.sh <图床仓库路径>
#
# ========================================

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

main() {
    local project_root="${1:-.}"
    cd "$project_root"

    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}初始化 stats.json${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""

    local timestamp_file="timestamps-backup-all.txt"
    local stats_file="stats.json"

    # 统计当前总数
    local desktop_count=$(grep '^desktop|' "$timestamp_file" 2>/dev/null | wc -l | tr -d ' ')
    local mobile_count=$(grep '^mobile|' "$timestamp_file" 2>/dev/null | wc -l | tr -d ' ')
    local avatar_count=$(grep '^avatar|' "$timestamp_file" 2>/dev/null | wc -l | tr -d ' ')

    echo -e "📊 当前统计:"
    echo -e "  🖥️  Desktop: ${GREEN}${desktop_count}${NC}"
    echo -e "  📱 Mobile: ${GREEN}${mobile_count}${NC}"
    echo -e "  👤 Avatar: ${GREEN}${avatar_count}${NC}"
    echo ""

    # 获取所有 tag 并生成历史记录
    git fetch --tags --quiet 2>/dev/null || true
    local tags=$(git tag -l 'v*' --sort=-version:refname | head -20)

    # 开始构建 JSON
    local releases_json="["
    local first=true
    local prev_desktop=$desktop_count
    local prev_mobile=$mobile_count
    local prev_avatar=$avatar_count

    for tag in $tags; do
        # 获取该 tag 时的统计（从时间戳文件中按 tag 过滤）
        local tag_desktop=$(grep "|$tag$" "$timestamp_file" 2>/dev/null | grep '^desktop|' | wc -l | tr -d ' ')
        local tag_mobile=$(grep "|$tag$" "$timestamp_file" 2>/dev/null | grep '^mobile|' | wc -l | tr -d ' ')
        local tag_avatar=$(grep "|$tag$" "$timestamp_file" 2>/dev/null | grep '^avatar|' | wc -l | tr -d ' ')

        # 获取 tag 日期
        local tag_date=$(git log -1 --format=%ci "$tag" 2>/dev/null | cut -d' ' -f1)

        # 计算增量（本次 tag 新增的数量）
        local added_desktop=$tag_desktop
        local added_mobile=$tag_mobile
        local added_avatar=$tag_avatar

        if [ "$first" = true ]; then
            first=false
        else
            releases_json+=","
        fi

        releases_json+="
    {
      \"tag\": \"$tag\",
      \"date\": \"$tag_date\",
      \"added\": { \"desktop\": $added_desktop, \"mobile\": $added_mobile, \"avatar\": $added_avatar }
    }"

        echo -e "  📦 $tag ($tag_date): +$added_desktop / +$added_mobile / +$added_avatar"
    done

    releases_json+="
  ]"

    # 生成完整 JSON
    cat > "$stats_file" << EOF
{
  "total": {
    "desktop": $desktop_count,
    "mobile": $mobile_count,
    "avatar": $avatar_count
  },
  "lastUpdated": "$(TZ='Asia/Shanghai' date -Iseconds)",
  "releases": $releases_json
}
EOF

    echo ""
    echo -e "${GREEN}✅ stats.json 已生成${NC}"
    echo ""

    # 显示文件内容
    echo -e "${BLUE}文件内容:${NC}"
    cat "$stats_file"
}

main "$@"
