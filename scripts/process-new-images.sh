#!/bin/bash
# ========================================
# 处理新增图片脚本（优化版）
# ========================================
#
# 功能：基于 Git diff 快速检测新增图片，生成缩略图和预览图
#       时间复杂度 O(新增文件数)，与总图片数无关
#
# 用法：
#   ./scripts/process-new-images.sh <图床仓库路径>
#
# ========================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# 配置（与 local-process.sh 保持一致）
THUMBNAIL_WIDTH=350
THUMBNAIL_QUALITY=75
PREVIEW_WIDTH=1920
PREVIEW_QUALITY=78
MOBILE_PREVIEW_WIDTH=1080
MOBILE_PREVIEW_QUALITY=75

WATERMARK_ENABLED=true
WATERMARK_TEXT="暖心"
WATERMARK_OPACITY=40

# 检测 ImageMagick 命令
detect_imagemagick_cmd() {
    if command -v magick &>/dev/null; then
        echo "magick"
    elif command -v convert &>/dev/null; then
        if convert --version 2>&1 | grep -q "ImageMagick"; then
            echo "convert"
        else
            echo ""
        fi
    else
        echo ""
    fi
}

# 检测中文字体
detect_chinese_font() {
    local cmd="$1"
    for f in "Noto-Sans-CJK-SC" "Heiti-SC-Medium" "PingFang-SC-Medium" "Microsoft-YaHei" "SimHei"; do
        $cmd -list font 2>/dev/null | grep -q "$f" && echo "$f" && return
    done
    echo ""
}

# 基于 Git diff 获取新增图片（极快）
get_new_images_by_git() {
    local project_root="$1"
    cd "$project_root"
    
    # 获取最新 tag
    git fetch --tags --quiet 2>/dev/null || true
    local latest_tag=$(git tag -l 'v*' --sort=-version:refname | head -1)
    
    if [ -z "$latest_tag" ]; then
        echo -e "${YELLOW}  没有找到 tag，扫描所有未处理的图片${NC}" >&2
        # 回退到遍历方式
        get_new_images_by_scan "$project_root"
        return
    fi
    
    echo -e "${GREEN}  ⚡ 基于 Git diff 检测 (对比 $latest_tag)${NC}" >&2
    
    # 获取新增的图片文件（只检测 wallpaper 目录下的图片）
    # 使用 -z 选项避免中文路径被转义，然后用 tr 转换为换行符
    git diff --name-only -z --diff-filter=A "$latest_tag"..HEAD -- 'wallpaper/*.jpg' 'wallpaper/*.jpeg' 'wallpaper/*.png' 'wallpaper/**/*.jpg' 'wallpaper/**/*.jpeg' 'wallpaper/**/*.png' 2>/dev/null | tr '\0' '\n' || true
    
    cd - > /dev/null
}

