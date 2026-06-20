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
}

# 日志队列 (最多保留 200 条)
LOGS = deque(maxlen=200)

# 抢券历史
HISTORY = deque(maxlen=50)


def add_log(level: str, module: str, message: str):
    """添加一条日志"""
    LOGS.append({
        "time": datetime.now(BJT).strftime("%H:%M:%S"),
        "level": level,       # info / warn / error / success
        "module": module,     # 系统 / 抢券 / 登录 / NTP
        "message": message,
    })


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
.log-tag.登录 { background: var(--blue-bg); color: var(--blue); }
.log-tag.NTP { background: var(--purple-bg); color: var(--purple); }
.log-tag.配置 { background: var(--orange-bg); color: var(--orange); }
.log-msg { flex: 1; word-break: break-all; }
.log-level-warn { color: var(--orange); }
.log-level-error { color: var(--red); font-weight: 600; }
.log-level-success { color: var(--green); font-weight: 600; }

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
      <!-- Countdown + Config -->
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
          <h3>⚙️ 当前配置</h3>
          <div class="config-row"><span class="config-key">目标时间</span><span class="config-val" id="cfgTime">-</span></div>
          <div class="config-row"><span class="config-key">提前开火</span><span class="config-val" id="cfgPre">-</span></div>
          <div class="config-row"><span class="config-key">结束时间</span><span class="config-val" id="cfgEnd">-</span></div>
          <div class="config-row"><span class="config-key">并发线程</span><span class="config-val" id="cfgThreads">-</span></div>
          <div class="config-row"><span class="config-key">运行时长</span><span class="config-val" id="cfgUptime">-</span></div>
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
      <div class="page-title">⚙️ 配置详情</div>
      <div class="config-table">
        <div class="form-row" style="margin-bottom:14px">
          <span class="form-label">目标时间</span>
          <input class="form-input form-input-sm" id="editHour" type="number" min="0" max="23" placeholder="时">
          <span>:</span>
          <input class="form-input form-input-sm" id="editMin" type="number" min="0" max="59" placeholder="分">
          <span>:</span>
          <input class="form-input form-input-sm" id="editSec" type="number" min="0" max="59" placeholder="秒">
        </div>
        <div class="form-row" style="margin-bottom:14px">
          <span class="form-label">提前开火</span>
          <input class="form-input form-input-sm" id="editPreSec" type="number" min="1" max="120" placeholder="秒"> <span>秒前开始</span>
        </div>
        <div class="form-row" style="margin-bottom:14px">
          <span class="form-label">结束时间</span>
          <input class="form-input form-input-sm" id="editEndHour" type="number" min="0" max="23" placeholder="时">
          <span>:</span>
          <input class="form-input form-input-sm" id="editEndMin" type="number" min="0" max="59" placeholder="分">
          <span>:</span>
          <input class="form-input form-input-sm" id="editEndSec" type="number" min="0" max="59" placeholder="秒">
        </div>
        <div class="form-row" style="margin-bottom:14px">
          <span class="form-label">并发线程</span>
          <input class="form-input form-input-sm" id="editThreads" type="number" min="1" max="20" placeholder="个"> <span>个线程持续发送</span>
        </div>
        <div class="btn-group">
          <button class="btn btn-primary" onclick="saveConfig()">💾 保存配置</button>
          <button class="btn btn-success" onclick="testGrab()">🚀 立即测试抢券</button>
          <button class="btn btn-outline" onclick="resetConfig()">↩ 恢复默认</button>
        </div>
      </div>
      <div style="margin-top:16px;padding:16px;background:var(--card);border:1px solid var(--border);border-radius:12px">
        <h3 style="margin-bottom:12px">💡 环境变量说明</h3>
        <div class="config-row"><span class="config-key">GRAB_HOUR</span><span class="config-val">目标时间 - 小时 (0-23)</span></div>
        <div class="config-row"><span class="config-key">GRAB_MINUTE</span><span class="config-val">目标时间 - 分钟 (0-59)</span></div>
        <div class="config-row"><span class="config-key">GRAB_SECOND</span><span class="config-val">目标时间 - 秒 (0-59)</span></div>
        <div class="config-row"><span class="config-key">PRE_START_SEC</span><span class="config-val">提前开始秒数 (如10=提前10秒开火)</span></div>
        <div class="config-row"><span class="config-key">END_HOUR/MINUTE/SECOND</span><span class="config-val">结束时间 (窗口内持续发送)</span></div>
        <div class="config-row"><span class="config-key">THREAD_COUNT</span><span class="config-val">并发线程数 (持续发送)</span></div>
        <div class="config-row"><span class="config-key">PORT</span><span class="config-val">Web 面板端口</span></div>
      </div>

      <!-- 安全设置 -->
      <div style="margin-top:16px;padding:16px;background:var(--card);border:1px solid var(--border);border-radius:12px">
        <h3 style="margin-bottom:12px">🔒 安全设置 · 修改密码</h3>
        <div class="form-group">
          <label class="form-label">当前密码</label>
          <input class="form-input" id="pwdOld" type="password" placeholder="输入当前密码" style="max-width:300px">
        </div>
        <div class="form-group">
          <label class="form-label">新密码</label>
          <input class="form-input" id="pwdNew" type="password" placeholder="新密码（至少4位）" style="max-width:300px" oninput="document.getElementById('pwdConfirm').pattern=this.value.replace(/[.*+?^${}()|[\]\\\\]/g,'\\\\$&')">
        </div>
        <div class="form-group">
          <label class="form-label">确认新密码</label>
          <input class="form-input" id="pwdConfirm" type="password" placeholder="再次输入新密码" style="max-width:300px">
        </div>
        <div class="btn-group">
          <button class="btn btn-primary" onclick="changePassword()">🔒 修改密码</button>
          <button class="btn btn-danger" onclick="logout()" style="margin-left:10px">🚪 退出登录</button>
        </div>
        <div id="pwdResult" style="display:none;margin-top:12px"></div>
      </div>
    </div>

    <!-- PAGE: 账号 -->
    <div class="page" id="page-account">
      <div class="page-title">👤 账号管理</div>

      <!-- 添加/编辑账号 -->
      <div class="account-card" style="max-width:600px;margin-bottom:16px">
        <h3 id="accountFormTitle">➕ 添加 / 编辑账号</h3>
        <div class="form-group">
          <label class="form-label">Access Token</label>
          <input class="form-input" id="inputToken" type="text" placeholder="粘贴 PDDAccessToken 的值">
        </div>
        <div class="form-group">
          <label class="form-label">User ID</label>
          <input class="form-input" id="inputUserId" type="text" placeholder="pdd_user_id (可选，自动提取)">
        </div>
        <div class="form-group">
          <label class="form-label">完整 Cookie 字符串 (可选)</label>
          <textarea class="form-input" id="inputCookies" placeholder="粘贴从抓包工具获取的完整 Cookie 字符串&#10;格式: key1=value1; key2=value2; ...&#10;如果只填 Access Token 也可以"></textarea>
        </div>
        <div class="btn-group">
          <button class="btn btn-primary" onclick="saveAccount()" id="btnSaveAcc">💾 保存账号</button>
          <button class="btn btn-success" id="btnTestCookie" onclick="testCookie()">🧪 测试 Cookie</button>
          <button class="btn btn-danger" onclick="clearAccount()">🗑 清除账号</button>
          <button class="btn btn-outline" onclick="loadAccountToForm()" style="margin-left:auto">📝 加载当前账号到表单</button>
        </div>
        <div id="testResult" style="display:none"></div>
      </div>

      <!-- 当前账号信息 -->
      <div class="account-card" style="max-width:600px">
        <h3>📋 当前账号信息</h3>
        <div class="account-row"><span class="account-label">Token 状态</span><span class="account-value" id="accTokenStatus">-</span></div>
        <div class="account-row"><span class="account-label">User ID</span><span class="account-value" id="accUserId">-</span></div>
        <div class="account-row"><span class="account-label">Cookie 数量</span><span class="account-value" id="accCookieCount">-</span></div>
        <div class="account-row"><span class="account-label">NTP 偏移</span><span class="account-value" id="accNtp">-</span></div>
        <div class="account-row"><span class="account-label">上次抢券</span><span class="account-value" id="accLastGrab">-</span></div>
        <div class="account-row"><span class="account-label">上次结果</span><span class="account-value" id="accLastResult">-</span></div>
      </div>
      <div class="account-card" style="margin-top:16px;max-width:600px">
        <h3>🔑 Access Token</h3>
        <div class="token-preview" id="accTokenPreview">加载中...</div>
      </div>
      <div class="account-card" style="margin-top:16px;max-width:600px">
        <h3>🍪 Cookie 列表</h3>
        <div id="accCookieList" style="max-height:300px;overflow-y:auto">
          <div style="color:var(--text3);padding:20px;text-align:center">加载中...</div>
        </div>
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
  const timeStr = `${String(data.grab_hour).padStart(2,'0')}:${String(data.grab_minute).padStart(2,'0')}:${String(data.grab_second).padStart(2,'0')}`;
  document.getElementById('cfgTime').textContent = timeStr;
  document.getElementById('cfgPre').textContent = (data.pre_start_sec || 10) + ' 秒前开始';
  const endStr = `${String(data.end_hour||0).padStart(2,'0')}:${String(data.end_minute||0).padStart(2,'0')}:${String(data.end_second||30).padStart(2,'0')}`;
  document.getElementById('cfgEnd').textContent = endStr;
  document.getElementById('cfgThreads').textContent = (data.thread_count || 5) + ' 线程';
  const uptime = Math.floor((Date.now()/1000) - data.uptime_start);
  const h = Math.floor(uptime/3600), m = Math.floor((uptime%3600)/60);
  document.getElementById('cfgUptime').textContent = `${h}时${m}分`;
  // Account page
  document.getElementById('accTokenStatus').innerHTML = data.token_valid
    ? '<span style="color:var(--green)">✓ 有效</span>' : '<span style="color:var(--red)">✗ 无效</span>';
  document.getElementById('accUserId').textContent = data.user_id || '-';
  document.getElementById('accCookieCount').textContent = data.cookie_count + ' 个';
  document.getElementById('accNtp').textContent = data.ntp_offset_ms.toFixed(1) + ' ms';
  document.getElementById('accLastGrab').textContent = data.last_grab_time || '-';
  document.getElementById('accLastResult').textContent = data.last_grab_result || '-';
  // Fill config form
  const eh = document.getElementById('editHour');
  if (eh && !eh.dataset.filled) {
    eh.value = data.grab_hour; eh.dataset.filled = '1';
    document.getElementById('editMin').value = data.grab_minute;
    document.getElementById('editSec').value = data.grab_second;
    document.getElementById('editPreSec').value = data.pre_start_sec || 10;
    document.getElementById('editEndHour').value = data.end_hour || 0;
    document.getElementById('editEndMin').value = data.end_minute || 0;
    document.getElementById('editEndSec').value = data.end_second || 30;
    document.getElementById('editThreads').value = data.thread_count || 5;
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

// === Config Functions ===
async function saveConfig() {
  const cfg = {
    grab_hour: parseInt(document.getElementById('editHour').value) || 0,
    grab_minute: parseInt(document.getElementById('editMin').value) || 0,
    grab_second: parseInt(document.getElementById('editSec').value) || 0,
    pre_start_sec: parseInt(document.getElementById('editPreSec').value) || 10,
    end_hour: parseInt(document.getElementById('editEndHour').value) || 0,
    end_minute: parseInt(document.getElementById('editEndMin').value) || 0,
    end_second: parseInt(document.getElementById('editEndSec').value) || 30,
    thread_count: parseInt(document.getElementById('editThreads').value) || 5,
  };
  try {
    const r = await fetch('/api/config', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(cfg) });
    const d = await r.json();
    if (d.success) showToast('配置已保存!', 'success');
    else showToast('保存失败: ' + (d.error||''), 'error');
  } catch(e) { showToast('请求失败: '+e.message, 'error'); }
}
function resetConfig() {
  document.getElementById('editHour').value = 0;
  document.getElementById('editMin').value = 0;
  document.getElementById('editSec').value = 0;
  document.getElementById('editPreSec').value = 10;
  document.getElementById('editEndHour').value = 0;
  document.getElementById('editEndMin').value = 0;
  document.getElementById('editEndSec').value = 30;
  document.getElementById('editThreads').value = 5;
  showToast('已恢复默认值，点击保存生效', 'info');
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
  const oldPw = document.getElementById('pwdOld').value;
  const newPw = document.getElementById('pwdNew').value;
  const confirm = document.getElementById('pwdConfirm').value;
  if (!oldPw || !newPw) { showToast('请填写所有密码字段', 'error'); return; }
  if (newPw.length < 4) { showToast('新密码至少4位', 'error'); return; }
  if (newPw !== confirm) { showToast('两次新密码不一致', 'error'); return; }
  try {
    const r = await fetch('/api/change-password', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({old_password:oldPw, new_password:newPw})
    });
    const d = await r.json();
    if (d.success) {
      showToast('✅ 密码修改成功', 'success');
      document.getElementById('pwdOld').value = '';
      document.getElementById('pwdNew').value = '';
      document.getElementById('pwdConfirm').value = '';
    } else {
      showToast('❌ ' + (d.error||'修改失败'), 'error');
    }
  } catch(e) { showToast('请求失败: '+e.message, 'error'); }
}

