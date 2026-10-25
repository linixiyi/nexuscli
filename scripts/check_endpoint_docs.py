#!/usr/bin/env python3
"""CI 校验入口：docs/runtime-api.md 端点表与 ROUTE_MANIFEST 不同步时 exit 1。

模式照搬（Ported from kitagent (MIT, zed123214/kitagent)
scripts/check_wire_protocol.py 的生成脚本薄包装）；本包装把模式钉死为
--check（只校验不写），供 .github/workflows/ci.yml 调用。
"""

from __future__ import annotations

from generate_endpoint_docs import main

if __name__ == "__main__":
    main(["--check"])
