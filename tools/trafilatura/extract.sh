#!/usr/bin/env bash
# trafilatura 网页正文提取封装
# 用法: ./extract.sh <URL> [--format txt|json|markdown|xml|csv]
# 依赖: workspace/.venv-trafilatura (uv 管理)
set -euo pipefail

WORKSPACE="$(cd "$(dirname "$0")/../../.." && pwd)"
PY="$WORKSPACE/.venv-trafilatura/bin/python"

if [ ! -x "$PY" ]; then
  echo "错误: 找不到虚拟环境 $PY" >&2
  echo "先运行: uv venv $WORKSPACE/.venv-trafilatura && uv pip install --python $PY trafilatura" >&2
  exit 1
fi

if [ $# -lt 1 ]; then
  echo "用法: $0 <URL> [--format txt|json|markdown|xml|csv]" >&2
  exit 1
fi

URL="$1"
FMT="txt"
[ "${2:-}" = "--format" ] && [ -n "${3:-}" ] && FMT="$3"

"$PY" - "$URL" "$FMT" << 'PYEOF'
import sys
import trafilatura

url = sys.argv[1]
fmt = sys.argv[2]

downloaded = trafilatura.fetch_url(url)
if not downloaded:
    print(f"下载失败: {url}", file=sys.stderr)
    sys.exit(1)

# 元数据
meta = trafilatura.extract_metadata(downloaded)
if meta and fmt == "txt":
    title = meta.title or "无标题"
    author = meta.author or "未知"
    date = meta.date or "未知日期"
    print(f"# {title}\n来源: {url}\n作者: {author} | 日期: {date}\n{'='*60}")

# 正文
if fmt in ("json", "xml", "csv", "markdown"):
    out = trafilatura.extract(downloaded, output_format=fmt, include_comments=False)
else:
    out = trafilatura.extract(downloaded, include_comments=False)

if out:
    print(out)
else:
    print("正文提取失败（可能页面结构特殊）", file=sys.stderr)
    sys.exit(1)
PYEOF
