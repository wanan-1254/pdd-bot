"""
拼多多抢券脚本 - Web 管理面板
现代化仪表盘，实时显示抢券状态、日志、配置
"""

import os
import json
import time
import threading
from datetime import datetime, timezone, timedelta
from collections import deque

from flask import Flask, jsonify, request, Response
from auth import login_required, register_auth_routes, get_secret_key, get_username

# ============================================================
# 全局状态 & 日志
# ============================================================
BJT = timezone(timedelta(hours=8))

# 调度器引用 (main.py 启动后注册)
_scheduler_ref = None


def register_scheduler(scheduler):
    """注册调度器引用，供配置保存时更新触发时间"""
    global _scheduler_ref
    _scheduler_ref = scheduler
    print(f"[Dashboard] 调度器已注册: {scheduler}")


# 日志配置
import os as _os
_log_max = int(_os.getenv("LOG_MAX_COUNT", "0"))  # 0=不限制

# 共享状态 (main.py 会更新这个)
STATE = {
    "status": "idle",           # idle / waiting / grabbing / success / failed
    "token_valid": False,
    "user_id": "",
    "cookie_count": 0,
    "next_grab": "00:00:00",
    "grab_hour": 0,
    "grab_minute": 0,
    "grab_second": 0,
    "pre_start_sec": 10,
    "end_hour": 0,
    "end_minute": 0,
    "end_second": 30,
    "thread_count": 5,
    "total_grabs": 0,
    "success_grabs": 0,
    "last_grab_time": "",
    "last_grab_result": "",
    "ntp_offset_ms": 0,
    "uptime_start": time.time(),
    "query_interval_minutes": 120,
    "log_max_count": _log_max,  # 0=不限制
}

# 日志队列 (可通过面板配置，默认不限制)
LOGS = deque(maxlen=_log_max if _log_max > 0 else None)

# 抢券历史
HISTORY = deque(maxlen=200)


def add_log(level: str, module: str, message: str):
    """添加一条日志"""
    LOGS.append({
        "time": datetime.now(BJT).strftime("%H:%M:%S"),
        "level": level,       # info / warn / error / success
        "module": module,     # 系统 / 抢券 / 登录 / NTP
        "message": message,
    })


def _get_eligible_queue_info():
    """获取预筛选队列信息，供前端展示"""
    try:
        from main import _eligible_queue, _eligible_queue_time
        items = []
        for acc in (_eligible_queue or []):
            si = acc.get("sign_in", {})
            items.append({
                "id": acc.get("id", ""),
                "label": acc.get("label", "未命名"),
                "finish_count": si.get("finish_count", 0),
                "gain_award_count": si.get("gain_award_count", 0),
                "display_status": si.get("display_status", 0),
                "last_check": si.get("last_check", ""),
            })
        return {
            "items": items,
            "count": len(items),
            "screen_time": _eligible_queue_time or "",
        }
    except Exception:
        return {"items": [], "count": 0, "screen_time": ""}


def add_history(success: bool, detail: str):
    """添加一条抢券历史"""
    HISTORY.append({
        "time": datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S"),
        "success": success,
        "detail": detail,
    })
    STATE["total_grabs"] += 1
    if success:
        STATE["success_grabs"] += 1


# ============================================================
# Flask App
# ============================================================
app = Flask(__name__)
app.secret_key = get_secret_key()

# 注册鉴权路由
register_auth_routes(app)

DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>拼多多抢券 Bot</title>
<style>
:root {
  --bg: #F3F4F6; --card: #FFFFFF; --border: #E5E7EB;
  --text: #1F2937; --text2: #6B7280; --text3: #9CA3AF;
  --red: #EF4444; --red-bg: #FEF2F2;
  --green: #10B981; --green-bg: #ECFDF5;
  --blue: #3B82F6; --blue-bg: #EFF6FF;
  --purple: #8B5CF6; --purple-bg: #F5F3FF;
  --orange: #F59E0B; --orange-bg: #FFFBEB;
  --pink: #EC4899; --pink-bg: #FDF2F8;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
       background: var(--bg); color: var(--text); font-size: 14px; }

/* Layout */
.layout { display: flex; min-height: 100vh; }
.sidebar { width: 220px; background: var(--card); border-right: 1px solid var(--border);
           padding: 20px 0; flex-shrink: 0; position: fixed; height: 100vh; overflow-y: auto; }
.main { flex: 1; margin-left: 220px; padding: 24px; }

/* Sidebar */
.brand { padding: 0 20px 20px; border-bottom: 1px solid var(--border); margin-bottom: 12px; }
.brand h1 { font-size: 18px; color: var(--red); }
.brand small { color: var(--text3); font-size: 12px; }
.nav-item { display: flex; align-items: center; gap: 10px; padding: 10px 20px;
            cursor: pointer; color: var(--text2); transition: all 0.2s; border-left: 3px solid transparent; }
.nav-item:hover { background: var(--bg); color: var(--text); }
.nav-item.active { background: var(--red-bg); color: var(--red); border-left-color: var(--red); font-weight: 600; }
.nav-icon { font-size: 18px; width: 24px; text-align: center; }

/* Header */
.header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
.header h2 { font-size: 22px; }
.status-badge { display: inline-flex; align-items: center; gap: 6px; padding: 6px 14px;
                border-radius: 20px; font-size: 13px; font-weight: 500; }
.status-badge.idle { background: var(--bg); color: var(--text2); }
.status-badge.waiting { background: var(--blue-bg); color: var(--blue); }
.status-badge.grabbing { background: var(--orange-bg); color: var(--orange); animation: pulse 1s infinite; }
.status-badge.success { background: var(--green-bg); color: var(--green); }
.status-badge.failed { background: var(--red-bg); color: var(--red); }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.6} }
.dot { width: 8px; height: 8px; border-radius: 50%; }
.dot.green { background: var(--green); }
.dot.red { background: var(--red); }
.dot.orange { background: var(--orange); }

/* Cards Grid */
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 12px;
        padding: 18px; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }
.card-title { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text2); margin-bottom: 8px; }
.card-value { font-size: 24px; font-weight: 700; }
.card-sub { font-size: 12px; color: var(--text3); margin-top: 4px; }
.card-icon { font-size: 20px; }

/* Big Cards */
.big-cards { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }
.big-card { background: var(--card); border: 1px solid var(--border); border-radius: 12px;
            padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }
.big-card h3 { font-size: 15px; margin-bottom: 14px; display: flex; align-items: center; gap: 8px; }

/* Countdown */
.countdown { text-align: center; padding: 20px 0; }
.countdown-time { font-size: 40px; font-weight: 800; font-variant-numeric: tabular-nums; font-family: 'Courier New', monospace;
                  background: linear-gradient(135deg, var(--red), var(--pink));
                  -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.countdown-label { color: var(--text3); font-size: 13px; margin-top: 8px; }

/* Logs */
.log-list { max-height: 400px; overflow-y: auto; }
.log-item { display: flex; align-items: flex-start; gap: 10px; padding: 8px 12px;
            border-bottom: 1px solid var(--bg); font-size: 13px; }
.log-time { color: var(--text3); font-family: 'Courier New', monospace; white-space: nowrap; min-width: 65px; }
.log-tag { padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; white-space: nowrap; }
.log-tag.系统 { background: var(--green-bg); color: var(--green); }
.log-tag.抢券 { background: var(--red-bg); color: var(--red); }
.log-tag.签到 { background: var(--purple-bg); color: var(--purple); }
.log-tag.登录 { background: var(--blue-bg); color: var(--blue); }
.log-tag.NTP { background: var(--orange-bg); color: var(--orange); }
.log-tag.配置 { background: var(--orange-bg); color: var(--orange); }
.log-msg { flex: 1; word-break: break-all; }
.log-level-warn { color: var(--orange); }
.log-level-error { color: var(--red); font-weight: 600; }
.log-level-success { color: var(--green); font-weight: 600; }

/* 抢券请求高亮 */
.log-grab-request {
  background: linear-gradient(90deg, rgba(59,130,246,0.05) 0%, transparent 100%);
  border-left: 3px solid var(--blue);
  padding-left: 8px;
}
.log-grab-success { color: var(--green); font-weight: 600; }
.log-grab-fail { color: var(--red); font-weight: 600; }

/* History */
.history-item { display: flex; align-items: center; gap: 10px; padding: 10px 12px;
                border-bottom: 1px solid var(--bg); }
.history-badge { padding: 3px 10px; border-radius: 4px; font-size: 12px; font-weight: 600; }
.history-badge.ok { background: var(--green-bg); color: var(--green); }
.history-badge.fail { background: var(--red-bg); color: var(--red); }

/* Config */
.config-row { display: flex; justify-content: space-between; padding: 8px 0;
              border-bottom: 1px solid var(--bg); font-size: 13px; }
.config-key { color: var(--text2); }
.config-val { font-weight: 600; font-family: monospace; }

/* Progress */
.progress-bar { height: 6px; background: var(--bg); border-radius: 3px; margin-top: 8px; overflow: hidden; }
.progress-fill { height: 100%; border-radius: 3px; transition: width 0.3s; }

/* Bottom section */
.bottom-section { display: grid; grid-template-columns: 1fr 350px; gap: 16px; }

/* Responsive */
@media (max-width: 900px) {
  .sidebar { display: none; }
  .main { margin-left: 0; }
  .big-cards, .bottom-section { grid-template-columns: 1fr; }
}

/* Page Tabs */
.page { display: none; }
.page.active { display: block; }
.page-title { font-size: 20px; font-weight: 700; margin-bottom: 20px; display: flex; align-items: center; gap: 10px; }
.full-log-list { max-height: calc(100vh - 180px); overflow-y: auto; background: var(--card); border: 1px solid var(--border);
                 border-radius: 12px; padding: 16px; }
.config-table { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }
.config-table .config-row { padding: 12px 0; }
.account-card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 24px; max-width: 500px; }
.account-card h3 { margin-bottom: 16px; }
.account-row { display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid var(--bg); }
.account-label { color: var(--text2); }
.account-value { font-family: monospace; font-weight: 500; word-break: break-all; max-width: 300px; text-align: right; }
.token-preview { background: var(--bg); padding: 8px 12px; border-radius: 6px; font-family: monospace; font-size: 12px;
                 word-break: break-all; margin-top: 12px; color: var(--text2); }

/* Form Elements */
.form-group { margin-bottom: 16px; }
.form-label { display: block; font-size: 13px; color: var(--text2); margin-bottom: 6px; font-weight: 500; }
.form-input { width: 100%; padding: 10px 14px; border: 1px solid var(--border); border-radius: 8px; font-size: 14px;
              background: var(--card); color: var(--text); transition: border-color 0.2s; font-family: inherit; }
.form-input:focus { outline: none; border-color: var(--blue); box-shadow: 0 0 0 3px rgba(59,130,246,0.1); }
.form-input-sm { width: 120px; }
textarea.form-input { min-height: 100px; resize: vertical; font-family: monospace; font-size: 13px; }
.form-row { display: flex; align-items: center; gap: 12px; }
.form-row .form-label { margin-bottom: 0; min-width: 100px; }
.btn { padding: 10px 24px; border: none; border-radius: 8px; font-size: 14px; font-weight: 600;
       cursor: pointer; transition: all 0.2s; display: inline-flex; align-items: center; gap: 6px; }