async function logout() {
  if (!confirm('确定退出登录？')) return;
  await fetch('/api/logout', { method:'POST' });
  window.location.href = '/login';
}

// === Account Functions ===
async function saveAccount() {
  const token = document.getElementById('inputToken').value.trim();
  const userId = document.getElementById('inputUserId').value.trim();
  const cookiesStr = document.getElementById('inputCookies').value.trim();
  if (!token && !cookiesStr) { showToast('请至少填写 Access Token 或 Cookie', 'error'); return; }
  try {
    const r = await fetch('/api/account', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ access_token: token, user_id: userId, cookie_string: cookiesStr })
    });
    const d = await r.json();
    if (d.success) {
      showToast('账号已保存!', 'success');
      document.getElementById('inputToken').value = '';
      document.getElementById('inputUserId').value = '';
      document.getElementById('inputCookies').value = '';
    } else showToast('保存失败: ' + (d.error||''), 'error');
  } catch(e) { showToast('请求失败: '+e.message, 'error'); }
}

async function testCookie() {
  const btn = document.getElementById('btnTestCookie');
  const result = document.getElementById('testResult');
  btn.disabled = true;
  btn.textContent = '⏳ 测试中...';
  result.style.display = 'block';
  result.className = 'test-result loading';
  result.innerHTML = '🔄 正在测试 Cookie 是否有效，请稍候...';
  try {
    const r = await fetch('/api/test-cookie', { method:'POST' });
    const d = await r.json();
    if (d.valid) {
      result.className = 'test-result ok';
      result.innerHTML = '✅ <strong>Cookie 有效!</strong><br>用户ID: ' + (d.user_id||'-') + '<br>昵称: ' + (d.nickname||'-');
    } else {
      result.className = 'test-result fail';
      result.innerHTML = '❌ <strong>Cookie 无效或已过期</strong><br>' + (d.error||'请重新获取 Cookie');
    }
  } catch(e) {
    result.className = 'test-result fail';
    result.innerHTML = '❌ 请求失败: ' + e.message;
  }
  btn.disabled = false;
  btn.textContent = '🧪 测试 Cookie';
}

