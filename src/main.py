"""入口:以 HTTP Web 服务模式启动(浏览器可访问),配合 Cloudflare Tunnel 等穿透工具对外暴露。

暴露一个 JSON API(POST /api {name, args}),前端通过 fetch 调用。
桌面 PyWebView GUI 已移除,纯 Web 部署,便于在服务器 / 云上长期运行,无需任何 GUI 依赖。
"""
from __future__ import annotations
import os
import sys
import socket
import argparse

# 确保 src/ 在路径中,保证 `from backend.xxx` 可用
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.api import API

HERE = os.path.dirname(os.path.abspath(__file__))


def _free_port() -> int:
    """让系统分配一个当前空闲的端口,避免端口冲突。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def run_web(port: int = 0, host: str = "127.0.0.1"):
    import http.server
    import json

    api = API()  # Web 模式不启动桌面通知调度(避免跨线程访问 DB),提醒增删查仍可用
    frontend_dir = os.path.join(HERE, "frontend")

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=frontend_dir, **kw)

        def _send_json(self, obj, status: int = 200):
            data = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if self.path.split("?")[0] == "/api":
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    payload = json.loads(raw.decode("utf-8") or "{}")
                except Exception:
                    payload = {}
                name = payload.get("name")
                args = payload.get("args") or []
                try:
                    if (not isinstance(name, str)
                            or name.startswith("_")
                            or not hasattr(api, name)
                            or not callable(getattr(api, name))):
                        raise ValueError(f"未知或不可调用的方法: {name}")
                    result = getattr(api, name)(*(args or []))
                    self._send_json({"result": result})
                except Exception as e:  # 任何异常都返回给前端,不崩服务
                    self._send_json({"error": str(e)}, status=400)
            else:
                self.send_error(404)

        def log_message(self, *a):  # 静默
            pass

    if not port:
        port = _free_port()
    server = http.server.ThreadingHTTPServer((host, port), Handler)
    url = f"http://127.0.0.1:{port}"
    if host not in ("127.0.0.1", "localhost"):
        print("[WARN] 正在监听非本机地址,局域网内其他设备可访问你的数据,请仅在调试小程序时使用 --host。")
    # 把访问地址写到文件,方便脚本/用户直接拿到(后台启动时不会刷屏)
    try:
        with open(os.path.join(os.path.dirname(HERE), "web_url.txt"), "w", encoding="utf-8") as f:
            f.write(url)
    except Exception:
        pass
    print(f"\n[OK] 小助手已启动(Web 模式): {url}")
    print("   按 Ctrl+C 停止。\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def main():
    parser = argparse.ArgumentParser(description="小助手 · AI 人生教练 (Web)")
    parser.add_argument("--port", type=int, default=0,
                        help="监听端口(0=自动选空闲端口,建议固定如 8000 以配合隧道)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="监听地址(默认仅本机;配合 Cloudflare Tunnel 等穿透工具无需改成 0.0.0.0)")
    args = parser.parse_args()
    run_web(args.port, args.host)


if __name__ == "__main__":
    main()