.btn-primary { background: var(--blue); color: white; }
.btn-primary:hover { background: #2563EB; }
.btn-success { background: var(--green); color: white; }
.btn-success:hover { background: #059669; }
.btn-danger { background: var(--red); color: white; }
.btn-danger:hover { background: #DC2626; }
.btn-outline { background: var(--card); color: var(--text); border: 1px solid var(--border); }
.btn-outline:hover { background: var(--bg); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-group { display: flex; gap: 10px; margin-top: 16px; }
.toast { position: fixed; top: 20px; right: 20px; padding: 12px 20px; border-radius: 8px; color: white;
         font-size: 14px; font-weight: 500; z-index: 9999; animation: slideIn 0.3s ease; }
.toast.success { background: var(--green); }
.toast.error { background: var(--red); }
.toast.info { background: var(--blue); }
@keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
.test-result { margin-top: 12px; padding: 12px 16px; border-radius: 8px; font-size: 13px; }
.test-result.ok { background: var(--green-bg); color: var(--green); border: 1px solid #A7F3D0; }
.test-result.fail { background: var(--red-bg); color: var(--red); border: 1px solid #FCA5A5; }
.test-result.loading { background: var(--blue-bg); color: var(--blue); border: 1px solid #93C5FD; }
</style>
</head>
<body>
<div class="layout">
  <!-- Sidebar -->
  <div class="sidebar">
    <div class="brand">
      <h1>🎯 抢券 Bot</h1>
      <small>拼多多签到券自动抢</small>
    </div>
    <div class="nav-item active" data-page="overview"><span class="nav-icon">📊</span> 概览</div>
    <div class="nav-item" data-page="logs"><span class="nav-icon">📋</span> 运行日志</div>
    <div class="nav-item" data-page="history"><span class="nav-icon">📈</span> 抢券历史</div>
    <div class="nav-item" data-page="config"><span class="nav-icon">⚙️</span> 配置</div>
    <div class="nav-item" data-page="account"><span class="nav-icon">👤</span> 账号</div>
  </div>

  <!-- Main Content -->
  <div class="main">
    <!-- Header (always visible) -->
    <div class="header">
      <h2 id="pageTitle">控制面板</h2>
      <div id="statusBadge" class="status-badge idle">
        <span class="dot" id="statusDot"></span>
        <span id="statusText">加载中...</span>
      </div>
    </div>

    <!-- PAGE: 概览 -->
    <div class="page active" id="page-overview">
      <!-- Stats Cards -->
      <div class="cards">
        <div class="card">
          <div class="card-title"><span class="card-icon">🎫</span> 目标券</div>
          <div class="card-value" style="color:var(--red)">30元</div>
          <div class="card-sub">话费券 · 满200可用</div>
        </div>
        <div class="card">
          <div class="card-title"><span class="card-icon">🔑</span> Token</div>
          <div class="card-value" id="tokenStatus" style="font-size:16px">检测中...</div>
          <div class="card-sub" id="cookieInfo">-</div>
        </div>
        <div class="card">
          <div class="card-title"><span class="card-icon">⏱️</span> 时间同步</div>
          <div class="card-value" id="ntpOffset" style="font-size:18px">-</div>
          <div class="card-sub" id="timeSource">毫秒 (越小越好)</div>
        </div>
        <div class="card">
          <div class="card-title"><span class="card-icon">📊</span> 成功率</div>
          <div class="card-value" id="successRate">-</div>
          <div class="card-sub" id="grabCount">总抢券: 0 次</div>
          <div class="progress-bar"><div class="progress-fill" id="rateBar" style="width:0%;background:var(--green)"></div></div>
        </div>
      </div>
      <!-- Countdown + Account Timers -->
      <div class="big-cards">
        <div class="big-card">
          <h3>⏰ 下次抢券倒计时</h3>
          <div class="countdown">
            <div class="countdown-time" id="countdown">--:--:--.---</div>
            <div class="countdown-label" id="countdownLabel">加载中...</div>
          </div>
          <div id="syncStatus" style="margin-top:12px;padding:10px;background:var(--bg);border-radius:8px;font-size:12px;color:var(--text2);text-align:center">
            ⚙️ 时间同步加载中...
          </div>
        </div>
        <div class="big-card">
          <h3>👥 账号抢券倒计时</h3>
          <div id="accountTimers" style="max-height:200px;overflow-y:auto">
            <div style="text-align:center;color:var(--text3);padding:20px;font-size:12px">加载中...</div>
          </div>
        </div>
        <div class="big-card">
          <h3 style="display:flex;justify-content:space-between;align-items:center">
            <span>📋 预筛选队列 <span style="font-size:12px;color:var(--text3);font-weight:normal" id="queueCount">(0个)</span></span>
            <button class="btn btn-primary" style="padding:4px 14px;font-size:12px" onclick="manualPreScreen()">🔍 立即筛选</button>
          </h3>
          <div id="eligibleQueue" style="max-height:200px;overflow-y:auto">
            <div style="text-align:center;color:var(--text3);padding:20px;font-size:12px">队列暂无数据，点击"立即筛选"或等待23:50自动筛选</div>
          </div>
        </div>
      </div>
      <!-- Logs + History -->
      <div class="bottom-section">
        <div class="big-card">
          <h3 style="display:flex;justify-content:space-between;align-items:center">
            <span>📋 运行日志 <span style="font-size:12px;color:var(--text3);font-weight:normal" id="logCount">(0条)</span></span>
            <div style="display:flex;gap:6px;align-items:center">
              <label style="font-size:12px;color:var(--text2);cursor:pointer;display:flex;align-items:center;gap:4px">
                <input type="checkbox" id="autoFollowLogs" checked> 跟随最新
              </label>
              <label style="font-size:12px;color:var(--text2);cursor:pointer;display:flex;align-items:center;gap:4px;border-left:1px solid var(--border);padding-left:8px">
                <input type="checkbox" id="showDetailedLogs"> 显示详细请求
              </label>
              <button class="btn btn-outline" style="padding:4px 12px;font-size:12px" onclick="clearLogs()">🗑 清除</button>
            </div>
          </h3>
          <div class="log-list" id="logList" onscroll="onLogScroll(this)" style="max-height:350px;overflow-y:auto">
            <div style="text-align:center;color:var(--text3);padding:40px">加载中...</div>
          </div>
        </div>
        <div class="big-card">
          <h3 style="display:flex;justify-content:space-between;align-items:center">
            <span>📈 抢券历史</span>
            <button class="btn btn-outline" style="padding:4px 12px;font-size:12px" onclick="clearHistory()">🗑 清除</button>
          </h3>
          <div id="historyList" style="max-height:350px;overflow-y:auto">
            <div style="text-align:center;color:var(--text3);padding:40px">暂无记录</div>
          </div>
        </div>
      </div>
    </div>

    <!-- PAGE: 运行日志 -->
    <div class="page" id="page-logs">
      <div class="page-title" style="display:flex;justify-content:space-between;align-items:center">
        <span>📋 运行日志 <span style="font-size:14px;color:var(--text3);font-weight:normal" id="logCountFull">(0条)</span></span>
        <div style="display:flex;gap:8px;align-items:center">
          <label style="font-size:13px;color:var(--text2);cursor:pointer;display:flex;align-items:center;gap:4px">
            <input type="checkbox" id="autoFollowLogsFull" checked> 跟随最新
          </label>
          <label style="font-size:13px;color:var(--text2);cursor:pointer;display:flex;align-items:center;gap:4px;border-left:1px solid var(--border);padding-left:8px">
            <input type="checkbox" id="showDetailedLogsFull"> 显示详细请求
          </label>
          <button class="btn btn-outline" style="padding:6px 16px;font-size:13px" onclick="clearLogs()">🗑 清除日志</button>
        </div>
      </div>
      <div class="full-log-list" id="logListFull" onscroll="onLogScrollFull(this)">
        <div style="text-align:center;color:var(--text3);padding:40px">加载中...</div>
      </div>
    </div>

    <!-- PAGE: 抢券历史 -->
    <div class="page" id="page-history">
      <div class="page-title" style="display:flex;justify-content:space-between;align-items:center">
        <span>📈 抢券历史</span>
        <button class="btn btn-outline" style="padding:6px 16px;font-size:13px" onclick="clearHistory()">🗑 清除历史</button>
      </div>
      <div class="big-card" style="min-height:400px">
        <div id="historyListFull" style="max-height:calc(100vh - 200px);overflow-y:auto">
          <div style="text-align:center;color:var(--text3);padding:40px">暂无记录</div>
        </div>
      </div>
    </div>

    <!-- PAGE: 配置 -->
    <div class="page" id="page-config">
      <div class="page-title">⚙️ 工具</div>
      <div class="config-table" style="text-align:center;padding:30px">
        <p style="color:var(--text2);margin-bottom:16px">每个账号的抢券配置请在「账号」页面单独设置</p>
        <button class="btn btn-success" onclick="testGrab()" style="padding:14px 40px;font-size:16px">🚀 立即测试抢券</button>
        <p style="color:var(--text3);font-size:12px;margin-top:12px">跳过时间等待，直接发送抢券请求，用于测试账号是否可用</p>
      </div>
    </div>

    <!-- PAGE: 账号 -->
    <div class="page" id="page-account">
      <div class="page-title">👤 账号管理</div>

      <!-- 账号列表 -->
      <div class="account-card" style="max-width:800px;margin-bottom:16px">
        <h3 style="display:flex;justify-content:space-between;align-items:center">
          <span>📝 PDD 账号列表</span>
          <div style="display:flex;gap:6px;align-items:center">
            <span style="font-size:12px;color:var(--text3)" id="accCount">0 个账号</span>
            <button class="btn btn-outline" style="padding:4px 10px;font-size:11px" onclick="queryAllSignIn()">🔍 查询全部</button>
            <button class="btn btn-success" style="padding:4px 10px;font-size:11px" onclick="signInAll()">📝 全部签到</button>
          </div>
        </h3>
        <div id="accountList" style="margin-top:12px">
          <div style="text-align:center;color:var(--text3);padding:30px">加载中...</div>
        </div>
      </div>

      <!-- 添加/编辑账号 -->
      <div class="account-card" style="max-width:800px;margin-bottom:16px">
        <h3 id="accountFormTitle">➕ 添加账号</h3>
        <p style="font-size:12px;color:var(--text3);margin-bottom:12px">⚠️ 以下为拼多多抢券凭证（Token/Cookie），不是登录密码</p>
        <input type="hidden" id="editAccountId" value="">
        <div class="form-group">
          <label class="form-label">标签名 <span style="color:var(--text3);font-weight:normal">(如“大号”“小号”，可选)</span></label>
          <input class="form-input" id="inputLabel" type="text" placeholder="给账号取个名字" style="max-width:300px">
        </div>
        <div class="form-group">
          <label class="form-label">Access Token <span style="color:var(--text3);font-weight:normal">(PDDAccessToken，必填)</span></label>
          <input class="form-input" id="inputToken" type="text" placeholder="粘贴 PDDAccessToken 的值">
        </div>
        <div class="form-group">
          <label class="form-label">完整 Cookie 字符串 (可选)</label>
          <textarea class="form-input" id="inputCookies" placeholder="粘贴从抓包工具获取的完整 Cookie 字符串&#10;格式: key1=value1; key2=value2; ...&#10;如果只填 Access Token 也可以"></textarea>
        </div>
        <h4 style="margin:16px 0 10px;font-size:14px">🎯 此账号的抢券配置</h4>
        <div class="form-row" style="margin-bottom:10px">
          <span class="form-label" style="min-width:80px">目标时间</span>
          <input class="form-input form-input-sm" id="accGrabH" type="number" min="0" max="23" placeholder="时" style="width:60px">
          <span>:</span>
          <input class="form-input form-input-sm" id="accGrabM" type="number" min="0" max="59" placeholder="分" style="width:60px">
          <span>:</span>
          <input class="form-input form-input-sm" id="accGrabS" type="number" min="0" max="59" placeholder="秒" style="width:60px">
        </div>
        <div class="form-row" style="margin-bottom:10px">
          <span class="form-label" style="min-width:80px">提前开火</span>
          <input class="form-input form-input-sm" id="accPreSec" type="number" min="1" max="120" placeholder="秒" style="width:80px">
          <span>秒前开始</span>
        </div>
        <div class="form-row" style="margin-bottom:10px">
          <span class="form-label" style="min-width:80px">结束时间</span>
          <input class="form-input form-input-sm" id="accEndH" type="number" min="0" max="23" placeholder="时" style="width:60px">
          <span>:</span>
          <input class="form-input form-input-sm" id="accEndM" type="number" min="0" max="59" placeholder="分" style="width:60px">
          <span>:</span>
          <input class="form-input form-input-sm" id="accEndS" type="number" min="0" max="59" placeholder="秒" style="width:60px">
        </div>
        <div class="form-row" style="margin-bottom:10px">
          <span class="form-label" style="min-width:80px">并发线程</span>
          <input class="form-input form-input-sm" id="accThreads" type="number" min="1" max="20" placeholder="个" style="width:80px">
          <span>个线程持续发送</span>
        </div>
        <div class="btn-group">
          <button class="btn btn-primary" onclick="saveAccount()" id="btnSaveAcc">💾 保存账号</button>
          <button class="btn btn-outline" onclick="resetAccountForm()">↩ 重置表单</button>
          <button class="btn btn-outline" onclick="fillDefaultConfig()">📝 填充全局默认配置</button>
        </div>
        <div id="testResult" style="display:none"></div>
      </div>

      <!-- 自动查询设置 -->
      <div class="account-card" style="max-width:800px;margin-top:16px">
        <h3>🔄 自动查询设置</h3>
        <p style="font-size:12px;color:var(--text3);margin-bottom:12px">设置自动查询签到状态的间隔时间</p>
        <div class="form-row" style="margin-bottom:12px">
          <span class="form-label" style="min-width:100px">查询间隔</span>
          <select class="form-input" id="queryInterval" style="max-width:150px" onchange="saveQueryInterval()">
            <option value="30">30 分钟</option>
            <option value="60">1 小时</option>
            <option value="120" selected>2 小时</option>
            <option value="180">3 小时</option>
            <option value="360">6 小时</option>
            <option value="720">12 小时</option>
          </select>
        </div>
        <div id="queryIntervalInfo" style="font-size:12px;color:var(--text2);padding:8px 12px;background:var(--bg);border-radius:6px">
          当前: 每 2 小时自动查询一次签到状态
        </div>
        <div style="margin-top:16px;border-top:1px solid var(--border);padding-top:16px">
          <h3 style="font-size:14px">📝 日志保存设置</h3>
          <p style="font-size:12px;color:var(--text3);margin-bottom:12px">运行日志不会拖慢抢券速度，仅占用少量内存</p>
          <div class="form-row" style="margin-bottom:12px">
            <span class="form-label" style="min-width:100px">日志条数</span>
            <select class="form-input" id="logMaxCount" style="max-width:150px" onchange="saveLogMaxCount()">
              <option value="0">不限制（一直保存）</option>
              <option value="1000">1000 条</option>
              <option value="2000">2000 条</option>
              <option value="5000">5000 条</option>
              <option value="10000">10000 条</option>
              <option value="50000">50000 条</option>
            </select>
          </div>
          <div id="logMaxInfo" style="font-size:12px;color:var(--text2);padding:8px 12px;background:var(--bg);border-radius:6px">
            当前: 不限制，日志会一直保存
          </div>
        </div>
      </div>
      <!-- 安全设置 · 账号密码管理 -->
      <div class="account-card" style="max-width:800px;margin-top:16px">
        <h3>🔒 安全设置</h3>
        <div id="loginInfoBox" style="padding:10px 14px;background:var(--bg);border-radius:8px;border:1px solid var(--border);margin-bottom:12px;font-size:13px">
          当前登录账号: <strong id="currentLoginUser">加载中...</strong>
        </div>
        <div class="form-group">
          <label class="form-label">新用户名 <span style="color:var(--text3);font-weight:normal">(可选，不改则留空)</span></label>
          <input class="form-input" id="newLoginUser" type="text" placeholder="新的登录用户名，如 admin2" style="max-width:300px">
        </div>
        <div class="form-group">
          <label class="form-label">新密码</label>
          <input class="form-input" id="pwdNew" type="password" placeholder="新密码（至少4位）" style="max-width:300px">
        </div>
        <div class="form-group">
          <label class="form-label">确认新密码</label>
          <input class="form-input" id="pwdConfirm" type="password" placeholder="再次输入新密码" style="max-width:300px">
        </div>
        <div class="form-group">
          <label class="form-label">当前密码</label>
          <input class="form-input" id="pwdOld" type="password" placeholder="输入当前密码以确认身份" style="max-width:300px">
        </div>
        <div class="btn-group">
          <button class="btn btn-primary" onclick="changeLoginCredentials()">🔒 修改账号密码</button>
          <button class="btn btn-danger" onclick="logout()" style="margin-left:10px">🚪 退出登录</button>
        </div>
        <div id="pwdResult" style="display:none;margin-top:12px"></div>
      </div>
    </div>
  </div>
</div>

<script>
const STATUS_MAP = {
  idle: {text: '待机中', cls: 'idle', dot: ''},
  waiting: {text: '等待抢券', cls: 'waiting', dot: 'orange'},
  grabbing: {text: '抢券中...', cls: 'grabbing', dot: 'orange'},
  success: {text: '抢券成功', cls: 'success', dot: 'green'},
  failed: {text: '抢券失败', cls: 'failed', dot: 'red'},
};

// === Navigation===
const PAGE_TITLES = {
  overview: '控制面板', logs: '运行日志', history: '抢券历史',
  config: '配置详情', account: '账号管理'
};
document.querySelectorAll('.nav-item').forEach(item => {
  item.addEventListener('click', () => {
    const page = item.dataset.page;
    if (!page) return;
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    item.classList.add('active');
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    const el = document.getElementById('page-' + page);
    if (el) el.classList.add('active');
    document.getElementById('pageTitle').textContent = PAGE_TITLES[page] || '控制面板';
    // 切换到账号页时加载登录信息（需要已登录）
    if (page === 'account') {
      fetch('/api/get-login-info').then(r => r.json()).then(d => {
        const el = document.getElementById('currentLoginUser');
        if (el) el.textContent = d.username || '(未知)';
      }).catch(() => {});
    }
  });
});

function updateDashboard(data) {
  const s = STATUS_MAP[data.status] || STATUS_MAP.idle;
  document.getElementById('statusBadge').className = 'status-badge ' + s.cls;
  document.getElementById('statusDot').className = 'dot ' + s.dot;
  document.getElementById('statusText').textContent = s.text;
  document.getElementById('tokenStatus').innerHTML = data.token_valid
    ? '<span style="color:var(--green)">✓ 有效</span>'
    : '<span style="color:var(--red)">✗ 无效</span>';
  document.getElementById('cookieInfo').textContent =
    `UserID: ${data.user_id || '-'} · Cookie: ${data.cookie_count}个`;
  document.getElementById('ntpOffset').textContent = (data.sync && data.sync.offset_ms !== undefined)
    ? (data.sync.offset_ms >= 0 ? '+' : '') + data.sync.offset_ms.toFixed(2) + ' ms'
    : data.ntp_offset_ms.toFixed(1) + ' ms';
  const srcMap = {pdd: '🎯 PDD服务器', ntp: '🌐 NTP', local: '⚠️ 本地'};
  const src = data.sync ? data.sync.source : (data.time_source || 'local');
  const samples = data.sync ? data.sync.samples : 0;
  const rtt = data.sync ? data.sync.last_rtt_ms : 0;
  document.getElementById('timeSource').innerHTML =
    (srcMap[src] || '本地') + ' | RTT ' + (rtt || 0).toFixed(0) + 'ms | ' + samples + '次采样';
  const total = data.total_grabs || 0, succ = data.success_grabs || 0;
  const rate = total > 0 ? ((succ / total) * 100).toFixed(0) + '%' : '-';
  document.getElementById('successRate').textContent = rate;
  document.getElementById('grabCount').textContent = `成功 ${succ} / 总计 ${total} 次`;
  document.getElementById('rateBar').style.width = (total > 0 ? (succ/total)*100 : 0) + '%';
  // 存储全局配置 (供 fillDefaultConfig 使用)
  window._globalGrabH = data.grab_hour;
  window._globalGrabM = data.grab_minute;
  window._globalGrabS = data.grab_second;
  window._globalPreSec = data.pre_start_sec || 10;
  window._globalEndH = data.end_hour || 0;
  window._globalEndM = data.end_minute || 0;
  window._globalEndS = data.end_second || 30;
  window._globalThreads = data.thread_count || 5;
  // Account page: 渲染账号列表
  if (data.accounts !== undefined) {
    renderAccountList(data.accounts);
  }
  // 回显查询间隔
  const qiEl = document.getElementById('queryInterval');
  if (qiEl && !qiEl.dataset.filled) {
    qiEl.value = String(data.query_interval_minutes || 120);
    qiEl.dataset.filled = '1';
    const labels = {30:'30分钟',60:'1小时',120:'2小时',180:'3小时',360:'6小时',720:'12小时'};
    const infoEl = document.getElementById('queryIntervalInfo');
    if (infoEl) infoEl.textContent = '当前: 每 ' + (labels[qiEl.value]||qiEl.value+'分钟') + ' 自动查询一次签到状态';
  }
  // 回显日志保存条数
  const lmEl = document.getElementById('logMaxCount');
  if (lmEl && !lmEl.dataset.filled) {
    lmEl.value = String(data.log_max_count || 0);
    lmEl.dataset.filled = '1';
    const logLabels = {0:'不限制，日志会一直保存',1000:'1000条',2000:'2000条',5000:'5000条',10000:'10000条',50000:'50000条'};
    const logInfoEl = document.getElementById('logMaxInfo');
    if (logInfoEl) logInfoEl.textContent = '当前: ' + (logLabels[lmEl.value]||lmEl.value+'条');
  }
}

// === Toast Notification ===
function showToast(msg, type='info') {
  const t = document.createElement('div');
  t.className = 'toast ' + type;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}

async function testGrab() {
  if (!confirm('立即触发一次抢券测试？（跳过时间等待，直接发送请求）')) return;
  try {
    const r = await fetch('/api/test-grab', { method:'POST' });
    const d = await r.json();
    if (d.success) showToast('🚀 抢券已触发！请查看日志', 'success');
    else showToast('触发失败: ' + (d.error||''), 'error');
  } catch(e) { showToast('请求失败: '+e.message, 'error'); }
}

// === Auth & Security ===
async function changePassword() {
  // 兼容旧调用，转发到 changeLoginCredentials
  return changeLoginCredentials();
}

async function changeLoginCredentials() {
  const oldPw = document.getElementById('pwdOld').value;
  const newPw = document.getElementById('pwdNew').value;
  const confirm = document.getElementById('pwdConfirm').value;
  const newUser = document.getElementById('newLoginUser').value.trim();

  if (!oldPw) { showToast('请输入当前密码确认身份', 'error'); return; }
  if (newPw && newPw.length < 4) { showToast('新密码至少4位', 'error'); return; }
  if (newPw && newPw !== confirm) { showToast('两次新密码不一致', 'error'); return; }
  if (!newPw && !newUser) { showToast('至少要修改用户名或密码', 'error'); return; }

  try {
    const r = await fetch('/api/change-password', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({old_password: oldPw, new_password: newPw || undefined, new_username: newUser || undefined})
    });
    const d = await r.json();
    const el = document.getElementById('pwdResult');
    el.style.display = 'block';
    if (d.success) {
      el.className = 'test-result ok';
      el.innerHTML = '✅ 登录凭证已更新！下次登录请使用新凭证。';
      fetch('/api/get-login-info').then(r2 => r2.json()).then(d2 => {
        document.getElementById('currentLoginUser').textContent = d2.username || '(未知)';
      });
      document.getElementById('pwdOld').value = '';
      document.getElementById('pwdNew').value = '';
      document.getElementById('pwdConfirm').value = '';
      document.getElementById('newLoginUser').value = '';
      showToast('✅ 账号密码修改成功', 'success');
    } else {
      el.className = 'test-result fail';
      el.innerHTML = '❌ ' + (d.error||'修改失败');
      showToast('❌ ' + (d.error||'修改失败'), 'error');
    }
  } catch(e) {
    showToast('请求失败: '+e.message, 'error');
  }
}

async function logout() {
  if (!confirm('确定退出登录？')) return;
  await fetch('/api/logout', { method:'POST' });
  window.location.href = '/login';
}

// === Account Functions (Multi-Account) ===
let _accountsCache = [];
let _accTestResults = {};  // 缓存测试结果，防止轮询覆盖

function renderAccountList(accounts) {
  _accountsCache = accounts || [];
  const el = document.getElementById('accountList');
  const countEl = document.getElementById('accCount');
  const enabledCount = _accountsCache.filter(a => a.enabled).length;
  if (countEl) countEl.textContent = `${_accountsCache.length} 个账号 / ${enabledCount} 个启用`;
  if (!_accountsCache.length) {
    el.innerHTML = '<div style="text-align:center;color:var(--text3);padding:30px">暂无账号，请在下方添加</div>';
    return;
  }
  el.innerHTML = '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px">' +
    _accountsCache.map(a => {
    const cfg = a.config || {};
    const si = a.sign_in || {};
    const fc = si.finish_count || 0;
    const gc = si.gain_award_count || 0;
    const ds = si.display_status || 0;
    const canGrab = si.can_grab || false;
    const canSign = si.can_sign || false;
    const autoSign = si.auto_sign_in !== false;
    const tokenPre = (a.access_token||'').substring(0,12) + '...';
    const cfgStr = `${String(cfg.grab_hour||0).padStart(2,'0')}:${String(cfg.grab_minute||0).padStart(2,'0')}:${String(cfg.grab_second||0).padStart(2,'0')} 提前${cfg.pre_start_sec||10}s`;

    // 签到进度条 (5天)
    let progressHtml = '<div style="display:flex;align-items:center;gap:4px;margin:8px 0">';
    for (let i = 1; i <= 5; i++) {
      const done = i <= fc;
      const color = done ? 'var(--green)' : 'var(--border)';
      const icon = done ? '✅' : '○';
      progressHtml += `<div style="text-align:center;flex:1"><div style="font-size:16px;color:${color}">${icon}</div><div style="font-size:10px;color:var(--text3)">第${i}天</div></div>`;
      if (i < 5) progressHtml += `<div style="flex:0.5;height:2px;background:${i < fc ? 'var(--green)' : 'var(--border)'};margin-bottom:14px"></div>`;
    }
    progressHtml += '</div>';

    // 状态标签 + 抢券资格标签
    let statusBadges = '';
    if (ds === 40) {
      statusBadges = '<span style="background:var(--orange-bg);color:var(--orange);padding:2px 8px;border-radius:4px;font-size:11px">已领取·等待重置</span>'
        + '<span style="background:var(--red-bg);color:var(--red);padding:2px 8px;border-radius:4px;font-size:11px;margin-left:4px">🚫 不可抢</span>';
    } else if (fc >= 5) {
      // 签到满5天 = 可抢券 (ds=31未领券、gc<fc未领、都一样直接抢)
      statusBadges = '<span style="background:var(--green-bg);color:var(--green);padding:3px 10px;border-radius:4px;font-size:12px;font-weight:700;border:1px solid var(--green)">✅ 可抢券</span>';
    } else if (fc > 0) {
      statusBadges = `<span style="background:var(--bg);color:var(--text2);padding:2px 8px;border-radius:4px;font-size:11px">签到中 ${fc}/5天</span>`
        + '<span style="background:var(--red-bg);color:var(--red);padding:2px 8px;border-radius:4px;font-size:11px;margin-left:4px">🚫 不可抢</span>';
    } else {
      statusBadges = '<span style="background:var(--bg);color:var(--text2);padding:2px 8px;border-radius:4px;font-size:11px">未开始</span>'
        + '<span style="background:var(--red-bg);color:var(--red);padding:2px 8px;border-radius:4px;font-size:11px;margin-left:4px">🚫 不可抢</span>';
    }

    return `
      <div style="background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px;${!a.enabled?'opacity:0.5;':''}">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">
          <div>
            <div style="font-weight:700;font-size:15px;display:flex;align-items:center;gap:8px">
              <span style="width:32px;height:32px;border-radius:50%;background:var(--red-bg);display:flex;align-items:center;justify-content:center;font-size:16px">👤</span>
              ${a.label || '未命名'}
            </div>
            <div style="font-size:11px;color:var(--text3);margin-top:4px;margin-left:40px;font-family:monospace">${tokenPre}</div>
          </div>
          <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
            ${statusBadges}
            <label style="display:flex;align-items:center;gap:3px;font-size:11px;cursor:pointer;color:var(--text2)">
              <input type="checkbox" ${a.enabled ? 'checked' : ''} onchange="toggleAccount('${a.id}', this.checked)"> 启用
            </label>
          </div>
        </div>
        ${progressHtml}
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px;flex-wrap:wrap;gap:6px">
          <div style="font-size:11px;color:var(--text3)">🕐 ${cfgStr} | ${cfg.thread_count||5}线程</div>
          <div style="display:flex;gap:4px;flex-wrap:wrap">
            <button class="btn btn-outline" style="padding:3px 8px;font-size:11px" onclick="testAccountCookie('${a.id}','${a.label||''}')" title="测试Cookie">🍪</button>
            <button class="btn btn-outline" style="padding:3px 8px;font-size:11px" onclick="testAccountGrab('${a.id}','${a.label||''}')" title="测试抢券">🎯</button>
            <button class="btn btn-outline" style="padding:3px 8px;font-size:11px" onclick="queryAccountSignIn('${a.id}','${a.label||''}')" title="查询签到状态">🔍</button>
            <button class="btn btn-outline" style="padding:3px 8px;font-size:11px" onclick="doAccountSignIn('${a.id}','${a.label||''}')" title="手动签到">📝</button>
            <button class="btn btn-outline" style="padding:3px 8px;font-size:11px" onclick="editAccount('${a.id}')" title="编辑">✏️</button>
            <button class="btn btn-outline" style="padding:3px 8px;font-size:11px;color:var(--red)" onclick="delAccount('${a.id}')" title="删除">🗑</button>
          </div>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px">
          <label style="font-size:11px;color:var(--text2);cursor:pointer;display:flex;align-items:center;gap:3px">
            <input type="checkbox" ${autoSign ? 'checked' : ''} onchange="toggleAutoSignIn('${a.id}', this.checked)"> 自动签到
          </label>
          ${si.last_check ? `<span style="font-size:10px;color:var(--text3)">上次检查: ${si.last_check}</span>` : ''}
        </div>
        <div id="accResult_${a.id}" style="display:none;margin-top:8px;padding:8px 12px;border-radius:6px;font-size:12px"></div>
      </div>
    `;
  }).join('') + '</div>';
  // 恢复缓存的测试结果
  setTimeout(() => {
    for (const [id, res] of Object.entries(_accTestResults)) {
      const el = document.getElementById('accResult_' + id);
      if (el) { el.style.display = 'block'; el.innerHTML = res.html; el.style.background = res.bg; el.style.color = res.color; el.style.border = res.border; }
    }
  }, 10);
}

async function saveAccount() {
  const editId = document.getElementById('editAccountId').value;
  const label = document.getElementById('inputLabel').value.trim();
  const token = document.getElementById('inputToken').value.trim();
  const cookiesStr = document.getElementById('inputCookies').value.trim();
  const config = {
    grab_hour: parseInt(document.getElementById('accGrabH').value) || 0,
    grab_minute: parseInt(document.getElementById('accGrabM').value) || 0,
    grab_second: parseInt(document.getElementById('accGrabS').value) || 0,
    pre_start_sec: parseInt(document.getElementById('accPreSec').value) || 10,
    end_hour: parseInt(document.getElementById('accEndH').value) || 0,
    end_minute: parseInt(document.getElementById('accEndM').value) || 0,
    end_second: parseInt(document.getElementById('accEndS').value) || 30,
    thread_count: parseInt(document.getElementById('accThreads').value) || 5,
  };

  if (editId) {
    // 更新现有账号
    try {
      const r = await fetch(`/api/accounts/${editId}`, {
        method:'PUT', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ label, access_token: token, cookie_string: cookiesStr, config })
      });
      const d = await r.json();
      if (d.success) { showToast('账号已更新!', 'success'); resetAccountForm(); }
      else showToast('更新失败: ' + (d.error||''), 'error');
    } catch(e) { showToast('请求失败: '+e.message, 'error'); }
  } else {
    // 新增账号
    if (!token && !cookiesStr) { showToast('请至少填写 Access Token 或 Cookie', 'error'); return; }
    try {
      const r = await fetch('/api/accounts', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ access_token: token, cookie_string: cookiesStr, label, config })
      });
      const d = await r.json();
      if (d.success) { showToast('账号已添加!', 'success'); resetAccountForm(); }
      else showToast('添加失败: ' + (d.error||''), 'error');
    } catch(e) { showToast('请求失败: '+e.message, 'error'); }
  }
}

function resetAccountForm() {
  document.getElementById('editAccountId').value = '';
  document.getElementById('inputLabel').value = '';
  document.getElementById('inputToken').value = '';
  document.getElementById('inputCookies').value = '';
  document.getElementById('accGrabH').value = '';
  document.getElementById('accGrabM').value = '';
  document.getElementById('accGrabS').value = '';
  document.getElementById('accPreSec').value = '';
  document.getElementById('accEndH').value = '';
  document.getElementById('accEndM').value = '';
  document.getElementById('accEndS').value = '';
  document.getElementById('accThreads').value = '';
  document.getElementById('accountFormTitle').textContent = '➕ 添加账号';
  document.getElementById('btnSaveAcc').textContent = '💾 保存账号';
  const tr = document.getElementById('testResult');
  if (tr) tr.style.display = 'none';
}

function fillDefaultConfig() {
  // 从全局 STATE 填充默认配置
  document.getElementById('accGrabH').value = window._globalGrabH || 0;
  document.getElementById('accGrabM').value = window._globalGrabM || 0;
  document.getElementById('accGrabS').value = window._globalGrabS || 0;
  document.getElementById('accPreSec').value = window._globalPreSec || 10;
  document.getElementById('accEndH').value = window._globalEndH || 0;
  document.getElementById('accEndM').value = window._globalEndM || 0;
  document.getElementById('accEndS').value = window._globalEndS || 30;
  document.getElementById('accThreads').value = window._globalThreads || 5;
  showToast('已填充全局默认配置', 'info');
}

function editAccount(id) {
  const acc = _accountsCache.find(a => a.id === id);
  if (!acc) return;
  document.getElementById('editAccountId').value = id;
  document.getElementById('inputLabel').value = acc.label || '';
  document.getElementById('inputToken').value = acc.access_token || '';
  const cookieStr = Object.entries(acc.cookies||{}).map(([k,v]) => k+'='+v).join('; ');
  document.getElementById('inputCookies').value = cookieStr;
  const cfg = acc.config || {};
  document.getElementById('accGrabH').value = cfg.grab_hour || 0;
  document.getElementById('accGrabM').value = cfg.grab_minute || 0;
  document.getElementById('accGrabS').value = cfg.grab_second || 0;
  document.getElementById('accPreSec').value = cfg.pre_start_sec || 10;
  document.getElementById('accEndH').value = cfg.end_hour || 0;
  document.getElementById('accEndM').value = cfg.end_minute || 0;
  document.getElementById('accEndS').value = cfg.end_second || 30;
  document.getElementById('accThreads').value = cfg.thread_count || 5;
  document.getElementById('accountFormTitle').textContent = '✏️ 编辑账号: ' + (acc.label || acc.id);
  document.getElementById('btnSaveAcc').textContent = '💾 更新账号';
  // 滚动到表单
  document.getElementById('inputLabel').scrollIntoView({behavior:'smooth'});
}

async function delAccount(id) {
  const acc = _accountsCache.find(a => a.id === id);
  if (!confirm(`确定删除账号“${acc ? acc.label : id}”？`)) return;
  try {
    const r = await fetch(`/api/accounts/${id}`, { method:'DELETE' });
    const d = await r.json();
    if (d.success) showToast('账号已删除', 'success');
    else showToast('删除失败: ' + (d.error||''), 'error');
  } catch(e) { showToast('请求失败: '+e.message, 'error'); }
}

async function toggleAccount(id, enabled) {
  try {
    await fetch(`/api/accounts/${id}/toggle`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({enabled})
    });
  } catch(e) { console.error(e); }
}