async function clearAccount() {
  if (!confirm('确定要清除当前账号信息吗？')) return;
  try {
    await fetch('/api/account', { method:'DELETE' });
    showToast('账号已清除', 'info');
    document.getElementById('inputToken').value = '';
    document.getElementById('inputUserId').value = '';
    document.getElementById('inputCookies').value = '';
  } catch(e) { showToast('清除失败: '+e.message, 'error'); }
}

// 加载当前账号到表单（用于编辑）
async function loadAccountToForm() {
  try {
    const r = await fetch('/api/state');
    const d = await r.json();
    const token = d.state.access_token || '';
    const uid = d.state.user_id || '';
    const cookies = d.state.cookies || {};
    // 把 cookies 拼成字符串
    const cookieStr = Object.entries(cookies).map(([k,v]) => k+'='+v).join('; ');
    document.getElementById('inputToken').value = token;
    document.getElementById('inputUserId').value = uid;
    document.getElementById('inputCookies').value = cookieStr;
    document.getElementById('accountFormTitle').textContent = '✏️ 编辑当前账号 (修改后点保存)';
    showToast('已加载当前账号数据到表单，可直接编辑保存', 'info');
  } catch(e) { showToast('加载失败: '+e.message, 'error'); }
}

function updateCountdown(data) {
  window._grabHour = data.grab_hour;
  window._grabMin = data.grab_minute;
  window._grabSec = data.grab_second;
  window._timeOffset = (data.sync && data.sync.offset_ms) ? data.sync.offset_ms : (data.ntp_offset_ms || 0);
  window._grabStatus = data.status || 'idle';
  const target = new Date();
  target.setHours(data.grab_hour, data.grab_minute, data.grab_second, 0);
  const now = new Date();
  if (now >= target) target.setDate(target.getDate() + 1);
  const dateStr = target.toLocaleDateString('zh-CN', {month:'long', day:'numeric'});
  document.getElementById('countdownLabel').textContent = `${dateStr} ${data.next_grab} (PDD服务器时间)`;
}

