"""
拼多多登录模块
通过抓包助手引导用户从拼多多 APP 中获取 access_token
支持 Railway 部署 (环境变量方式) 和本地运行 (抓包助手方式)
"""

import os
import sys
import json
import time
import logging

logger = logging.getLogger("pdd_coupon")

# Token 缓存文件路径
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pdd_token")


# ============================================================
# 抓包助手：引导用户从 APP 中获取 Token
# ============================================================

def _open_grab_helper():
    """
    启动一个本地网页，图文并茂地教用户如何从拼多多 APP 抓包获取 Token
    """
    import webbrowser
    import threading
    from http.server import HTTPServer, BaseHTTPRequestHandler

    html_content = _build_helper_html()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html_content.encode("utf-8"))

        def do_POST(self):
            """接收用户提交的 Token"""
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")

            # 解析表单数据
            token = ""
            for part in body.split("&"):
                if part.startswith("token="):
                    from urllib.parse import unquote
                    token = unquote(part.split("=", 1)[1]).strip()
                    break

            if token:
                # 智能解析：如果粘贴的是整段 cookie，提取 PDDAccessToken
                parsed = _smart_parse_token(token)
                save_token(parsed["access_token"], extra=parsed.get("extra"))
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    '<h1 style="text-align:center;color:#4CAF50;margin-top:100px;">'
                    'Token 已保存成功！请回到终端窗口。</h1>'.encode("utf-8")
                )
                # 写入成功标志文件
                with open(os.path.join(os.path.dirname(TOKEN_FILE), ".token_ready"), "w") as f:
                    f.write(token)
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Token is empty")

        def log_message(self, format, *args):
            pass

    port = 18923
    server = HTTPServer(("127.0.0.1", port), Handler)

    url = f"http://127.0.0.1:{port}"
    print(f"\n  抓包助手已打开: {url}")
    print(f"  如果浏览器未自动打开，请手动访问上面的地址\n")

    webbrowser.open(url)

    # 后台运行服务器
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    return server, port