async function testAccountCookie(id, label) {
  const loadingHtml = `🔄 正在测试“${label}”的 Cookie...`;
  const loadingBg = 'var(--blue-bg)'; const loadingColor = 'var(--blue)'; const loadingBorder = '1px solid #93C5FD';
  _accTestResults[id] = {html: loadingHtml, bg: loadingBg, color: loadingColor, border: loadingBorder};
  const result = document.getElementById('accResult_' + id);
  if (result) {
    result.style.display = 'block';
    result.style.background = loadingBg; result.style.color = loadingColor; result.style.border = loadingBorder;
    result.innerHTML = loadingHtml;
  }
  try {
    const r = await fetch('/api/test-cookie', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({account_id: id})
    });
    const d = await r.json();
    let html, bg, color, border;
    if (d.valid) {
      html = `✅ <strong>Cookie 有效!</strong> ${d.nickname||''}`;
      bg = 'var(--green-bg)'; color = 'var(--green)'; border = '1px solid #A7F3D0';
    } else {
      html = `❌ <strong>Cookie 无效</strong> ${d.error||''}`;
      bg = 'var(--red-bg)'; color = 'var(--red)'; border = '1px solid #FCA5A5';
    }
    _accTestResults[id] = {html, bg, color, border};
    const el = document.getElementById('accResult_' + id);
    if (el) { el.style.display = 'block'; el.style.background = bg; el.style.color = color; el.style.border = border; el.innerHTML = html; }
    setTimeout(() => {
      delete _accTestResults[id];
      const el2 = document.getElementById('accResult_' + id);
      if (el2) { el2.style.display = 'none'; el2.innerHTML = ''; }
    }, 5000);
  } catch(e) {
    const errHtml = '❌ 请求失败: ' + e.message;
    _accTestResults[id] = {html: errHtml, bg: 'var(--red-bg)', color: 'var(--red)', border: '1px solid #FCA5A5'};
    const el = document.getElementById('accResult_' + id);
    if (el) { el.style.display = 'block'; el.style.background = 'var(--red-bg)'; el.style.color = 'var(--red)'; el.style.border = '1px solid #FCA5A5'; el.innerHTML = errHtml; }
    setTimeout(() => { delete _accTestResults[id]; const el2 = document.getElementById('accResult_' + id); if (el2) { el2.style.display = 'none'; } }, 5000);
  }
}