// 毫秒级平滑倒计时 (requestAnimationFrame 驱动)
function tickCountdown() {
  const el = document.getElementById('countdown');
  if (!el) { requestAnimationFrame(tickCountdown); return; }

  const now = Date.now();
  const offset = window._timeOffset || 0;
  const correctedNow = now + offset;
  const status = window._grabStatus || 'idle';

  const target = new Date();
  target.setHours(window._grabHour || 0, window._grabMin || 0, window._grabSec || 0, 0);
  if (Date.now() >= target.getTime()) target.setDate(target.getDate() + 1);

  const diff = target.getTime() - correctedNow;
  if (status === 'grabbing') {
    el.textContent = '🔥 抢券中...';
    el.style.color = 'var(--red)';
  } else if (status === 'success') {
    el.textContent = '✅ 抢券成功!';
    el.style.color = 'var(--green)';
  } else if (diff <= 0) {
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
  requestAnimationFrame(tickCountdown);
}
requestAnimationFrame(tickCountdown);

// === Log Scroll Control ===
let _lastLogCount = 0;
let _logScrollLocked = false; // 用户手动滚动时锁定

function makeLogHtml(logs) {
  if (!logs.length) return '<div style="text-align:center;color:var(--text3);padding:40px">暂无日志</div>';
  return logs.slice().reverse().map(l => `
    <div class="log-item">
      <span class="log-time">${l.time}</span>
      <span class="log-tag ${l.module}">${l.module}</span>
      <span class="log-msg log-level-${l.level}">${l.message}</span>
    </div>
  `).join('');
}

// 滚动事件：用户滚到底部时自动勾选"跟随最新"
function onLogScroll(el) {
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
  if (atBottom) { const c=document.getElementById('autoFollowLogs');if(c)c.checked=true; }
}
function onLogScrollFull(el) {
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
  if (atBottom) document.getElementById('autoFollowLogsFull').checked=true;
}

function updateLogs(logs) {
  const el = document.getElementById('logList');
  const logFull = document.getElementById('logListFull');
  document.getElementById('logCount').textContent = `(${logs.length}条)`;
  const lcFull = document.getElementById('logCountFull');
  if (lcFull) lcFull.textContent = `(${logs.length}条)`;

  const html = makeLogHtml(logs);

  // 判断是否需要跟随（两个日志区域共用一个状态）
  const follow = document.getElementById('autoFollowLogs')?.checked || document.getElementById('autoFollowLogsFull')?.checked;

  for (const target of [el, logFull]) {
    if (!target) continue;
    const wasAtBottom = target.scrollHeight - target.scrollTop - target.clientHeight < 30;

    target.innerHTML = html;

    // 只在"跟随最新"开启 或 原本就在底部时 自动滚到底部
    if (follow || wasAtBottom) {
      target.scrollTop = target.scrollHeight;
    }
  }

  _lastLogCount = logs.length;
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
    updateDashboard(data.state);
    updateCountdown(data.state);
    updateLogs(data.logs);
    updateHistory(data.history);
    // Account: token preview + cookie list + pre-fill form
    const tokenPrev = document.getElementById('accTokenPreview');
    if (tokenPrev) tokenPrev.textContent = data.state.access_token || '未加载';
    const cookieList = document.getElementById('accCookieList');
    if (cookieList && data.state.cookies) {
      cookieList.innerHTML = Object.entries(data.state.cookies).map(([k,v]) =>
        `<div class="account-row"><span class="account-label">${k}</span><span class="account-value" style="font-size:11px">${String(v).substring(0,40)}${String(v).length>40?'...':''}</span></div>`
      ).join('') || '<div style="color:var(--text3);padding:20px;text-align:center">无 Cookie</div>';
    }
    // 首次加载时预填账号表单（如果表单为空）
    const inputToken = document.getElementById('inputToken');
    if (inputToken && !inputToken.value && data.state.access_token) {
      inputToken.value = data.state.access_token;
      document.getElementById('inputUserId').value = data.state.user_id || '';
      const cookies = data.state.cookies || {};
      document.getElementById('inputCookies').value = Object.entries(cookies).map(([k,v]) => k+'='+v).join('; ');
    }
    // 同步状态显示
    const syncEl = document.getElementById('syncStatus');
    if (syncEl && data.sync) {
      const s = data.sync;
      const srcIcons = {pdd:'🎯', ntp:'🌐', local:'⚠️'};
      const srcNames = {pdd:'PDD服务器', ntp:'NTP', local:'本地'};
      const icon = srcIcons[s.source] || '⚠️';
      const name = srcNames[s.source] || '本地';
      syncEl.innerHTML = `${icon} <b>${name}</b> | 偏移 <b style="color:${Math.abs(s.offset_ms)<100?'var(--green)':'var(--orange)'}">${s.offset_ms>=0?'+':''}${s.offset_ms.toFixed(2)}ms</b> | RTT ${s.last_rtt_ms.toFixed(0)}ms | ${s.samples}次采样 | 范围 ${s.min_offset.toFixed(1)}~${s.max_offset.toFixed(1)}ms`;
    }
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
    # Load token data for account page
    token_data = {}
    try:
        token_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pdd_token")
        if os.path.exists(token_file):
            with open(token_file, "r", encoding="utf-8") as f:
                token_data = json.load(f)
    except Exception:
        pass

    state = dict(STATE)
    state["access_token"] = token_data.get("access_token", "")
    state["cookies"] = token_data.get("cookies", {})

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
    })


