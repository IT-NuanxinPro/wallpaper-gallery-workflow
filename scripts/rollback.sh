#!/bin/bash
# ========================================
# 回滚脚本
# ========================================
#
# 功能：回滚用户上传的壁纸（不影响 Bing 每日同步）
#   1. 删除指定 tag 新增的原图、缩略图、预览图
#   2. 从 timestamps 文件中移除对应记录
#   3. 更新 stats.json 统计数据（仅 desktop/mobile/avatar）
#   4. 删除 tag 和 release
#
# 注意：
#   - 只回滚 wallpaper/thumbnail/preview 目录
#   - 不影响 bing/ 目录（Bing 每日同步独立管理）
#   - 如果该 tag 只有 Bing 更新，则只删除 tag/release
#
# 用法：
#   ./scripts/rollback.sh <图床仓库路径> [要回滚的tag]
#
# 如果不指定 tag，默认回滚最新的 tag
#
# 环境变量：
#   GH_TOKEN - GitHub Token（用于删除 Release）
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
    local target_tag="${2:-}"

    cd "$project_root"

    local timestamp_file="timestamps-backup-all.txt"
    local stats_file="stats.json"

    # 获取所有 tags
    git fetch --tags --quiet 2>/dev/null || true
    local all_tags=$(git tag -l 'v*' --sort=-version:refname)
    
    if [ -z "$all_tags" ]; then
        echo -e "${RED}❌ 没有找到任何 tag，无法回滚${NC}"
        exit 1
    fi

    local latest_tag=$(echo "$all_tags" | head -1)
    
    # 如果没有指定 tag，使用最新的
    if [ -z "$target_tag" ]; then
        target_tag="$latest_tag"
    fi

    # 验证 tag 存在
    if ! git tag -l | grep -q "^${target_tag}$"; then
        echo -e "${RED}❌ Tag ${target_tag} 不存在${NC}"
        exit 1
    fi

    # 获取上一个 tag（回滚后的最新版本）
    local previous_tag=$(echo "$all_tags" | grep -A1 "^${target_tag}$" | tail -1)
    if [ "$previous_tag" = "$target_tag" ]; then
        previous_tag=""
    fi

    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}回滚版本（仅用户上传的壁纸）${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
    echo -e "🎯 要回滚的版本: ${RED}${target_tag}${NC}"
    echo -e "📦 回滚后版本: ${GREEN}${previous_tag:-无}${NC}"
    echo -e "${YELLOW}⚠️  注意: 不影响 Bing 每日同步数据${NC}"
    echo ""

    # 1. 查找该 tag 新增的文件（只查 timestamps，不包括 bing）
    echo -e "${BLUE}📋 查找 ${target_tag} 新增的用户上传文件...${NC}"
    
    local files_to_delete=()
    local desktop_removed=0
    local mobile_removed=0
    local avatar_removed=0

    # timestamps 文件格式: series|rel_path|timestamp|tag
    while IFS='|' read -r series rel_path timestamp tag; do
        if [ "$tag" = "$target_tag" ]; then
            # 只处理 desktop/mobile/avatar，跳过其他
            case "$series" in
                desktop|mobile|avatar)
                    files_to_delete+=("$series|$rel_path")
                    case "$series" in
                        desktop) desktop_removed=$((desktop_removed + 1)) ;;
                        mobile) mobile_removed=$((mobile_removed + 1)) ;;
                        avatar) avatar_removed=$((avatar_removed + 1)) ;;
                    esac
                    ;;
            esac
        fi
    done < "$timestamp_file"

    local total_removed=$((desktop_removed + mobile_removed + avatar_removed))
    
    echo -e "  找到 ${YELLOW}${total_removed}${NC} 个文件需要删除:"
    echo -e "    🖥️  Desktop: ${desktop_removed}"
    echo -e "    📱 Mobile: ${mobile_removed}"
    echo -e "    👤 Avatar: ${avatar_removed}"
    echo ""

    if [ $total_removed -eq 0 ]; then
        echo -e "${YELLOW}⚠️  该版本没有用户上传的文件（可能只有 Bing 更新），仅删除 tag 和 release${NC}"
    else
        # 2. 删除文件（原图、缩略图、预览图）
        echo -e "${BLUE}🗑️  删除文件...${NC}"
        
        for item in "${files_to_delete[@]}"; do
            IFS='|' read -r series rel_path <<< "$item"
            
            # 原图
            local wallpaper_file="wallpaper/$series/$rel_path"
            if [ -f "$wallpaper_file" ]; then
                rm -f "$wallpaper_file"
                echo -e "  删除原图: $wallpaper_file"
            fi
            
            # 缩略图
            local thumbnail_file="thumbnail/$series/$rel_path"
            if [ -f "$thumbnail_file" ]; then
                rm -f "$thumbnail_file"
            fi
            
            # 预览图
            local preview_file="preview/$series/$rel_path"
            if [ -f "$preview_file" ]; then
                rm -f "$preview_file"
            fi
        done
        
        # 清理空目录
        find wallpaper thumbnail preview -type d -empty -delete 2>/dev/null || true
        
        echo -e "${GREEN}✅ 文件删除完成${NC}"
        echo ""

        # 3. 更新 timestamps 文件
        echo -e "${BLUE}📝 更新时间戳文件...${NC}"
        
        # 移除该 tag 的记录
        grep -v "|${target_tag}$" "$timestamp_file" > "${timestamp_file}.tmp" || true
        mv "${timestamp_file}.tmp" "$timestamp_file"
        
        echo -e "${GREEN}✅ 时间戳文件已更新${NC}"
        echo ""
    fi

    # 4. 更新 stats.json
    echo -e "${BLUE}📊 更新统计文件...${NC}"
    
    # 重新统计总数
    local desktop_count=$(grep '^desktop|' "$timestamp_file" 2>/dev/null | wc -l | tr -d ' ')
    local mobile_count=$(grep '^mobile|' "$timestamp_file" 2>/dev/null | wc -l | tr -d ' ')
    local avatar_count=$(grep '^avatar|' "$timestamp_file" 2>/dev/null | wc -l | tr -d ' ')

    if command -v jq &>/dev/null; then
        # 更新总数，移除该 tag 的 release 记录
        jq --arg tag "$target_tag" \
           --argjson desktop "$desktop_count" \
           --argjson mobile "$mobile_count" \
           --argjson avatar "$avatar_count" \
           '.total = {"desktop": $desktop, "mobile": $mobile, "avatar": $avatar} | 
            .lastUpdated = now | 
            .releases = [.releases[] | select(.tag != $tag)]' \
           "$stats_file" > "${stats_file}.tmp" && mv "${stats_file}.tmp" "$stats_file"
    elif command -v node &>/dev/null; then
        node -e "