async function testAccountGrab(id, label) {
  if (!confirm(`立即为“${label}”触发抢券测试？（跳过时间等待，直接发送请求）`)) return;
  const loadingHtml = `🔄 正在为“${label}”测试抢券...`;
  const loadingBg = 'var(--blue-bg)'; const loadingColor = 'var(--blue)'; const loadingBorder = '1px solid #93C5FD';
  // 立即缓存加载状态，防止轮询重绘覆盖
  _accTestResults[id] = {html: loadingHtml, bg: loadingBg, color: loadingColor, border: loadingBorder};
  const result = document.getElementById('accResult_' + id);
  if (result) {
    result.style.display = 'block';
    result.style.background = loadingBg; result.style.color = loadingColor; result.style.border = loadingBorder;
    result.innerHTML = loadingHtml;
  }
  try {
    const r = await fetch('/api/test-grab-account', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({account_id: id})
    });
    const d = await r.json();
    let html, bg, color, border;
    if (d.success) {
      html = `✅ <strong>抢券成功!</strong> ${d.detail||''}`;
      bg = 'var(--green-bg)'; color = 'var(--green)'; border = '1px solid #A7F3D0';
    } else {
      html = `⚠️ <strong>${d.detail||'抢券结束'}</strong> (${d.total_requests||0}个请求)`;
      bg = 'var(--orange-bg)'; color = 'var(--orange)'; border = '1px solid #FCD34D';
    }
    // 更新缓存和当前div
    _accTestResults[id] = {html, bg, color, border};
    const el = document.getElementById('accResult_' + id);
    if (el) {
      el.style.display = 'block'; el.style.background = bg; el.style.color = color; el.style.border = border; el.innerHTML = html;
    }
    setTimeout(() => {
      delete _accTestResults[id];
      const el2 = document.getElementById('accResult_' + id);
      if (el2) { el2.style.display = 'none'; el2.innerHTML = ''; }
    }, 8000);
  } catch(e) {
    const errHtml = '❌ 请求失败: ' + e.message;
    _accTestResults[id] = {html: errHtml, bg: 'var(--red-bg)', color: 'var(--red)', border: '1px solid #FCA5A5'};
    const el = document.getElementById('accResult_' + id);
    if (el) {
      el.style.display = 'block'; el.style.background = 'var(--red-bg)'; el.style.color = 'var(--red)'; el.style.border = '1px solid #FCA5A5'; el.innerHTML = errHtml;
    }
    setTimeout(() => { delete _accTestResults[id]; const el2 = document.getElementById('accResult_' + id); if (el2) { el2.style.display = 'none'; } }, 8000);
  }
}

