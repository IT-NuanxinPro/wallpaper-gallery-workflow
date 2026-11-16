#!/bin/bash
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
project_root="$(cd "${1:-.}" && pwd)"
fix_mode="${2:-}"
use_tag="${3:-}"
if [ -z "$use_tag" ]; then
    use_tag=$(git -C "$project_root" tag -l 'v*' --sort=-version:refname | sed -n '1p')
    use_tag="${use_tag:-v1.0.0}"
fi
if [ "$fix_mode" = "--fix" ]; then
    node "$script_dir/lib/timestamps.mjs" verify "$project_root" "$use_tag" --fix
else
    node "$script_dir/lib/timestamps.mjs" verify "$project_root" "$use_tag"
fi