# ============================================================
# API: 保存配置
# ============================================================
@app.route("/api/config", methods=["POST"])
@login_required
def api_save_config():
    try:
        data = request.get_json()
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
                f"配置已更新: {STATE['next_grab']} | "
                f"提前{STATE['pre_start_sec']}s | "
                f"结束{STATE['end_hour']:02d}:{STATE['end_minute']:02d}:{STATE['end_second']:02d} | "
                f"{STATE['thread_count']}线程")

        # 更新调度器触发时间（目标时间 - 提前秒数）
        try:
            global _scheduler_ref
            if _scheduler_ref is not None:
                from apscheduler.triggers.cron import CronTrigger
                pre_sec = STATE["pre_start_sec"]
                th = STATE["grab_hour"]
                tm = STATE["grab_minute"]
                ts = STATE["grab_second"] - pre_sec
                if ts < 0:
                    ts += 60
                    tm -= 1
                    if tm < 0:
                        tm += 60
                        th -= 1
                        if th < 0:
                            th += 24
                new_trigger = CronTrigger(
                    hour=th, minute=tm, second=ts,
                    timezone=BJT,
                )
                _scheduler_ref.reschedule_job("pdd_coupon_grab", trigger=new_trigger)
                add_log("info", "系统", f"调度器已重新定时: {th:02d}:{tm:02d}:{ts:02d} 触发 (提前{pre_sec}s)")
                print(f"[Dashboard] 调度器已更新: {th:02d}:{tm:02d}:{ts:02d}")
            else:
                add_log("warn", "系统", "调度器未注册，无法更新时间")
                print(f"[Dashboard] 调度器未注册!")
        except Exception as e:
            add_log("warn", "系统", f"调度器更新时间失败: {e}")
            print(f"[Dashboard] 调度器更新失败: {e}")

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


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

    _grab_thread = threading.Thread(target=_run, daemon=True)
    _grab_thread.start()

    add_log("info", "抢券", "手动测试抢券已触发 (跳过等待)")
    return jsonify({"success": True, "message": "抢券已触发，请查看日志"})


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
# API: 保存账号
# ============================================================
def _load_token_file():
    token_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pdd_token")
    if os.path.exists(token_file):
        with open(token_file, "r", encoding="utf-8") as f:
            return json.load(f), token_file
    return {}, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pdd_token")