async function queryAccountSignIn(id, label) {
  try {
    showToast(`正在查询“${label}”的签到状态...`, 'info');
    const r = await fetch(`/api/accounts/${id}/sign-in/query`, { method:'POST' });
    const d = await r.json();
    if (d.success) {
      const fc = d.finish_count || 0;
      const gc = d.gain_award_count || 0;
      const canGrab = d.can_grab ? '✅可抢券' : '❌不可抢券';
      const canSign = d.can_sign ? '✅可签到' : '❌不可签到';
      showToast(`[${label}] 签到${fc}/5天 | 领奖${gc}次 | ${canSign} | ${canGrab}`, 'success');
    } else {
      showToast(`查询失败: ${d.error||'未知错误'}`, 'error');
    }
  } catch(e) { showToast('请求失败: '+e.message, 'error'); }
}

async function doAccountSignIn(id, label) {
  try {
    showToast(`正在为“${label}”签到...`, 'info');
    const r = await fetch(`/api/accounts/${id}/sign-in`, { method:'POST' });
    const d = await r.json();
    if (d.success) {
      showToast(`[${label}] ${d.message}`, 'success');
    } else {
      showToast(`签到失败: ${d.message||d.error||''}`, 'error');
    }
  } catch(e) { showToast('请求失败: '+e.message, 'error'); }
}

async function toggleAutoSignIn(id, enabled) {
  try {
    // Update the sign_in.auto_sign_in field
    await fetch(`/api/accounts/${id}`, {
      method:'PUT', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ sign_in: { ...(_accountsCache.find(a=>a.id===id)||{}).sign_in, auto_sign_in: enabled } })
    });
  } catch(e) { console.error(e); }
}

async function queryAllSignIn() {
  try {
    showToast('正在查询所有账号签到状态...', 'info');
    const r = await fetch('/api/query-all-sign-in', { method:'POST' });
    const d = await r.json();
    if (d.success) {
      showToast(`已查询 ${d.results.length} 个账号`, 'success');
    } else {
      showToast(`查询失败: ${d.error||''}`, 'error');
    }
  } catch(e) { showToast('请求失败: '+e.message, 'error'); }
}

async function signInAll() {
  try {
    showToast('正在为所有账号签到...', 'info');
    const r = await fetch('/api/sign-in-all', { method:'POST' });
    const d = await r.json();
    if (d.success) {
      const msgs = (d.results||[]).map(r => `${r.label}: ${r.action}`).join('\n');
      showToast(`签到完成!\n${msgs}`, 'success');
    } else {
      showToast(`签到失败: ${d.error||''}`, 'error');
    }
  } catch(e) { showToast('请求失败: '+e.message, 'error'); }
}

async function saveQueryInterval() {
  const val = document.getElementById('queryInterval').value;
  try {
    const r = await fetch('/api/query-interval', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({interval_minutes: parseInt(val)})
    });
    const d = await r.json();
    if (d.success) {
      const labels = {30:'30分钟',60:'1小时',120:'2小时',180:'3小时',360:'6小时',720:'12小时'};
      document.getElementById('queryIntervalInfo').textContent = '当前: 每 ' + (labels[val]||val+'分钟') + ' 自动查询一次签到状态';
      showToast('查询间隔已更新', 'success');
    } else {
      showToast('更新失败: '+(d.error||''), 'error');
    }
  } catch(e) { showToast('请求失败: '+e.message, 'error'); }
}

async function saveLogMaxCount() {
  const val = document.getElementById('logMaxCount').value;
  try {
    const r = await fetch('/api/log-max', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({max_count: parseInt(val)})
    });
    const d = await r.json();
    if (d.success) {
      const labels = {0:'不限制，日志会一直保存',1000:'1000条',2000:'2000条',5000:'5000条',10000:'10000条',50000:'50000条'};
      document.getElementById('logMaxInfo').textContent = '当前: ' + (labels[val]||val+'条');
      showToast('日志保存设置已更新', 'success');
    } else {
      showToast('更新失败: '+(d.error||''), 'error');
    }
  } catch(e) { showToast('请求失败: '+e.message, 'error'); }
}

// 【修复】辅助函数：用北京时间(H:M:S)计算下一个目标时间戳
// 关键: Date.UTC 构造的是 UTC 时间，减去 8h 偏移转换为北京时间
function calcTargetTimestamp(gh, gm, gs, correctedNow) {
  // correctedNow 已代表北京时间，其 UTC 日期分量就是北京日期
  const d = new Date(correctedNow);
  const bjYear = d.getUTCFullYear();
  const bjMonth = d.getUTCMonth();
  const bjDay = d.getUTCDate();
  
  // 构造"今天北京时间 gh:gm:gs"的绝对时间戳
  const targetMs = Date.UTC(bjYear, bjMonth, bjDay, gh, gm, gs, 0) - 8 * 3600000;
  
  if (correctedNow >= targetMs) {
    return targetMs + 86400000; // 推到明天
  }
  return targetMs;
}

function updateCountdown(data) {
  // 【修复】计算所有账号中最近的下一个抢券时间 (全部用UTC时间戳)
  const accounts = data.accounts || [];
  const enabledAccounts = accounts.filter(a => a.enabled !== false);
  
  let nearestTargetMs = null;
  let nearestAccLabel = '全局默认';
  
  // 先更新时间偏移
  window._timeOffset = (data.sync && data.sync.offset_ms !== undefined) ? data.sync.offset_ms : (data.ntp_offset_ms || 0);
  const offset = window._timeOffset;
  const correctedNow = Date.now() + offset;
  
  if (enabledAccounts.length > 0) {
    for (const acc of enabledAccounts) {
      const cfg = acc.config || {};
      const gh = cfg.grab_hour !== undefined ? cfg.grab_hour : (data.grab_hour || 0);
      const gm = cfg.grab_minute !== undefined ? cfg.grab_minute : (data.grab_minute || 0);
      const gs = cfg.grab_second !== undefined ? cfg.grab_second : (data.grab_second || 0);
      const preSec = cfg.pre_start_sec !== undefined ? cfg.pre_start_sec : 10;
      
      let totalSec = gh * 3600 + gm * 60 + gs - preSec;
      if (totalSec < 0) totalSec += 86400;
      const tH = Math.floor(totalSec / 3600) % 24;
      const tM = Math.floor((totalSec % 3600) / 60);
      const tS = totalSec % 60;
      
      const targetMs = calcTargetTimestamp(tH, tM, tS, correctedNow);
      
      if (nearestTargetMs === null || targetMs < nearestTargetMs) {
        nearestTargetMs = targetMs;
        nearestAccLabel = acc.label || `账号${acc.id}`;
      }
    }
  } else {
    const gh = data.grab_hour || 0, gm = data.grab_minute || 0, gs = data.grab_second || 0;
    nearestTargetMs = calcTargetTimestamp(gh, gm, gs, correctedNow);
  }
  
  // 【关键修复】直接存储目标时间戳(ms)，tickCountdown直接使用，不再通过setHours
  window._targetTime = nearestTargetMs;
  window._grabStatus = data.status || 'idle';
  
  // 从目标时间戳还原北京时间显示
  const targetDate = new Date(nearestTargetMs);
  const dateStr = targetDate.toLocaleDateString('zh-CN', {month:'long', day:'numeric', timeZone:'Asia/Shanghai'});
  const timeStr = targetDate.toLocaleTimeString('zh-CN', {hour12:false, timeZone:'Asia/Shanghai'});
  document.getElementById('countdownLabel').textContent = `${dateStr} ${timeStr} (${nearestAccLabel})`;
}

