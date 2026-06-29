"""小念 XiaoNian 启动入口。

用法：
    python run.py
然后浏览器打开 http://127.0.0.1:8765
"""
from __future__ import annotations

import uvicorn

from config import settings

if __name__ == "__main__":
    print("=" * 56)
    print("  小念 XiaoNian — 你电脑里会成长的生活助理")
    print(f"  本地服务: http://{settings.host}:{settings.port}")
    print(f"  默认模型: {settings.default_provider}")
    print(f"  数据目录: {settings.data_dir}（全部留在本机）")
    print("=" * 56)
    uvicorn.run("daemon.main:app", host=settings.host, port=settings.port, reload=False)