# 回退方案：遍历检查（首次或无 tag 时使用）
get_new_images_by_scan() {
    local project_root="$1"
    
    for series in desktop mobile avatar; do
        local wallpaper_dir="$project_root/wallpaper/$series"
        local thumbnail_dir="$project_root/thumbnail/$series"
        
        [ ! -d "$wallpaper_dir" ] && continue
        
        while IFS= read -r -d '' img; do
            local rel_path="${img#$wallpaper_dir/}"
            local filename=$(basename "$img")
            local name="${filename%.*}"
            local dir_path=$(dirname "$rel_path")
            
            # 检查缩略图是否存在
            if [ "$dir_path" = "." ]; then
                local thumb_path="$thumbnail_dir/${name}.webp"
            else
                local thumb_path="$thumbnail_dir/$dir_path/${name}.webp"
            fi
            
            if [ ! -f "$thumb_path" ]; then
                echo "wallpaper/$series/$rel_path"
            fi
        done < <(find "$wallpaper_dir" -type f \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.png" \) -print0)
    done
}

# 处理单张图片
process_image() {
    local src_file="$1"
    local project_root="$2"
    local im_cmd="$3"
    local font="$4"
    
    # 解析路径: wallpaper/desktop/动漫/原神/xxx.jpg
    local rel_to_wallpaper="${src_file#wallpaper/}"  # desktop/动漫/原神/xxx.jpg
    local series="${rel_to_wallpaper%%/*}"            # desktop
    local rest="${rel_to_wallpaper#*/}"               # 动漫/原神/xxx.jpg
    
    local filename=$(basename "$src_file")
    local name="${filename%.*}"
    local dir_path=$(dirname "$rest")  # 动漫/原神
    
    local full_src="$project_root/$src_file"
    
    [ ! -f "$full_src" ] && return
    
    # 目标路径
    if [ "$dir_path" = "." ]; then
        local thumbnail_dir="$project_root/thumbnail/$series"
        local preview_dir="$project_root/preview/$series"
    else
        local thumbnail_dir="$project_root/thumbnail/$series/$dir_path"
        local preview_dir="$project_root/preview/$series/$dir_path"
    fi
    
    mkdir -p "$thumbnail_dir"
    [ "$series" != "avatar" ] && mkdir -p "$preview_dir"
    
    # 生成缩略图（带水印）
    local dest_thumbnail="$thumbnail_dir/${name}.webp"
    if [ ! -f "$dest_thumbnail" ]; then
        local thumb_font_size=$((THUMBNAIL_WIDTH * 2 / 100))
        local watermark_alpha=$(awk "BEGIN {printf \"%.2f\", $WATERMARK_OPACITY / 100}")
        local watermark_color="rgba(255,255,255,$watermark_alpha)"
        
        if [ "$WATERMARK_ENABLED" = true ] && [ -n "$font" ]; then
            $im_cmd "$full_src" \
                -resize "${THUMBNAIL_WIDTH}x>" \
                -font "$font" \
                -pointsize "$thumb_font_size" \
                -fill "$watermark_color" \
                -gravity southeast \
                -annotate -25x-25+20+40 "$WATERMARK_TEXT" \
                -gravity southwest \
                -annotate 0x0+20+40 "$WATERMARK_TEXT" \
                -quality "$THUMBNAIL_QUALITY" \
                -strip \
                "$dest_thumbnail" 2>/dev/null || \
            $im_cmd "$full_src" \
                -resize "${THUMBNAIL_WIDTH}x>" \
                -quality "$THUMBNAIL_QUALITY" \
                -strip \
                "$dest_thumbnail"
        else
            $im_cmd "$full_src" \
                -resize "${THUMBNAIL_WIDTH}x>" \
                -quality "$THUMBNAIL_QUALITY" \
                -strip \
                "$dest_thumbnail"
        fi
        echo -e "    ${GREEN}✓${NC} 缩略图"
    fi
    
    # 生成预览图（无水印，avatar 不需要）
    if [ "$series" != "avatar" ]; then
        local preview_width=$PREVIEW_WIDTH
        local preview_quality=$PREVIEW_QUALITY
        if [ "$series" = "mobile" ]; then
            preview_width=$MOBILE_PREVIEW_WIDTH
            preview_quality=$MOBILE_PREVIEW_QUALITY
        fi
        
        local dest_preview="$preview_dir/${name}.webp"
        if [ ! -f "$dest_preview" ]; then
            $im_cmd "$full_src" \
                -resize "${preview_width}x>" \
                -quality "$preview_quality" \
                -strip \
                "$dest_preview"
            echo -e "    ${GREEN}✓${NC} 预览图"
        fi
    fi
}

main() {
    local project_root="${1:-.}"
    
    [ ! -d "$project_root/wallpaper" ] && {
        echo -e "${RED}错误: 找不到 wallpaper 目录${NC}"
        exit 1
    }
    
    # 检测 ImageMagick
    local im_cmd=$(detect_imagemagick_cmd)
    [ -z "$im_cmd" ] && {
        echo -e "${RED}错误: 未找到 ImageMagick${NC}"
        exit 1
    }
    
    # 检测字体
    local font=""
    [ "$WATERMARK_ENABLED" = true ] && font=$(detect_chinese_font "$im_cmd")
    
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}检测并处理新增图片（Git diff 优化版）${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
    
    # 获取新增图片列表
    local new_files=()
    while IFS= read -r file; do
        [ -n "$file" ] && new_files+=("$file")
    done < <(get_new_images_by_git "$project_root")
    
    local count=${#new_files[@]}
    
    echo ""
    echo -e "发现 ${GREEN}${count}${NC} 张新图片"
    echo ""
    
    if [ "$count" -eq 0 ]; then
        echo -e "${YELLOW}没有新图片需要处理${NC}"
        echo "0" > /tmp/processed_count.txt
        exit 0
    fi
    
    # 处理每张图片
    local processed=0
    for file in "${new_files[@]}"; do
        processed=$((processed + 1))
        echo -e "${BLUE}[$processed/$count]${NC} $file"
        process_image "$file" "$project_root" "$im_cmd" "$font"
        echo ""
    done
    
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}处理完成! 共处理 ${count} 张图片${NC}"
    echo -e "${GREEN}========================================${NC}"
    
    # 输出处理数量供工作流使用
    echo "$count" > /tmp/processed_count.txt
}

main "$@"