// 更新预筛选队列展示
function updateEligibleQueue(queue) {
  const el = document.getElementById('eligibleQueue');
  const countEl = document.getElementById('queueCount');
  if (!el) return;

  if (!queue || !queue.items || queue.items.length === 0) {
    el.innerHTML = '<div style="text-align:center;color:var(--text3);padding:20px;font-size:12px">队列暂无数据，点击"立即筛选"或等待23:50自动筛选</div>';
    if (countEl) countEl.textContent = '(0个)';
    return;
  }

  if (countEl) countEl.textContent = `(${queue.items.length}个)`;

  const screenTime = queue.screen_time || '';
  let html = '';
  if (screenTime) {
    html += `<div style="padding:6px 10px;background:var(--bg);border-radius:6px;font-size:11px;color:var(--text3);margin-bottom:6px">筛选时间: ${screenTime}</div>`;
  }

  html += queue.items.map(item => {
    const dsText = {0:'未开始',21:'进行中',31:'已完成',40:'已领取'}[item.display_status] || item.display_status;
    return `
      <div style="display:flex;align-items:center;padding:8px 10px;border-bottom:1px solid var(--border);font-size:13px">
        <span style="flex:1;display:flex;align-items:center;gap:6px">
          <span style="font-weight:600">${item.label}</span>
          <span style="font-size:11px;color:var(--text3)">${dsText}</span>
        </span>
        <span style="font-size:12px;color:var(--text2)">
          签到 <b style="color:var(--primary)">${item.finish_count}</b>/5
          | 已领 <b>${item.gain_award_count}</b>
        </span>
      </div>`;
  }).join('');

  el.innerHTML = html;
}