@app.route("/api/account", methods=["POST"])
@login_required
def api_save_account():
    try:
        data = request.get_json()
        token_str = data.get("access_token", "").strip()
        user_id = data.get("user_id", "").strip()
        cookie_str = data.get("cookie_string", "").strip()

        # 解析 cookie 字符串
        cookies = {}
        if cookie_str and "=" in cookie_str:
            for pair in cookie_str.split(";"):
                pair = pair.strip()
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    k = k.strip().lstrip("+").strip()
                    cookies[k] = v.strip()

        # 从 cookie 中提取 token
        if not token_str:
            token_str = cookies.get("PDDAccessToken", "")
        if not user_id:
            user_id = cookies.get("pdd_user_id", "")

        # 如果只提供了 token 没有 cookie，构建最小 cookie
        if token_str and not cookies:
            cookies["PDDAccessToken"] = token_str
            if user_id:
                cookies["pdd_user_id"] = user_id

        if not token_str:
            return jsonify({"success": False, "error": "未检测到 Access Token"})

        # 保存
        token_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pdd_token")
        with open(token_file_path, "w", encoding="utf-8") as f:
            json.dump({
                "access_token": token_str,
                "user_id": user_id,
                "cookies": cookies,
                "saved_at": datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S"),
            }, f, ensure_ascii=False, indent=2)

        # 更新 STATE
        STATE["token_valid"] = True
        STATE["user_id"] = user_id
        STATE["cookie_count"] = len(cookies)

        add_log("info", "登录", f"账号已保存: Token={token_str[:20]}... UserID={user_id} Cookie={len(cookies)}个")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/account", methods=["DELETE"])