const fs = require('fs');
const stats = JSON.parse(fs.readFileSync('$stats_file', 'utf8'));
stats.total = { desktop: $desktop_count, mobile: $mobile_count, avatar: $avatar_count };
stats.lastUpdated = new Date().toISOString();
stats.releases = (stats.releases || []).filter(r => r.tag !== '$target_tag');
fs.writeFileSync('$stats_file', JSON.stringify(stats, null, 2));
"
    fi
    
    echo -e "${GREEN}✅ 统计文件已更新${NC}"
    echo ""

    # 5. 提交更改
    echo -e "${BLUE}📥 提交更改...${NC}"
    
    git config user.name "github-actions[bot]"
    git config user.email "github-actions[bot]@users.noreply.github.com"
    
    git add .
    git commit -m "chore: rollback ${target_tag}" || echo "No changes to commit"
    git push || true
    
    echo -e "${GREEN}✅ 更改已提交${NC}"
    echo ""

    # 6. 删除 tag 和 release
    echo -e "${BLUE}🏷️  删除 tag 和 release...${NC}"
    
    # 删除远程 tag
    git push origin --delete "$target_tag" 2>/dev/null || echo "Remote tag already deleted"
    
    # 删除本地 tag
    git tag -d "$target_tag" 2>/dev/null || echo "Local tag already deleted"
    
    # 删除 GitHub Release
    if command -v gh &>/dev/null || [ -n "$GH_TOKEN" ]; then
        gh release delete "$target_tag" --yes 2>/dev/null || echo "Release already deleted or not found"
    fi
    
    echo -e "${GREEN}✅ Tag 和 Release 已删除${NC}"
    echo ""

    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}✅ 回滚成功!${NC}"
    echo -e "${GREEN}   已删除: ${target_tag}${NC}"
    echo -e "${GREEN}   当前版本: ${previous_tag:-无}${NC}"
    echo -e "${GREEN}   删除文件: ${total_removed} 个${NC}"
    echo -e "${GREEN}========================================${NC}"

    # 输出结果供后续使用
    echo "$previous_tag" > /tmp/rollback_result.txt
}

main "$@"
