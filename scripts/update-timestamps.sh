#!/bin/bash
# ========================================
# 更新时间戳脚本
# ========================================
#
# 功能：为新增图片添加时间戳记录
#       格式: series|相对路径|时间戳(秒)|first_tag
#
# 用法：
#   ./scripts/update-timestamps.sh <图床仓库路径> [新tag]
#
# ========================================

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

main() {
    local project_root="${1:-.}"
    local new_tag="${2:-}"

    local backup_file="$project_root/timestamps-backup-all.txt"
    local timestamp=$(date +%s)

    # 如果没有传入 tag，计算下一个版本号
    if [ -z "$new_tag" ]; then
        cd "$project_root"
        local latest_tag=$(git tag -l 'v*' --sort=-version:refname | head -1)
        if [ -z "$latest_tag" ]; then
            new_tag="v1.0.1"
        else
            local version=${latest_tag#v}
            IFS='.' read -r major minor patch <<< "$version"
            local new_patch=$((patch + 1))
            new_tag="v${major}.${minor}.${new_patch}"
        fi
        cd - > /dev/null
    fi

    echo -e "${BLUE}更新时间戳文件...${NC}"
    echo -e "  Tag: ${GREEN}$new_tag${NC}"

    local count=0

    # 扫描三个系列，找出没有时间戳记录的图片
    for series in desktop mobile avatar; do
        local wallpaper_dir="$project_root/wallpaper/$series"
        [ ! -d "$wallpaper_dir" ] && continue

        while IFS= read -r -d '' img; do
            local rel_path="${img#$wallpaper_dir/}"
            local key="${series}|${rel_path}"

            # 检查是否已有记录
            if ! grep -q "^${key}|" "$backup_file" 2>/dev/null; then
                echo "${key}|${timestamp}|${new_tag}" >> "$backup_file"
                count=$((count + 1))
            fi
        done < <(find "$wallpaper_dir" -type f \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.png" \) -print0)
    done

    echo -e "  新增 ${GREEN}${count}${NC} 条时间戳记录"
}

main "$@"
