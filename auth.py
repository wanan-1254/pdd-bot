"""
PDD Bot 登录鉴权模块
Flask session 账号密码登录，凭证持久化存储
"""
import os
import json
import hashlib
import secrets
from functools import wraps
from flask import session, redirect, url_for, request, jsonify, Response

CREDENTIALS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".credentials.json")

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "pdd2026"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def load_credentials() -> dict:
    """加载凭证，不存在则创建默认"""
    if not os.path.exists(CREDENTIALS_FILE):
        creds = {"username": DEFAULT_USERNAME, "password_hash": _hash(DEFAULT_PASSWORD)}
        _save_credentials(creds)
        return creds
    try:
        with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"username": DEFAULT_USERNAME, "password_hash": _hash(DEFAULT_PASSWORD)}


def _save_credentials(creds: dict):
    with open(CREDENTIALS_FILE, "w", encoding="utf-8") as f:
        json.dump(creds, f, ensure_ascii=False, indent=2)


def get_secret_key() -> str:
    """从凭证文件派生 session secret key"""
    creds = load_credentials()
    return _hash(creds.get("password_hash", "") + "pdd_bot_secret")


def verify_password(password: str) -> bool:
    return _hash(password) == load_credentials().get("password_hash", "")


def change_credentials(new_username: str = None, new_password: str = None):
    creds = load_credentials()
    if new_username is not None and new_username.strip():
        creds["username"] = new_username.strip()
    if new_password is not None and new_password.strip():
        creds["password_hash"] = _hash(new_password.strip())
    _save_credentials(creds)


def get_username() -> str:
    return load_credentials().get("username", DEFAULT_USERNAME)