@login_required
def api_clear_account():
    try:
        token_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pdd_token")
        if os.path.exists(token_file_path):
            os.remove(token_file_path)
        STATE["token_valid"] = False
        STATE["user_id"] = ""
        STATE["cookie_count"] = 0
        add_log("info", "系统", "账号信息已清除")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ============================================================
# API: 测试 Cookie
# ============================================================
@app.route("/api/test-cookie", methods=["POST"])
@login_required
def api_test_cookie():
    try:
        import requests as req
        token_data, _ = _load_token_file()
        cookies = token_data.get("cookies", {})
        token = token_data.get("access_token", "")

        if not token and not cookies:
            return jsonify({"valid": False, "error": "未找到已保存的 Cookie，请先添加账号"})

        # 用 cookie 访问 PDD 验证登录态
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36",
            "Referer": "https://mobile.yangkeduo.com/",
        }
        resp = req.get(
            "https://mobile.yangkeduo.com/",
            cookies=cookies,
            headers=headers,
            timeout=10,
            allow_redirects=False,
        )

        # 检查响应：如果返回 302 到 login 页，说明 cookie 失效
        if resp.status_code in (301, 302):
            location = resp.headers.get("Location", "")
            if "login" in location:
                return jsonify({"valid": False, "error": "Cookie 已过期，被重定向到登录页"})

        # 尝试访问 API 获取用户信息
        resp2 = req.get(
            f"https://mobile.yangkeduo.com/proxy/api/api/server/_stm?pdduid={token_data.get('user_id', '')}",
            cookies=cookies,
            headers=headers,
            timeout=10,
        )

        if resp2.status_code == 200:
            # Cookie 基本有效
            user_id = token_data.get("user_id", cookies.get("pdd_user_id", "-"))
            return jsonify({
                "valid": True,
                "user_id": user_id,
                "nickname": f"用户 {user_id}",
            })
        else:
            return jsonify({
                "valid": resp.status_code == 200,
                "error": f"API 返回状态码 {resp2.status_code}，Cookie 可能已失效",
            })

    except ImportError:
        return jsonify({"valid": False, "error": "需要安装 requests 库: pip install requests"})
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
