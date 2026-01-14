#!/bin/bash
# ========================================
# 时间戳数据校验脚本
# ========================================
#
# 功能：检查时间戳记录与实际文件的一致性
#       - 找出孤立记录（文件已删除但记录还在）
#       - 找出遗漏记录（文件存在但没有记录）
#       - 可选：自动修复
#
# 用法：
#   ./scripts/verify-timestamps.sh <图床仓库路径> [--fix]
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
    local fix_mode="${2:-}"
    
    cd "$project_root"
    
    local backup_file="timestamps-backup-all.txt"
    local orphan_count=0
    local missing_count=0
    local orphan_records=()
    local missing_files=()
    
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}时间戳数据校验${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
    
    # 1. 检查孤立记录（文件已删除但记录还在）
    echo -e "${BLUE}检查孤立记录...${NC}"
    while IFS='|' read -r series path timestamp tag; do
        local full_path="wallpaper/$series/$path"
        if [ ! -f "$full_path" ]; then
            orphan_records+=("${series}|${path}|${timestamp}|${tag}")
            orphan_count=$((orphan_count + 1))
        fi
    done < "$backup_file"
    
    if [ $orphan_count -gt 0 ]; then
        echo -e "  ${YELLOW}发现 $orphan_count 条孤立记录${NC}"
        if [ "$fix_mode" = "--fix" ]; then
            echo -e "  ${BLUE}正在清理...${NC}"
            # 创建临时文件，排除孤立记录
            local temp_file="${backup_file}.tmp"
            > "$temp_file"
            while IFS='|' read -r series path timestamp tag; do
                local full_path="wallpaper/$series/$path"
                if [ -f "$full_path" ]; then
                    echo "${series}|${path}|${timestamp}|${tag}" >> "$temp_file"
                fi
            done < "$backup_file"
            mv "$temp_file" "$backup_file"
            echo -e "  ${GREEN}已清理 $orphan_count 条孤立记录${NC}"
        else
            echo -e "  ${YELLOW}使用 --fix 参数自动清理${NC}"
            # 显示前 10 条
            local show_count=0
            for record in "${orphan_records[@]}"; do
                if [ $show_count -lt 10 ]; then
                    echo -e "    - $record"
                    show_count=$((show_count + 1))
                fi
            done
            [ $orphan_count -gt 10 ] && echo -e "    ... 还有 $((orphan_count - 10)) 条"
        fi
    else
        echo -e "  ${GREEN}没有孤立记录${NC}"
    fi
    echo ""
    
    # 2. 检查遗漏记录（文件存在但没有记录）
    echo -e "${BLUE}检查遗漏记录...${NC}"
    for series in desktop mobile avatar; do
        local wallpaper_dir="wallpaper/$series"
        [ ! -d "$wallpaper_dir" ] && continue
        
        while IFS= read -r -d '' img; do
            local rel_path="${img#$wallpaper_dir/}"
            local key="${series}|${rel_path}"
            
            if ! grep -q "^${key}|" "$backup_file" 2>/dev/null; then
                missing_files+=("$key")
                missing_count=$((missing_count + 1))
            fi
        done < <(find "$wallpaper_dir" -type f \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.png" \) -print0)
    done
    
    if [ $missing_count -gt 0 ]; then
        echo -e "  ${YELLOW}发现 $missing_count 个文件缺少记录${NC}"
        if [ "$fix_mode" = "--fix" ]; then
            echo -e "  ${BLUE}正在补充...${NC}"
            local timestamp=$(date +%s)
            local latest_tag=$(git tag -l 'v*' --sort=-version:refname | head -1)
            [ -z "$latest_tag" ] && latest_tag="v1.0.0"
            
            for key in "${missing_files[@]}"; do
                echo "${key}|${timestamp}|${latest_tag}" >> "$backup_file"
            done
            echo -e "  ${GREEN}已补充 $missing_count 条记录（标记为 $latest_tag）${NC}"
        else
            echo -e "  ${YELLOW}使用 --fix 参数自动补充${NC}"
            # 显示前 10 条
            local show_count=0
            for key in "${missing_files[@]}"; do
                if [ $show_count -lt 10 ]; then
                    echo -e "    - $key"
                    show_count=$((show_count + 1))
                fi
            done
            [ $missing_count -gt 10 ] && echo -e "    ... 还有 $((missing_count - 10)) 个"
        fi
    else
        echo -e "  ${GREEN}所有文件都有记录${NC}"
    fi
    echo ""
    
    # 3. 统计信息
    echo -e "${BLUE}统计信息${NC}"
    local total_records=$(wc -l < "$backup_file" | tr -d ' ')
    local desktop_count=$(grep '^desktop|' "$backup_file" | wc -l | tr -d ' ')
    local mobile_count=$(grep '^mobile|' "$backup_file" | wc -l | tr -d ' ')
    local avatar_count=$(grep '^avatar|' "$backup_file" | wc -l | tr -d ' ')
    
    echo -e "  总记录数: ${GREEN}$total_records${NC}"
    echo -e "  🖥️  Desktop: ${GREEN}$desktop_count${NC}"
    echo -e "  📱 Mobile: ${GREEN}$mobile_count${NC}"
    echo -e "  👤 Avatar: ${GREEN}$avatar_count${NC}"
    echo ""
    
    # 4. 结果
    if [ $orphan_count -eq 0 ] && [ $missing_count -eq 0 ]; then
        echo -e "${GREEN}========================================${NC}"
        echo -e "${GREEN}✅ 数据一致性检查通过${NC}"
        echo -e "${GREEN}========================================${NC}"
        exit 0
    else
        echo -e "${YELLOW}========================================${NC}"
        if [ "$fix_mode" = "--fix" ]; then
            echo -e "${GREEN}✅ 数据已修复${NC}"
        else
            echo -e "${YELLOW}⚠️  发现数据不一致，使用 --fix 修复${NC}"
        fi
        echo -e "${YELLOW}========================================${NC}"
        exit 1
    fi
}

main "$@"