def login_required(f):
    """鉴权装饰器：未登录则跳转 /login 或返回 401"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized", "message": "请先登录"}), 401
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated


def register_auth_routes(app):
    """向 Flask app 注册鉴权路由"""

    @app.route("/login", methods=["GET", "POST"])
    def login_page():
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            username = data.get("username", "").strip()
            password = data.get("password", "")
            # 同时验证用户名和密码
            creds = load_credentials()
            if username != creds.get("username", DEFAULT_USERNAME):
                return jsonify({"success": False, "error": "账号错误"})
            if not verify_password(password):
                return jsonify({"success": False, "error": "密码错误"})
            session["logged_in"] = True
            return jsonify({"success": True})

        # GET: 已登录则跳转面板
        if session.get("logged_in"):
            return redirect("/")
        # 不暴露用户名给前端
        return Response(LOGIN_HTML, content_type="text/html; charset=utf-8")

    @app.route("/api/logout", methods=["POST"])
    def api_logout():
        session.clear()
        return jsonify({"success": True})

    @app.route("/api/change-password", methods=["POST"])
    @login_required
    def api_change_password():
        data = request.get_json(silent=True) or {}
        old_pw = data.get("old_password", "")
        new_pw = data.get("new_password", "")
        new_user = data.get("new_username", "").strip()

        if not verify_password(old_pw):
            return jsonify({"success": False, "error": "旧密码错误"})

        if new_pw and len(new_pw) < 4:
            return jsonify({"success": False, "error": "新密码至少4位"})

        change_credentials(
            new_username=new_user if new_user else None,
            new_password=new_pw if new_pw else None,
        )
        # 更新 session secret
        if new_pw:
            app.secret_key = get_secret_key()
        return jsonify({"success": True, "message": "凭证已更新"})

    @app.route("/health")
    def health():
        """Railway 健康检查"""
        return jsonify({"status": "ok"})

    @app.route("/api/get-login-info")
    @login_required
    def api_login_info():
        """返回当前登录信息（已登录才能调用）"""
        return jsonify({"username": get_username()})

    @app.before_request
    def _check_auth():
        """全局鉴权：保护除白名单外的所有路由"""
        public = {"login_page", "health", "static"}
        if request.endpoint in public:
            return
        # API 白名单
        if request.path == "/login":
            return
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized", "message": "请先登录"}), 401
            return redirect("/login")


LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>登录 · 拼多多抢券 Bot</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  background: #F3F4F6;
  display: flex; align-items: center; justify-content: center;
  min-height: 100vh;
  color: #1F2937;
}
.container {
  width: 100%; max-width: 400px;
  padding: 20px;
}
.card {
  background: #FFFFFF;
  border-radius: 16px;
  padding: 36px 32px;
  box-shadow: 0 4px 24px rgba(0,0,0,0.08);
  border: 1px solid #E5E7EB;
}
.logo {
  text-align: center; margin-bottom: 28px;
}
.logo-icon { font-size: 48px; }
.logo h1 { font-size: 22px; color: #EF4444; margin-top: 8px; }
.logo p { color: #9CA3AF; font-size: 13px; margin-top: 4px; }
.form-group { margin-bottom: 18px; }
.form-label {
  display: block; font-size: 14px; color: #6B7280;
  margin-bottom: 6px; font-weight: 500;
}
.form-input {
  width: 100%; padding: 12px 16px;
  border: 2px solid #E5E7EB; border-radius: 10px;
  font-size: 15px; font-family: inherit;
  transition: border-color 0.2s;
  background: #F9FAFB; color: #1F2937;
  autocomplete: off;
}
.form-input:focus {
  outline: none; border-color: #EF4444;
  background: #FFFFFF;
  box-shadow: 0 0 0 3px rgba(239,68,68,0.1);
}
.btn {
  width: 100%; padding: 13px;
  background: linear-gradient(135deg, #EF4444, #DC2626);
  color: white; border: none; border-radius: 10px;
  font-size: 16px; font-weight: 600; cursor: pointer;
  transition: all 0.2s;
}
.btn:hover { background: linear-gradient(135deg, #DC2626, #B91C1C); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.error-msg {
  text-align: center; color: #EF4444; font-size: 14px;
  margin-top: 16px; display: none;
  padding: 10px; background: #FEF2F2; border-radius: 8px;
}
.footer {
  text-align: center; color: #9CA3AF; font-size: 12px;
  margin-top: 20px;
}
</style>
</head>
<body>
<div class="container">
  <div class="card">
    <div class="logo">
      <div class="logo-icon">🎯</div>
      <h1>拼多多抢券 Bot</h1>
      <p>请输入账号和密码登录</p>
    </div>
    <form onsubmit="return doLogin()">
      <div class="form-group">
        <label class="form-label">账号</label>
        <input class="form-input" id="username" type="text" placeholder="请输入登录账号" autocomplete="new-username" autofocus>
      </div>
      <div class="form-group">
        <label class="form-label">密码</label>
        <input class="form-input" id="password" type="password" placeholder="请输入登录密码" autocomplete="new-password">
      </div>
      <button class="btn" type="submit" id="submitBtn">登 录</button>
    </form>
    <div class="error-msg" id="errorMsg"></div>
  </div>
  <div class="footer">PDD Coupon Bot · 私有部署</div>
</div>

<script>
// 防止浏览器自动填充
if (document.addEventListener) {
  document.addEventListener('DOMContentLoaded', function() {
    var u = document.getElementById('username');
    var p = document.getElementById('password');
    if (u) { u.value = ''; u.autocomplete = 'off'; }
    if (p) { p.value = ''; p.autocomplete = 'off'; }
  });
}

async function doLogin() {
  const user = document.getElementById('username').value.trim();
  const pw = document.getElementById('password').value;
  if (!user) {
    showError('请输入账号');
    return false;
  }
  if (!pw) {
    showError('请输入密码');
    return false;
  }
  const btn = document.getElementById('submitBtn');
  btn.disabled = true;
  btn.textContent = '登录中...';

  try {
    const r = await fetch('/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: user, password: pw})
    });
    const d = await r.json();
    if (d.success) {
      window.location.href = '/';
    } else {
      showError(d.error || '账号或密码错误');
    }
  } catch(e) {
    showError('网络错误: ' + e.message);
  }
  btn.disabled = false;
  btn.textContent = '登 录';
  return false;
}

function showError(msg) {
  const el = document.getElementById('errorMsg');
  el.textContent = msg;
  el.style.display = 'block';
  setTimeout(() => el.style.display = 'none', 4000);
}

document.getElementById('password').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') doLogin();
});
</script>
</body>
</html>"""