// 手动触发预筛选
async function manualPreScreen() {
  const btn = document.querySelector('button[onclick="manualPreScreen()"]');
  if (!btn || btn.disabled) return;
  try {
    btn.disabled = true;
    btn.textContent = '筛选中...';
    const r = await fetch('/api/pre-screen', {method:'POST'});
    const data = await r.json();
    if (data.success) {
      showToast(data.message, 'success');
    } else {
      showToast('筛选失败: ' + (data.error || ''), 'error');
    }
  } catch(e) {
    showToast('请求失败: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '🔍 立即筛选';
  }
}

// 更新账号倒计时卡片
function updateAccountTimers(accounts) {
  const el = document.getElementById('accountTimers');
  if (!el) return;
  
  const enabledAccounts = (accounts || []).filter(a => a.enabled !== false);
  
  if (!enabledAccounts.length) {
    el.innerHTML = '<div style="text-align:center;color:var(--text3);padding:20px;font-size:12px">无启用账号</div>';
    return;
  }
  
  const offset = window._timeOffset || 0;
  const correctedNow = Date.now() + offset;
  
  const timers = enabledAccounts.map(acc => {
    const cfg = acc.config || {};
    const gh = cfg.grab_hour !== undefined ? cfg.grab_hour : 0;
    const gm = cfg.grab_minute !== undefined ? cfg.grab_minute : 0;
    const gs = cfg.grab_second !== undefined ? cfg.grab_second : 0;
    const preSec = cfg.pre_start_sec !== undefined ? cfg.pre_start_sec : 10;
    
    let totalSec = gh * 3600 + gm * 60 + gs - preSec;
    if (totalSec < 0) totalSec += 86400;
    const tH = Math.floor(totalSec / 3600) % 24;
    const tM = Math.floor((totalSec % 3600) / 60);
    const tS = totalSec % 60;
    
    // 【修复】用UTC计算目标时间戳
    const targetMs = calcTargetTimestamp(tH, tM, tS, correctedNow);
    const diff = targetMs - correctedNow;
    
    const h = String(Math.floor(diff / 3600000)).padStart(2, '0');
    const m = String(Math.floor((diff % 3600000) / 60000)).padStart(2, '0');
    const s = String(Math.floor((diff % 60000) / 1000)).padStart(2, '0');
    const ms = String(Math.floor(diff % 1000)).padStart(3, '0');
    
    const signInDays = (acc.sign_in && acc.sign_in.finish_count) || 0;
    const canGrab = signInDays >= 5;
    
    return {
      label: acc.label || `账号${acc.id}`,
      countdown: diff > 0 ? `${h}:${m}:${s}.${ms}` : '00:00:00.000',
      canGrab,
      signInDays,
      diff
    };
  });
  
  timers.sort((a, b) => a.diff - b.diff);
  
  el.innerHTML = timers.map(t => `
    <div style="display:flex;align-items:center;padding:8px 0;border-bottom:1px solid var(--border);font-size:13px">
      <span style="flex:1;display:flex;align-items:center;gap:6px">
        <span style="font-size:16px"></span>
        <span style="font-weight:500">${t.label}</span>
        <span style="font-size:11px;color:var(--text3)">${t.signInDays}/5天</span>
      </span>
      <span style="font-family:monospace;font-size:14px;font-weight:bold;color:${t.canGrab?'var(--green)':'var(--text3)'}">
        ${t.countdown}
      </span>
      <span style="margin-left:8px;font-size:11px;color:${t.canGrab?'var(--green)':'var(--orange)'}">
        ${t.canGrab?'✅':''}
      </span>
    </div>
  `).join('');
}

// 毫秒级平滑倒计时 (requestAnimationFrame 驱动)
// 【修复】直接使用 updateCountdown 计算的目标时间戳，不再用 setHours
function tickCountdown() {
  const el = document.getElementById('countdown');
  if (!el) { requestAnimationFrame(tickCountdown); return; }

  const status = window._grabStatus || 'idle';

  if (status === 'grabbing') {
    el.textContent = '🔥 抢券中...';
    el.style.color = 'var(--red)';
  } else if (status === 'success') {
    el.textContent = '✅ 抢券成功!';
    el.style.color = 'var(--green)';
  } else if (window._targetTime) {
    // 【关键】直接使用存储的目标时间戳，无时区问题
    const offset = window._timeOffset || 0;
    const correctedNow = Date.now() + offset;
    const diff = window._targetTime - correctedNow;
    
    if (diff <= 0) {
      el.textContent = '00:00:00.000';
      el.style.color = 'var(--red)';
    } else {
      const h = String(Math.floor(diff / 3600000)).padStart(2, '0');
      const m = String(Math.floor((diff % 3600000) / 60000)).padStart(2, '0');
      const s = String(Math.floor((diff % 60000) / 1000)).padStart(2, '0');
      const ms = String(Math.floor(diff % 1000)).padStart(3, '0');
      el.textContent = `${h}:${m}:${s}.${ms}`;
      if (diff < 10000) el.style.color = 'var(--red)';
      else if (diff < 60000) el.style.color = 'var(--orange)';
      else el.style.color = '';
    }
  } else {
    // poll 还没成功过，显示等待
    el.textContent = '--:--:--.---';
    el.style.color = '';
  }
  requestAnimationFrame(tickCountdown);
}
requestAnimationFrame(tickCountdown);

// === Log Scroll Control ===
let _lastLogHtml = '';

function makeLogHtml(logs) {
  if (!logs.length) return '<div style="text-align:center;color:var(--text3);padding:40px">暂无日志</div>';
  const showDetailed = document.getElementById('showDetailedLogs')?.checked || document.getElementById('showDetailedLogsFull')?.checked;
  return logs.slice().reverse().map(l => {
    // 判断是否为抢券请求日志
    const isGrabRequest = l.message && (l.message.includes('POST /api/') || l.message.includes('线程-') || l.message.includes('已发'));
    
    let msgClass = 'log-level-' + l.level;
    let msgContent = l.message;
    
    // 如果是抢券请求且开启了详细显示
    if (isGrabRequest && showDetailed) {
      // 解析并高亮显示
      msgClass += ' log-grab-request';
    }
    
    return `
    <div class="log-item">
      <span class="log-time">${l.time}</span>
      <span class="log-tag ${l.module}">${l.module}</span>
      <span class="log-msg ${msgClass}">${msgContent}</span>
    </div>
  `}).join('');
}

// 滚动事件：用户往上滚→取消跟随；滚到底部→恢复跟随
function onLogScroll(el) {
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
  const c = document.getElementById('autoFollowLogs');
  if (c) c.checked = atBottom;
}
function onLogScrollFull(el) {
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
  const c = document.getElementById('autoFollowLogsFull');
  if (c) c.checked = atBottom;
}

function updateLogs(logs) {
  const el = document.getElementById('logList');
  const logFull = document.getElementById('logListFull');
  document.getElementById('logCount').textContent = `(${logs.length}条)`;
  const lcFull = document.getElementById('logCountFull');
  if (lcFull) lcFull.textContent = `(${logs.length}条)`;

  const html = makeLogHtml(logs);

  // 如果内容没变就不刷新（避免无意义重绘）
  if (html === _lastLogHtml) return;
  _lastLogHtml = html;

  for (const target of [el, logFull]) {
    if (!target) continue;
    // 保存滚动状态
    const oldScrollHeight = target.scrollHeight;
    const oldScrollTop = target.scrollTop;
    const wasAtBottom = oldScrollHeight - oldScrollTop - target.clientHeight < 30;

    // 只追加新内容，不全量替换
    target.innerHTML = html;

    // 恢复或滚动
    if (wasAtBottom) {
      // 原本在底部 → 跟随到新底部
      target.scrollTop = target.scrollHeight;
    } else {
      // 不在底部 → 保持原位置（按比例恢复）
      const newScrollHeight = target.scrollHeight;
      target.scrollTop = oldScrollTop + (newScrollHeight - oldScrollHeight);
    }
  }
}

// "跟随最新"两个复选框联动
document.addEventListener('DOMContentLoaded', function() {
  const chk1 = document.getElementById('autoFollowLogs');
  const chk2 = document.getElementById('autoFollowLogsFull');
  if (chk1) chk1.addEventListener('change', function() { if(chk2)chk2.checked=this.checked; });
  if (chk2) chk2.addEventListener('change', function() { if(chk1)chk1.checked=this.checked; });
});

function updateHistory(list) {
  const el = document.getElementById('historyList');
  const html = list.length ? list.slice().reverse().map(h => `
    <div class="history-item">
      <span style="color:var(--text3);font-size:12px;min-width:140px">${h.time}</span>
      <span class="history-badge ${h.success ? 'ok' : 'fail'}">${h.success ? '成功' : '失败'}</span>
      <span style="flex:1;font-size:13px">${h.detail}</span>
    </div>
  `).join('') : '<div style="text-align:center;color:var(--text3);padding:40px">暂无记录</div>';
  el.innerHTML = html;
  const histFull = document.getElementById('historyListFull');
  if (histFull) histFull.innerHTML = html;
}

// 清除日志
async function clearLogs() {
  try {
    await fetch('/api/clear-logs', { method:'POST' });
    showToast('日志已清除', 'success');
  } catch(e) { showToast('清除失败: '+e.message, 'error'); }
}
// 清除历史
async function clearHistory() {
  try {
    await fetch('/api/clear-history', { method:'POST' });
    showToast('历史已清除', 'success');
  } catch(e) { showToast('清除失败: '+e.message, 'error'); }
}

// Polling
let lastLogId = 0;
async function poll() {
  try {
    const r = await fetch('/api/state');
    const data = await r.json();
    try { updateDashboard(data.state); } catch(e) { console.error('updateDashboard error:', e); }
    try { updateCountdown(data.state); } catch(e) { console.error('updateCountdown error:', e); }
    try { updateAccountTimers(data.state.accounts); } catch(e) { console.error('updateAccountTimers error:', e); }
    try { updateEligibleQueue(data.eligible_queue); } catch(e) { console.error('updateEligibleQueue error:', e); }
    try { updateLogs(data.logs); } catch(e) { console.error('updateLogs error:', e); }
    try { updateHistory(data.history); } catch(e) { console.error('updateHistory error:', e); }
    // 同步状态显示
    try {
      const syncEl = document.getElementById('syncStatus');
      if (syncEl && data.sync) {
        const s = data.sync;
        const srcIcons = {pdd:'🎯', ntp:'🌐', local:'️'};
        const srcNames = {pdd:'PDD服务器', ntp:'NTP', local:'本地'};
        const icon = srcIcons[s.source] || '⚠️';
        const name = srcNames[s.source] || '本地';
        syncEl.innerHTML = `${icon} <b>${name}</b> | 偏移 <b style="color:${Math.abs(s.offset_ms)<100?'var(--green)':'var(--orange)'}">${s.offset_ms>=0?'+':''}${s.offset_ms.toFixed(2)}ms</b> | RTT ${s.last_rtt_ms.toFixed(0)}ms | ${s.samples}次采样 | 范围 ${s.min_offset.toFixed(1)}~${s.max_offset.toFixed(1)}ms`;
      }
    } catch(e) { console.error('syncStatus error:', e); }
  } catch(e) { console.error('Poll error:', e); }
}
poll();
setInterval(poll, 2000);
</script>
</body>
</html>
"""


@app.route("/")
@login_required
def index():
    return Response(DASHBOARD_HTML, content_type="text/html; charset=utf-8")


@app.route("/api/state")
@login_required
def api_state():
    # 加载账号列表
    accounts = []
    try:
        from main import load_accounts
        accounts = load_accounts()
    except Exception:
        pass

    state = dict(STATE)
    state["accounts"] = accounts
    # 第一个启用账号的 token 用于兼容显示
    enabled = [a for a in accounts if a.get("enabled", True)]
    if enabled:
        state["access_token"] = enabled[0].get("access_token", "")
        state["cookies"] = enabled[0].get("cookies", {})
    else:
        state["access_token"] = ""
        state["cookies"] = {}

    # 获取同步状态
    sync_status = {}
    try:
        from main import get_sync_status
        sync_status = get_sync_status()
    except Exception:
        pass

    return jsonify({
        "state": state,
        "sync": sync_status,
        "logs": list(LOGS),
        "history": list(HISTORY),
        "eligible_queue": _get_eligible_queue_info(),
    })


# ============================================================
# API: 保存配置
# ============================================================
@app.route("/api/config", methods=["POST"])
@login_required
def api_save_config():
    try:
        data = request.get_json()
        # 保存全局默认配置 (新增账号时的默认值)
        STATE["grab_hour"] = data.get("grab_hour", 0)
        STATE["grab_minute"] = data.get("grab_minute", 0)
        STATE["grab_second"] = data.get("grab_second", 0)
        STATE["next_grab"] = f"{STATE['grab_hour']:02d}:{STATE['grab_minute']:02d}:{STATE['grab_second']:02d}"
        STATE["pre_start_sec"] = data.get("pre_start_sec", 10)
        STATE["end_hour"] = data.get("end_hour", 0)
        STATE["end_minute"] = data.get("end_minute", 0)
        STATE["end_second"] = data.get("end_second", 30)
        STATE["thread_count"] = data.get("thread_count", 5)
        add_log("info", "配置",
                f"全局默认配置已更新: {STATE['next_grab']} | "
                f"提前{STATE['pre_start_sec']}s | "
                f"结束{STATE['end_hour']:02d}:{STATE['end_minute']:02d}:{STATE['end_second']:02d} | "
                f"{STATE['thread_count']}线程")

        # 更新调度器触发时间 (基于所有账号的最早时间)
        try:
            global _scheduler_ref
            if _scheduler_ref is not None:
                from apscheduler.triggers.cron import CronTrigger
                from main import calc_earliest_trigger
                th, tm, ts = calc_earliest_trigger()
                new_trigger = CronTrigger(
                    hour=th, minute=tm, second=ts, timezone=BJT,
                )
                _scheduler_ref.reschedule_job("pdd_coupon_grab", trigger=new_trigger)
                add_log("info", "系统", f"调度器已重新定时: {th:02d}:{tm:02d}:{ts:02d}")
            else:
                add_log("warn", "系统", "调度器未注册")
        except Exception as e:
            add_log("warn", "系统", f"调度器更新时间失败: {e}")

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ============================================================
# API: 手动预筛选
# ============================================================
@app.route("/api/pre-screen", methods=["POST"])
@login_required
def api_pre_screen():
    """手动触发一次预筛选（后台执行，立即返回）"""
    import threading as _t

    # 检查是否正在筛选
    if getattr(api_pre_screen, '_running', False):
        return jsonify({"success": False, "error": "筛选正在进行中，请等待完成"})

    def _run():
        api_pre_screen._running = True
        try:
            from main import pre_screen_accounts
            pre_screen_accounts()
        except Exception as e:
            add_log("error", "预筛选", f"手动预筛选失败: {e}")
        finally:
            api_pre_screen._running = False

    api_pre_screen._running = True
    _t.Thread(target=_run, daemon=True).start()
    return jsonify({
        "success": True,
        "message": "筛选已启动，请查看日志和队列刷新",
    })


# ============================================================
# API: 立即测试抢券 (跳过等待)
# ============================================================
_grab_thread = None


@app.route("/api/test-grab", methods=["POST"])
@login_required
def api_test_grab():
    """立即触发一次抢券 (跳过时间等待)"""
    global _grab_thread

    if _grab_thread is not None and _grab_thread.is_alive():
        return jsonify({"success": False, "error": "抢券正在进行中，请等待完成"})

    def _run():
        try:
            from main import run_grab_session
            os.environ["SKIP_WAIT"] = "true"
            run_grab_session()
        except Exception as e:
            add_log("error", "抢券", f"测试抢券异常: {e}")
            STATE["status"] = "failed"
            STATE["last_grab_result"] = f"异常: {e}"
            add_history(False, f"异常: {e}")
        finally:
            os.environ.pop("SKIP_WAIT", None)
            # 确保状态不会卡在"抢券中"
            if STATE["status"] == "grabbing":
                STATE["status"] = "waiting"
                add_log("info", "系统", "抢券状态已重置为等待")

    _grab_thread = threading.Thread(target=_run, daemon=True)
    _grab_thread.start()

    # 启动超时保护: 60秒后如果还在抢券，强制重置状态
    def _watchdog():
        import time as _t
        _t.sleep(60)
        if STATE["status"] == "grabbing":
            STATE["status"] = "waiting"
            add_log("warn", "系统", "抢券超时(60秒)，状态已重置")
    threading.Thread(target=_watchdog, daemon=True).start()

    add_log("info", "抢券", "手动测试抢券已触发 (跳过等待)")
    return jsonify({"success": True, "message": "抢券已触发，请查看日志"})


# ============================================================
# API: 单账号测试抢券
# ============================================================
@app.route("/api/test-grab-account", methods=["POST"])
@login_required
def api_test_grab_account():
    """为单个账号立即触发抢券测试 (跳过时间等待，同步执行)"""
    import time as _t
    import requests as _req
    try:
        from main import load_accounts, get_time_offset
        from pdd_token import generate_anti_content
        data = request.get_json()
        account_id = data.get("account_id", "")
        accounts = load_accounts()
        account = next((a for a in accounts if a["id"] == account_id), None)
        if not account:
            return jsonify({"success": False, "detail": "账号不存在"})

        label = account.get("label", account_id)
        cookies = account.get("cookies", {})
        cfg = account.get("config", {})
        t_count = cfg.get("thread_count", 5)

        api_url = "https://mobile.yangkeduo.com/proxy/api/api/aurum/check_in/task/gain/award"
        base_headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36",
            "Content-Type": "application/json;charset=UTF-8",
            "Referer": "https://mobile.yangkeduo.com/charge_sign_coupon.html",
            "Origin": "https://mobile.yangkeduo.com",
        }
        task_template_id = "1"
        task_id = os.getenv("PDD_TASK_ID", "") or account.get("sign_in", {}).get("task_id", "") or "MT829143858423691176"
        add_log("info", "测试", f"[{label}] 使用 task_id={task_id}")

        add_log("info", "抢券", f"[{label}] 单账号测试抢券开始 ({t_count}线程，持续3秒)")

        session = _req.Session()
        session.headers.update(base_headers)
        session.cookies.update(cookies)

        stop_event = threading.Event()
        results = []
        r_lock = threading.Lock()
        total_req = [0]
        success_flag = [False]

        def worker(tid):
            count = 0
            while not stop_event.is_set():
                try:
                    anti_token = generate_anti_content(
                        int(_t.time() * 1000 + get_time_offset() * 1000)
                    )
                    h = {"anti-content": anti_token}
                    payload = {
                        "request_source": 1,
                        "anti_content": anti_token,
                        "task_id": task_id,
                        "task_template_id": task_template_id,
                    }
                    resp = session.post(api_url, json=payload, headers=h, timeout=3)
                    data = resp.json()
                    count += 1
                    with r_lock:
                        results.append(data)
                        total_req[0] += 1
                    if data.get("success") or data.get("error_code") == 0:
                        add_log("success", "抢券", f"[{label}] 线程-{tid} #{count} 抢券成功!")
                        success_flag[0] = True
                        stop_event.set()
                        return
                except Exception:
                    with r_lock:
                        total_req[0] += 1

        threads = []
        for i in range(t_count):
            t = threading.Thread(target=worker, args=(i,), daemon=True)
            threads.append(t)
            t.start()

        _t.sleep(3)  # 发送 3 秒
        stop_event.set()
        for t in threads:
            t.join(timeout=3)
        session.close()

        # 分析结果
        detail = ""
        if success_flag[0]:
            detail = f"{label} 抢券成功!"
            add_log("success", "抢券", f"[{label}] 测试抢券成功! ({total_req[0]}个请求)")
        elif results:
            for r in results:
                err = str(r.get("error_msg", "")) + str(r.get("errorMsg", ""))
                if any(kw in err for kw in ["已领完", "已抢完", "库存不足", "今日已领"]):
                    detail = err[:80]
                    break
            if not detail:
                detail = f"未抢到 ({total_req[0]}个请求)"
            add_log("warn", "抢券", f"[{label}] 测试抢券结束: {detail}")
        else:
            detail = "无有效响应"
            add_log("warn", "抢券", f"[{label}] 测试抢券无响应")

        return jsonify({"success": success_flag[0], "detail": detail, "total_requests": total_req[0]})
    except Exception as e:
        add_log("error", "抢券", f"单账号测试抢券异常: {e}")
        return jsonify({"success": False, "detail": str(e), "total_requests": 0})


# ============================================================
# API: 签到管理
# ============================================================
@app.route("/api/accounts/<account_id>/sign-in/query", methods=["POST"])
@login_required
def api_query_sign_in(account_id):
    """查询账号签到状态"""
    try:
        from main import refresh_account_sign_in
        result = refresh_account_sign_in(account_id)
        STATE["accounts"] = __import__("main").load_accounts()
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/accounts/<account_id>/sign-in", methods=["POST"])
@login_required
def api_perform_sign_in(account_id):
    """手动执行签到"""
    try:
        from main import load_accounts, perform_sign_in, query_sign_in_status, update_account
        accounts = load_accounts()
        acc = next((a for a in accounts if a["id"] == account_id), None)
        if not acc:
            return jsonify({"success": False, "error": "账号不存在"})
        label = acc.get("label", "")
        result = perform_sign_in(acc)
        # 签到后刷新状态
        status = query_sign_in_status(acc)
        if status["success"]:
            sign_in = acc.get("sign_in", {})
            sign_in["finish_count"] = status["finish_count"]
            sign_in["gain_award_count"] = status["gain_award_count"]
            sign_in["display_status"] = status["display_status"]
            sign_in["last_check"] = __import__("datetime").datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S")
            sign_in["can_sign"] = status["can_sign"]
            sign_in["can_grab"] = status["can_grab"]
            update_account(account_id, sign_in=sign_in)
        STATE["accounts"] = __import__("main").load_accounts()
        add_log("info", "签到", f"[{label}] {result['message']}")
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/sign-in-all", methods=["POST"])
@login_required
def api_sign_in_all():
    """为所有启用账号执行签到"""
    try:
        from main import auto_sign_in_all
        results = auto_sign_in_all()
        STATE["accounts"] = __import__("main").load_accounts()
        return jsonify({"success": True, "results": results})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/query-all-sign-in", methods=["POST"])
@login_required
def api_query_all_sign_in():
    """查询所有账号的签到状态"""
    try:
        from main import load_accounts, refresh_account_sign_in
        accounts = load_accounts()
        results = []
        for acc in accounts:
            r = refresh_account_sign_in(acc["id"])
            results.append({"id": acc["id"], "label": acc.get("label", ""), "result": r})
        STATE["accounts"] = __import__("main").load_accounts()
        return jsonify({"success": True, "results": results})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ============================================================
# API: 查询间隔设置
# ============================================================
@app.route("/api/query-interval", methods=["POST"])
@login_required
def api_query_interval():
    """更新签到状态查询间隔"""
    try:
        data = request.get_json()
        minutes = data.get("interval_minutes", 120)
        STATE["query_interval_minutes"] = minutes
        global _scheduler_ref
        if _scheduler_ref is not None:
            try:
                from apscheduler.triggers.interval import IntervalTrigger
                _scheduler_ref.reschedule_job(
                    "pdd_sign_in_query",
                    trigger=IntervalTrigger(minutes=minutes)
                )
                add_log("info", "配置", f"签到查询间隔已更新: {minutes} 分钟")
            except Exception as e:
                add_log("warn", "系统", f"更新查询间隔失败: {e}")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ============================================================
# API: 日志保存条数设置
# ============================================================
@app.route("/api/log-max", methods=["POST"])
@login_required
def api_log_max():
    """更新日志保存条数限制"""
    try:
        global LOGS
        data = request.get_json()
        max_count = data.get("max_count", 0)
        # 重建 deque，保留现有日志
        old_logs = list(LOGS)
        new_maxlen = max_count if max_count > 0 else None
        LOGS = deque(old_logs, maxlen=new_maxlen)
        STATE["log_max_count"] = max_count
        label = "不限制" if max_count == 0 else f"{max_count} 条"
        add_log("info", "配置", f"日志保存上限已更新: {label}")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ============================================================
# API: 清除日志 / 历史
# ============================================================
@app.route("/api/clear-logs", methods=["POST"])
@login_required
def api_clear_logs():
    LOGS.clear()
    add_log("info", "系统", "日志已清除")
    return jsonify({"success": True})


@app.route("/api/clear-history", methods=["POST"])
@login_required
def api_clear_history():
    HISTORY.clear()
    STATE["total_grabs"] = 0
    STATE["success_grabs"] = 0
    return jsonify({"success": True})


# ============================================================
# API: 多账号管理
# ============================================================
def _reschedule_job():
    """账号变更后重新调度"""
    global _scheduler_ref
    if _scheduler_ref is None:
        return
    try:
        from apscheduler.triggers.cron import CronTrigger
        from main import calc_earliest_trigger
        th, tm, ts = calc_earliest_trigger()
        new_trigger = CronTrigger(hour=th, minute=tm, second=ts, timezone=BJT)
        _scheduler_ref.reschedule_job("pdd_coupon_grab", trigger=new_trigger)
        add_log("info", "系统", f"调度器已重新定时: {th:02d}:{tm:02d}:{ts:02d}")
    except Exception as e:
        add_log("warn", "系统", f"调度器更新时间失败: {e}")
def _parse_cookie_string(cookie_str: str) -> dict:
    """解析 cookie 字符串为字典"""
    cookies = {}
    if cookie_str and "=" in cookie_str:
        for pair in cookie_str.split(";"):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                k = k.strip().lstrip("+").strip()
                cookies[k] = v.strip()
    return cookies


@app.route("/api/accounts", methods=["POST"])
@login_required
def api_add_account():
    """新增账号"""
    try:
        from main import add_account
        data = request.get_json()
        token_str = data.get("access_token", "").strip()
        user_id = data.get("user_id", "").strip()
        cookie_str = data.get("cookie_string", "").strip()
        label = data.get("label", "").strip()
        config = data.get("config")  # 可选的独立配置

        cookies = _parse_cookie_string(cookie_str)
        if not token_str:
            token_str = cookies.get("PDDAccessToken", "")
        if not user_id:
            user_id = cookies.get("pdd_user_id", "")
        if token_str and not cookies:
            cookies["PDDAccessToken"] = token_str
            if user_id:
                cookies["pdd_user_id"] = user_id
        if not token_str:
            return jsonify({"success": False, "error": "未检测到 Access Token"})

        acc = add_account(token_str, user_id, cookies, label, config)
        STATE["accounts"] = __import__("main").load_accounts()
        STATE["token_valid"] = True
        add_log("info", "登录", f"账号已添加: {acc['label']} | Cookie={len(cookies)}个")
        _reschedule_job()
        return jsonify({"success": True, "account": acc})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/accounts/<account_id>", methods=["PUT"])
@login_required
def api_update_account(account_id):
    """更新账号配置或凭证"""
    try:
        from main import update_account, load_accounts
        data = request.get_json()
        kwargs = {}
        if "label" in data:
            kwargs["label"] = data["label"]
        if "enabled" in data:
            kwargs["enabled"] = data["enabled"]
        if "config" in data:
            kwargs["config"] = data["config"]
        if "access_token" in data or "cookie_string" in data:
            token_str = data.get("access_token", "").strip()
            cookie_str = data.get("cookie_string", "").strip()
            cookies = _parse_cookie_string(cookie_str)
            if not token_str:
                token_str = cookies.get("PDDAccessToken", "")
            if token_str and not cookies:
                cookies["PDDAccessToken"] = token_str
            if token_str:
                kwargs["access_token"] = token_str
                kwargs["cookies"] = cookies
                kwargs["user_id"] = data.get("user_id", cookies.get("pdd_user_id", ""))
        update_account(account_id, **kwargs)
        STATE["accounts"] = __import__("main").load_accounts()
        add_log("info", "配置", f"账号已更新: {account_id}")
        _reschedule_job()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/accounts/<account_id>", methods=["DELETE"])
@login_required
def api_delete_account(account_id):
    """删除账号"""
    try:
        from main import delete_account, load_accounts
        delete_account(account_id)
        STATE["accounts"] = load_accounts()
        enabled = [a for a in STATE["accounts"] if a.get("enabled", True)]
        STATE["token_valid"] = bool(enabled)
        STATE["cookie_count"] = len(enabled[0].get("cookies", {})) if enabled else 0
        add_log("info", "系统", f"账号已删除: {account_id}")
        _reschedule_job()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/accounts/<account_id>/toggle", methods=["POST"])
@login_required
def api_toggle_account(account_id):
    """启用/禁用账号"""
    try:
        from main import update_account, load_accounts
        data = request.get_json()
        enabled = data.get("enabled", True)
        update_account(account_id, enabled=enabled)
        STATE["accounts"] = load_accounts()
        status = "启用" if enabled else "禁用"
        add_log("info", "配置", f"账号已{status}: {account_id}")
        _reschedule_job()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ============================================================
# API: 测试 Cookie (支持指定账号)
# ============================================================
@app.route("/api/test-cookie", methods=["POST"])
@login_required
def api_test_cookie():
    try:
        import requests as req
        from main import load_accounts
        data = request.get_json() or {}
        account_id = data.get("account_id", "")

        # 如果指定了账号 ID，用该账号；否则用第一个启用的
        accounts = load_accounts()
        if account_id:
            account = next((a for a in accounts if a["id"] == account_id), None)
        else:
            enabled = [a for a in accounts if a.get("enabled", True)]
            account = enabled[0] if enabled else None

        if not account:
            return jsonify({"valid": False, "error": "未找到账号"})

        cookies = account.get("cookies", {})
        token = account.get("access_token", "")
        user_id = account.get("user_id", "")
        label = account.get("label", "")

        if not token and not cookies:
            return jsonify({"valid": False, "error": "该账号无 Cookie"})

        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36",
            "Referer": "https://mobile.yangkeduo.com/",
        }
        resp = req.get(
            "https://mobile.yangkeduo.com/",
            cookies=cookies, headers=headers, timeout=10, allow_redirects=False,
        )
        if resp.status_code in (301, 302):
            location = resp.headers.get("Location", "")
            if "login" in location:
                return jsonify({"valid": False, "error": f"[{label}] Cookie 已过期"})

        resp2 = req.get(
            f"https://mobile.yangkeduo.com/proxy/api/api/server/_stm?pdduid={user_id}",
            cookies=cookies, headers=headers, timeout=10,
        )
        if resp2.status_code == 200:
            return jsonify({"valid": True, "user_id": user_id, "nickname": f"{label} ({user_id})"})
        else:
            return jsonify({"valid": False, "error": f"[{label}] API 返回 {resp2.status_code}"})
    except Exception as e:
        return jsonify({"valid": False, "error": f"测试失败: {str(e)}"})


# ============================================================
# 启动
# ============================================================
def start_dashboard(port: int = 8080):
    """在后台线程启动 Web 面板"""
    def run():
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    add_log("info", "系统", f"Web 面板启动于端口 {port}")
    return thread


# 独立运行测试
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)

    # 模拟一些数据
    STATE.update({
        "status": "waiting",
        "token_valid": True,
        "user_id": "3245009495192",
        "cookie_count": 11,
        "next_grab": "00:00:00",
        "grab_hour": 0,
        "grab_minute": 0,
        "grab_second": 0,
        "pre_start_sec": 10,
        "end_hour": 0,
        "end_minute": 0,
        "end_second": 30,
        "thread_count": 5,
        "total_grabs": 3,
        "success_grabs": 1,
        "ntp_offset_ms": 99.5,
        "uptime_start": time.time() - 3600,
    })

    add_log("info", "系统", "脚本启动完成")
    add_log("info", "NTP", "时间同步成功，偏移 99.5ms")
    add_log("info", "登录", "Token 加载成功 (11个Cookie)")
    add_log("info", "配置", "目标时间 00:00:00 | 提前10s | 结束00:00:30 | 5线程")
    add_log("info", "系统", "调度器已启动，等待抢券时间...")
    add_log("warn", "抢券", "上次抢券: 券已领完，请明日再来")
    add_log("success", "抢券", "2026-06-17 抢券成功!")

    add_history(True, "抢券成功，30元话费券已到账")
    add_history(False, "券已领完，请明日再来")
    add_history(False, "券已领完，请明日再来")

    print("Web 面板启动: http://127.0.0.1:8080")
    app.run(host="0.0.0.0", port=8080, debug=False)
