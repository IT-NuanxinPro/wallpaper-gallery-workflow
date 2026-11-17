derivative_filename() {
    local image_file="$1"
    local filename="${image_file##*/}"
    local stem="${filename%.*}"
    local source_dir="${image_file%/*}"
    local matches=0
    local candidate

    for candidate in "$source_dir/$stem".*; do
        [ -f "$candidate" ] || continue
        case "${candidate##*.}" in
            jpg|jpeg|png|webp|JPG|JPEG|PNG|WEBP)
                matches=$((matches + 1))
                [ "$matches" -gt 1 ] && break
                ;;
        esac
    done

    if [ "$matches" -gt 1 ]; then
        printf '%s.webp' "$filename"
    else
        printf '%s.webp' "$stem"
    fi
}

original_exists_for_derivative() {
    local source_dir="$1"
    local derivative_file="$2"
    local stem="${derivative_file%.webp}"
    local ext

    [ -f "$source_dir/$stem" ] && return 0
    for ext in jpg jpeg png webp JPG JPEG PNG WEBP; do
        [ -f "$source_dir/$stem.$ext" ] && return 0
    done
    return 1
}