def _build_helper_html() -> str:
    """构建抓包助手 HTML 页面"""
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>拼多多 Token 获取助手</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #f5f5f5; color: #333; line-height: 1.8; }
.container { max-width: 700px; margin: 0 auto; padding: 20px; }
h1 { text-align: center; color: #e02e24; margin: 30px 0; font-size: 24px; }
.card { background: white; border-radius: 12px; padding: 24px; margin: 16px 0;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
.card h2 { color: #e02e24; font-size: 18px; margin-bottom: 12px; }
.step { display: flex; gap: 16px; margin: 16px 0; align-items: flex-start; }
.step-num { background: #e02e24; color: white; width: 32px; height: 32px;
            border-radius: 50%; display: flex; align-items: center; justify-content: center;
            font-weight: bold; flex-shrink: 0; font-size: 16px; }
.step-text { flex: 1; }
.step-text strong { color: #e02e24; }
.tip { background: #fff3e0; border-left: 4px solid #ff9800; padding: 12px 16px;
       border-radius: 4px; margin: 12px 0; font-size: 14px; }
.code { background: #263238; color: #aed581; padding: 12px 16px; border-radius: 8px;
        font-family: 'Courier New', monospace; font-size: 13px; margin: 8px 0;
        overflow-x: auto; word-break: break-all; }
.form-area { margin-top: 30px; text-align: center; }
textarea { width: 100%; height: 80px; border: 2px solid #ddd; border-radius: 8px;
           padding: 12px; font-size: 14px; font-family: monospace; resize: vertical; }
textarea:focus { border-color: #e02e24; outline: none; }
button { background: #e02e24; color: white; border: none; padding: 14px 40px;
         border-radius: 8px; font-size: 16px; cursor: pointer; margin-top: 12px; }
button:hover { background: #c62828; }
.method-tabs { display: flex; gap: 8px; margin-bottom: 16px; }
.method-tab { padding: 8px 16px; border-radius: 20px; cursor: pointer;
              border: 2px solid #e02e24; font-size: 14px; background: white; }
.method-tab.active { background: #e02e24; color: white; }
.method-content { display: none; }
.method-content.active { display: block; }
</style>
</head>
<body>
<div class="container">
<h1>🔑 拼多多 Token 获取助手</h1>

<div class="card">
<h2>什么是 Token？</h2>
<p>Token 是拼多多 APP 中你的登录凭证，抢券脚本需要它来代替你发请求。
   我们需要通过<strong>抓包</strong>从手机 APP 中获取它。</p>
</div>

<div class="card">
<h2>选择获取方式</h2>
<div class="method-tabs">
  <div class="method-tab active" onclick="switchMethod('a')">方式A: 手机抓包(推荐)</div>
  <div class="method-tab" onclick="switchMethod('b')">方式B: 电脑浏览器</div>
</div>

<!-- 方式A: 手机抓包 -->
<div class="method-content active" id="method-a">
<div class="step">
  <div class="step-num">1</div>
  <div class="step-text">
    <strong>安装抓包工具</strong><br>
    iPhone: 下载 <strong>Stream</strong> (App Store 免费)<br>
    Android: 下载 <strong>HttpCanary</strong> 或 <strong>Packet Capture</strong>
  </div>
</div>

<div class="step">
  <div class="step-num">2</div>
  <div class="step-text">
    <strong>开始抓包</strong><br>
    打开抓包工具 → 点击开始录制 → 然后打开<strong>拼多多 APP</strong>
  </div>
</div>

<div class="step">
  <div class="step-num">3</div>
  <div class="step-text">
    <strong>触发一个请求</strong><br>
    在拼多多 APP 中随便点一下（比如进入首页、搜索商品、点开一个优惠券页面）
  </div>
</div>

<div class="step">
  <div class="step-num">4</div>
  <div class="step-text">
    <strong>找到 access_token</strong><br>
    回到抓包工具 → 停止录制 → 找到任意一个
    <code style="background:#eee;padding:2px 6px;border-radius:4px;">api.pinduoduo.com</code> 的请求<br>
    查看请求参数 (Request Body) → 找到 <strong>access_token</strong> 字段
  </div>
</div>

<div class="tip">
  💡 <strong>提示：</strong>access_token 通常是一串很长的字符串，类似：<br>
  <code style="font-size:11px;">XOIFNC3MFRQDABBC2W3X4Y5Z6A7B8C9D...</code>
</div>
</div>

<!-- 方式B: 电脑浏览器 -->
<div class="method-content" id="method-b">
<div class="step">
  <div class="step-num">1</div>
  <div class="step-text">
    <strong>在手机上设置代理</strong><br>
    电脑和手机连同一个 WiFi → 电脑上安装
    <strong>Fiddler</strong> (Windows) 或 <strong>Charles</strong> (Mac)<br>
    手机 WiFi 设置中配置代理，指向电脑 IP + 端口
  </div>
</div>

<div class="step">
  <div class="step-num">2</div>
  <div class="step-text">
    <strong>安装证书</strong><br>
    手机浏览器访问 <code style="background:#eee;padding:2px 6px;border-radius:4px;">chls.pro/ssl</code>
    (Charles) 或对应地址，安装并信任证书
  </div>
</div>

<div class="step">
  <div class="step-num">3</div>
  <div class="step-text">
    <strong>打开拼多多 APP 操作一下</strong><br>
    在手机上正常使用拼多多 APP，电脑上的抓包工具会实时显示请求
  </div>
</div>

<div class="step">
  <div class="step-num">4</div>
  <div class="step-text">
    <strong>搜索 access_token</strong><br>
    在抓包工具中搜索 <strong>access_token</strong>，复制它的值
  </div>
</div>
</div>
</div>

<div class="card">
<h2>粘贴 Token</h2>
<p style="margin-bottom:12px;">将获取到的 access_token 值粘贴到下方：</p>
<form onsubmit="return submitToken()">
  <textarea id="tokenInput" placeholder="在此粘贴 access_token 的值..."></textarea>
  <br>
  <button type="submit">保存 Token</button>
</form>
<p id="result" style="margin-top:12px; text-align:center;"></p>
</div>

<div class="card">
<h2>⚠️ 注意事项</h2>
<ul style="padding-left: 20px;">
  <li>Token 有有效期，过期后需要重新获取</li>
  <li>不要将 Token 分享给他人</li>
  <li>如果抓不到 access_token，试试在 APP 中多操作几步</li>
  <li>某些抓包工具需要先安装 HTTPS 证书才能抓到拼多多的请求</li>
</ul>
</div>
</div>

<script>
function switchMethod(m) {
  document.querySelectorAll('.method-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.method-content').forEach(c => c.classList.remove('active'));
  event.target.classList.add('active');
  document.getElementById('method-' + m).classList.add('active');
}

function submitToken() {
  const token = document.getElementById('tokenInput').value.trim();
  if (!token) {
    document.getElementById('result').innerHTML = '<span style="color:red;">请输入 Token</span>';
    return false;
  }
  const form = new URLSearchParams();
  form.append('token', token);
  fetch('/submit', { method: 'POST', body: form.toString() })
    .then(r => {
      if (r.ok) {
        document.getElementById('result').innerHTML =
          '<span style="color:#4CAF50;font-size:18px;">✅ Token 已保存！请回到终端窗口。</span>';
      } else {
        document.getElementById('result').innerHTML =
          '<span style="color:red;">保存失败，请重试</span>';
      }
    })
    .catch(e => {
      document.getElementById('result').innerHTML =
        '<span style="color:red;">请求失败: ' + e + '</span>';
    });
  return false;
}
</script>
</body>
</html>"""


# ============================================================
# 智能解析 Token
# ============================================================

def _smart_parse_token(raw: str) -> dict:
    """
    智能解析用户粘贴的内容:
    - 如果是 cookie 字符串 (key=value; key=value)，提取 PDDAccessToken
    - 如果是纯 token 值，直接使用
    - 同时提取 pdd_user_id 等信息
    """
    raw = raw.strip()
    result = {"access_token": raw, "extra": {}}

    # 检查是否是 cookie 格式 (包含 ; 和 =)
    if ";" in raw and "=" in raw:
        cookies = {}
        for pair in raw.split(";"):
            pair = pair.strip()
            if "=" in pair:
                key, val = pair.split("=", 1)
                # 去除 key 前后的空格和 + 号 (URL编码空格)
                key = key.strip().lstrip("+").strip()
                cookies[key] = val.strip()

        result["extra"] = {"cookies": cookies, "auth_type": "cookie_string"}

        # 提取 PDDAccessToken
        pdd_token = cookies.get("PDDAccessToken", "")
        if pdd_token:
            result["access_token"] = pdd_token
            logger.info(f"从 cookie 中提取到 PDDAccessToken: {pdd_token[:30]}...")
        else:
            # 没有找到 PDDAccessToken，尝试找其他 token 字段
            for key in ["access_token", "accessToken", "token"]:
                if key in cookies:
                    result["access_token"] = cookies[key]
                    break

        # 提取 user_id
        user_id = cookies.get("pdd_user_id", "")
        if user_id:
            result["extra"]["user_id"] = user_id

    return result


# ============================================================
# Token 持久化
# ============================================================

def save_token(token: str, extra: dict = None, filepath: str = TOKEN_FILE):
    """将 token 保存到本地文件"""
    data = {
        "access_token": token,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if extra:
        if extra.get("cookies"):
            data["cookies"] = extra["cookies"]
        if extra.get("auth_type"):
            data["auth_type"] = extra["auth_type"]

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"Token 已保存到 {filepath}")


def load_token(filepath: str = TOKEN_FILE) -> str:
    """从本地文件加载 token"""
    if not os.path.exists(filepath):
        return ""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        token = data.get("access_token", "")
        saved_at = data.get("saved_at", "未知")
        logger.info(f"从缓存加载 Token (保存于 {saved_at})")
        return token
    except Exception:
        return ""


# ============================================================
# 手动终端输入 (备用)
# ============================================================

def _login_manual() -> str:
    """终端中手动粘贴 Token"""
    print()
    print("=" * 55)
    print("  手动粘贴 Token")
    print("=" * 55)
    print()
    print("  获取方式：")
    print("  1. 手机安装抓包工具 (Stream / HttpCanary)")
    print("  2. 开始抓包后打开拼多多 APP 随便操作一下")
    print("  3. 在抓包记录中搜索 access_token")
    print("  4. 复制其值粘贴到下方")
    print()

    token = input("  请粘贴 access_token: ").strip()
    if not token:
        raise RuntimeError("未输入 Token")
    return token


# ============================================================
# 主入口
# ============================================================

def qrcode_login(timeout: int = 300) -> str:
    """
    完整的登录流程:
    1. 加载已缓存的 Token
    2. 打开抓包助手网页 (引导获取 + 在线提交)
    3. 终端手动输入 (兜底)

    返回: access_token
    """
    print()
    print("=" * 55)
    print("  拼多多 Token 获取")
    print("=" * 55)
    print()

    # 1. 尝试加载缓存
    saved_token = load_token()
    if saved_token:
        print(f"  检测到已保存的 Token (前20位: {saved_token[:20]}...)")
        if sys.stdin.isatty():
            try:
                choice = input("  是否继续使用？(y/n，默认 y): ").strip().lower()
                if choice in ("", "y", "yes"):
                    return saved_token
            except (EOFError, KeyboardInterrupt):
                return saved_token
        else:
            logger.info("非交互环境，使用缓存 Token")
            return saved_token

    # 2. 打开抓包助手
    try:
        server, port = _open_grab_helper()
        print(f"  抓包助手运行在 http://127.0.0.1:{port}")
        print(f"  请在浏览器中按指引获取 Token 并提交")
        print()

        # 等待用户在网页中提交 Token
        ready_file = os.path.join(os.path.dirname(TOKEN_FILE), ".token_ready")
        start = time.time()
        while time.time() - start < timeout:
            if os.path.exists(ready_file):
                with open(ready_file, "r") as f:
                    token = f.read().strip()
                os.remove(ready_file)
                server.shutdown()

                if token:
                    # 确保已保存
                    if not os.path.exists(TOKEN_FILE):
                        save_token(token)
                    print()
                    print("=" * 55)
                    print("  Token 获取成功!")
                    print("=" * 55)
                    print()
                    return token

            # 也检查 .pdd_token 是否已被网页端保存
            cached = load_token()
            if cached:
                server.shutdown()
                print()
                print("=" * 55)
                print("  Token 已保存!")
                print("=" * 55)
                print()
                return cached

            time.sleep(1)

        server.shutdown()
        logger.warning("等待超时")

    except Exception as e:
        logger.warning(f"抓包助手启动失败: {e}")

    # 3. 终端手动输入 (兜底)
    try:
        token = _login_manual()
        save_token(token)
        print()
        print("=" * 55)
        print("  Token 已保存!")
        print("=" * 55)
        print()
        return token
    except Exception as e:
        logger.error(f"获取 Token 失败: {e}")
        raise


# 可独立运行测试
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    token = qrcode_login()
    print(f"\n最终 Token: {token[:80]}...")

    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"\nToken 文件内容:")
        print(json.dumps(data, ensure_ascii=False, indent=2))
