"""
拼多多签到券自动抢券脚本
直接 HTTP API + Node.js anti_content Token 生成
精确 PDD 服务器时间同步，毫秒级触发
"""

import os
import sys
import json
import time
import logging
import threading
import requests
from datetime import datetime, timezone, timedelta

import ntplib
from dashboard import start_dashboard, STATE, add_log, add_history, register_scheduler

# ============================================================
# 日志配置
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pdd_coupon")


class DashboardLogHandler(logging.Handler):
    """将日志转发到 Web 面板"""
    def emit(self, record):
        try:
            msg = self.format(record)
            # 过滤掉高频同步日志，避免刷屏
            if "[同步]" in msg:
                return
            level_map = {"INFO": "info", "WARNING": "warn", "ERROR": "error", "CRITICAL": "error"}
            level = level_map.get(record.levelname, "info")
            # 从消息内容推断模块
            module = "系统"
            if "NTP" in msg or "PDD" in msg or "时间" in msg or "偏移" in msg:
                module = "NTP"
            elif "抢券" in msg or "点击" in msg or "开火" in msg or "浏览器" in msg:
                module = "抢券"
            elif "签到" in msg or "打卡" in msg:
                module = "签到"
            elif "Token" in msg or "Cookie" in msg or "登录" in msg:
                module = "登录"
            elif "配置" in msg or "目标" in msg or "调度器" in msg:
                module = "配置"
            add_log(level, module, msg)
        except Exception:
            pass


dashboard_handler = DashboardLogHandler()
dashboard_handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(dashboard_handler)

# ============================================================
# 配置
# ============================================================
# --- 定时设置 (北京时间 UTC+8) ---
GRAB_HOUR = int(os.getenv("GRAB_HOUR", "0"))              # 抢券小时 (默认0点)
GRAB_MINUTE = int(os.getenv("GRAB_MINUTE", "0"))          # 抢券分钟
GRAB_SECOND = int(os.getenv("GRAB_SECOND", "0"))          # 抢券秒
PRE_START_SEC = int(os.getenv("PRE_START_SEC", "10"))     # 提前多少秒开始点击
END_HOUR = int(os.getenv("END_HOUR", "0"))                # 结束小时
END_MINUTE = int(os.getenv("END_MINUTE", "0"))            # 结束分钟
END_SECOND = int(os.getenv("END_SECOND", "30"))           # 结束秒

# --- 并发 ---
THREAD_COUNT = int(os.getenv("THREAD_COUNT", "5"))        # 并发线程数

# --- NTP ---
NTP_SERVER = os.getenv("NTP_SERVER", "ntp.aliyun.com")

# --- 浏览器 ---
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"  # Railway 用无头模式

# --- 页面 ---
COUPON_URL = os.getenv("COUPON_URL",
    "https://mobile.yangkeduo.com/charge_sign_coupon.html"
    "?source=deposit&_pdd_fs=1"
    "&refer_page_name=deposit&refer_page_id=10089"
)

# --- 账号文件 ---
ACCOUNTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pdd_accounts.json")
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pdd_token")  # 兼容旧文件

# --- Token 预生成队列 ---
TOKEN_QUEUE_SIZE = int(os.getenv("TOKEN_QUEUE_SIZE", "100"))  # 队列大小
_TOKEN_QUEUE = None
_TOKEN_QUEUE_LOCK = threading.Lock()
_TOKEN_QUEUE_THREAD = None
_TOKEN_QUEUE_STOP = threading.Event()

# 北京时间时区
BJT = timezone(timedelta(hours=8))


# ============================================================
# PDD 服务器时间同步 (持续) + NTP (备用)
# ============================================================
PDD_OFFSET = 0.0       # PDD 服务器与本地的偏移 (秒)
NTP_OFFSET = 0.0       # NTP 偏移 (秒)
TIME_SOURCE = "local"  # pdd / ntp / local
OFFSET_HISTORY = []    # 偏移历史 [(timestamp_ms, offset_ms, rtt_ms), ...]
OFFSET_HISTORY_LOCK = threading.Lock()  # 【修复】保护OFFSET_HISTORY的线程锁
MAX_HISTORY = 100      # 最多保留 100 条
SYNC_INTERVAL = 30     # 持续同步间隔 (秒)
_sync_running = False


def _single_pdd_sample(cookies: dict, user_id: str) -> tuple:
    """
    单次 PDD 时间采样。
    返回: (offset_ms, rtt_ms) 或 (None, None)
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36",
        "Referer": "https://mobile.yangkeduo.com/",
    }
    url = f"https://mobile.yangkeduo.com/proxy/api/api/server/_stm?pdduid={user_id}"
    try:
        local_before = time.time() * 1000
        resp = requests.get(url, cookies=cookies, headers=headers, timeout=5)
        local_after = time.time() * 1000
        if resp.status_code == 200:
            data = resp.json()
            pdd_time = data.get("server_time", 0)
            if pdd_time > 0:
                local_mid = (local_before + local_after) / 2
                rtt = local_after - local_before
                offset_ms = pdd_time - local_mid
                return offset_ms, rtt
    except Exception:
        pass
    return None, None


def get_pdd_server_offset(samples: int = 5) -> float:
    """
    通过 PDD 服务器 _stm 接口多次采样获取平均偏移。
    返回: 偏移量 (秒)
    """
    global OFFSET_HISTORY

    token_data = load_token_data()
    cookies = token_data.get("cookies", {})
    user_id = token_data.get("user_id", "")
    if not cookies:
        logger.warning("无 Cookie，无法获取 PDD 服务器时间")
        return 0.0

    offsets = []
    for i in range(samples):
        offset_ms, rtt_ms = _single_pdd_sample(cookies, user_id)
        if offset_ms is not None:
            offsets.append(offset_ms)
            with OFFSET_HISTORY_LOCK:
                OFFSET_HISTORY.append((time.time() * 1000, offset_ms, rtt_ms))
                if len(OFFSET_HISTORY) > MAX_HISTORY:
                    OFFSET_HISTORY = OFFSET_HISTORY[-MAX_HISTORY:]
            logger.info(f"PDD 采样 #{i+1}: 偏移={offset_ms:+.1f}ms RTT={rtt_ms:.1f}ms")
        if i < samples - 1:
            time.sleep(0.3)

    # 限制历史长度（已在循环中处理，这里删除重复代码）

    if offsets:
        avg = sum(offsets) / len(offsets)
        logger.info(f"PDD 同步完成: 平均偏移={avg:+.2f}ms ({len(offsets)}次采样)")
        return avg / 1000.0
    return 0.0


def get_ntp_offset() -> float:
    """NTP 备用: 获取 NTP 偏移 (秒)"""
    try:
        client = ntplib.NTPClient()
        response = client.request(NTP_SERVER, version=3, timeout=5)
        offset = response.offset
        logger.info(f"NTP 同步: 偏移={offset*1000:.2f}ms")
        return offset
    except Exception as e:
        logger.warning(f"NTP 同步失败: {e}")
        return 0.0


def sync_time():
    """时间同步: 优先 PDD 服务器时间，失败则用 NTP"""
    global PDD_OFFSET, NTP_OFFSET, TIME_SOURCE

    pdd_offset = get_pdd_server_offset()
    if abs(pdd_offset) > 0.0001:
        PDD_OFFSET = pdd_offset
        TIME_SOURCE = "pdd"
        logger.info(f"✓ PDD 服务器时间 (偏移: {PDD_OFFSET*1000:+.2f}ms)")
    else:
        NTP_OFFSET = get_ntp_offset()
        if abs(NTP_OFFSET) > 0.0001:
            TIME_SOURCE = "ntp"
            logger.info(f"✓ NTP 时间 (偏移: {NTP_OFFSET*1000:+.2f}ms)")
        else:
            TIME_SOURCE = "local"
            logger.warning("✗ 本地时间 (无可用同步)")

    # 同步到面板 STATE
    STATE["ntp_offset_ms"] = get_time_offset() * 1000
    STATE["time_source"] = TIME_SOURCE
    STATE["pdd_offset_ms"] = PDD_OFFSET * 1000


def _continuous_sync():
    """后台线程: 每 SYNC_INTERVAL 秒重新同步一次 PDD 时间"""
    global PDD_OFFSET, TIME_SOURCE, OFFSET_HISTORY
    logger.info(f"持续同步线程已启动 (间隔 {SYNC_INTERVAL}s)")
    while True:
        try:
            time.sleep(SYNC_INTERVAL)
            token_data = load_token_data()
            cookies = token_data.get("cookies", {})
            user_id = token_data.get("user_id", "")
            if not cookies:
                continue

            offset_ms, rtt_ms = _single_pdd_sample(cookies, user_id)
            if offset_ms is not None:
                with OFFSET_HISTORY_LOCK:
                    OFFSET_HISTORY.append((time.time() * 1000, offset_ms, rtt_ms))
                    if len(OFFSET_HISTORY) > MAX_HISTORY:
                        OFFSET_HISTORY = OFFSET_HISTORY[-MAX_HISTORY:]
                    # 用最近 10 次采样做平滑 (加权平均，越新权重越大)
                    recent = OFFSET_HISTORY[-10:]
                    weights = list(range(1, len(recent) + 1))
                    total_w = sum(weights)
                    smoothed = sum(o * w for (_, o, _), w in zip(recent, weights)) / total_w
                PDD_OFFSET = smoothed / 1000.0
                TIME_SOURCE = "pdd"
                logger.info(f"[同步] 偏移={offset_ms:+.1f}ms RTT={rtt_ms:.1f}ms → 平滑={smoothed:+.2f}ms")
        except Exception as e:
            logger.warning(f"[同步] 异常: {e}")


def start_continuous_sync():
    """启动持续同步后台线程"""
    global _sync_running
    if _sync_running:
        return
    _sync_running = True
    t = threading.Thread(target=_continuous_sync, daemon=True)
    t.start()


def get_time_offset() -> float:
    """获取当前时间偏移 (秒)"""
    if TIME_SOURCE == "pdd":
        return PDD_OFFSET
    elif TIME_SOURCE == "ntp":
        return NTP_OFFSET
    return 0.0


def get_sync_status() -> dict:
    """返回同步状态摘要 (供面板显示)"""
    history = OFFSET_HISTORY[-20:]
    offsets = [o for (_, o, _) in history]
    rtts = [r for (_, _, r) in history]
    return {
        "source": TIME_SOURCE,
        "offset_ms": get_time_offset() * 1000,
        "pdd_offset_ms": PDD_OFFSET * 1000,
        "last_rtt_ms": rtts[-1] if rtts else 0,
        "samples": len(OFFSET_HISTORY),
        "min_offset": min(offsets) if offsets else 0,
        "max_offset": max(offsets) if offsets else 0,
        "avg_offset": sum(offsets) / len(offsets) if offsets else 0,
        "avg_rtt": sum(rtts) / len(rtts) if rtts else 0,
        "history": [(int(t), round(o, 2), round(r, 2)) for t, o, r in history],
    }


# ============================================================
# Token 预生成队列机制
# ============================================================
def _token_producer_thread():
    """后台线程：持续生产 Token 填充队列"""
    global _TOKEN_QUEUE, _TOKEN_QUEUE_STOP
    from pdd_token import generate_anti_content
    
    logger.info(f"Token 生产者线程已启动 (队列大小={TOKEN_QUEUE_SIZE})")
    
    while not _TOKEN_QUEUE_STOP.is_set():
        try:
            with _TOKEN_QUEUE_LOCK:
                queue_len = len(_TOKEN_QUEUE) if _TOKEN_QUEUE else 0
            
            # 如果队列不足，批量生成补充
            if queue_len < TOKEN_QUEUE_SIZE // 2:
                batch_size = TOKEN_QUEUE_SIZE - queue_len
                server_time = int(time.time() * 1000 + get_time_offset() * 1000)
                tokens = []
                for i in range(batch_size):
                    token = generate_anti_content(server_time + i)
                    tokens.append(token)
                
                with _TOKEN_QUEUE_LOCK:
                    if _TOKEN_QUEUE is None:
                        _TOKEN_QUEUE = []
                    _TOKEN_QUEUE.extend(tokens)
                    queue_len = len(_TOKEN_QUEUE)
                
                logger.debug(f"Token 队列补充: +{batch_size} → {queue_len}/{TOKEN_QUEUE_SIZE}")
            
            time.sleep(0.1)  # 每100ms检查一次
        except Exception as e:
            logger.error(f"Token 生产异常: {e}")
            time.sleep(1)


def start_token_queue():
    """启动 Token 预生成队列"""
    global _TOKEN_QUEUE, _TOKEN_QUEUE_THREAD, _TOKEN_QUEUE_STOP
    
    with _TOKEN_QUEUE_LOCK:
        if _TOKEN_QUEUE is not None:
            return  # 已经启动
        _TOKEN_QUEUE = []
        _TOKEN_QUEUE_STOP.clear()
    
    _TOKEN_QUEUE_THREAD = threading.Thread(target=_token_producer_thread, daemon=True)
    _TOKEN_QUEUE_THREAD.start()
    logger.info("✓ Token 预生成队列已启动")


def stop_token_queue():
    """停止 Token 预生成队列"""
    global _TOKEN_QUEUE, _TOKEN_QUEUE_THREAD, _TOKEN_QUEUE_STOP
    
    _TOKEN_QUEUE_STOP.set()
    if _TOKEN_QUEUE_THREAD:
        _TOKEN_QUEUE_THREAD.join(timeout=2)
    
    with _TOKEN_QUEUE_LOCK:
        _TOKEN_QUEUE = None
        _TOKEN_QUEUE_THREAD = None
    
    logger.info("✗ Token 预生成队列已停止")


def get_token_from_queue() -> str:
    """
    从队列中获取一个 Token（非阻塞）。
    如果队列为空，则实时生成并记录警告。
    """
    from pdd_token import generate_anti_content
    
    with _TOKEN_QUEUE_LOCK:
        if _TOKEN_QUEUE and len(_TOKEN_QUEUE) > 0:
            token = _TOKEN_QUEUE.pop(0)
            queue_len = len(_TOKEN_QUEUE)
            # 只在队列极低时警告（减少日志噪音）
            if queue_len < TOKEN_QUEUE_SIZE // 10 and queue_len % 5 == 0:
                logger.warning(f"⚠️ Token 队列不足: {queue_len}/{TOKEN_QUEUE_SIZE}")
            return token
    
    # 队列为空，实时生成
    logger.warning("Token 队列为空，实时生成")
    server_time = int(time.time() * 1000 + get_time_offset() * 1000)
    return generate_anti_content(server_time)


def now_bjt() -> datetime:
    """校正后的北京时间 (基于 PDD 服务器或 NTP)"""
    return datetime.now(timezone.utc) + timedelta(seconds=get_time_offset()) + timedelta(hours=8)


# ============================================================
# 多账号存储层
# ============================================================
_default_config = {
    "grab_hour": GRAB_HOUR, "grab_minute": GRAB_MINUTE, "grab_second": GRAB_SECOND,
    "pre_start_sec": PRE_START_SEC,
    "end_hour": END_HOUR, "end_minute": END_MINUTE, "end_second": END_SECOND,
    "thread_count": THREAD_COUNT,
}


def _migrate_old_token():
    """迁移旧的 .pdd_token 单账号文件到新的多账号格式"""
    if not os.path.exists(TOKEN_FILE):
        return
    if os.path.exists(ACCOUNTS_FILE):
        return  # 已有账号文件，不迁移
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("access_token") or data.get("cookies"):
            account = {
                "id": f"acc_{int(time.time())}",
                "label": "默认账号",
                "access_token": data.get("access_token", ""),
                "user_id": data.get("user_id", ""),
                "cookies": data.get("cookies", {}),
                "enabled": True,
                "config": dict(_default_config),
                "saved_at": data.get("saved_at", datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S")),
            }
            save_accounts([account])
            logger.info("已迁移旧账号数据到多账号格式")
    except Exception as e:
        logger.warning(f"迁移旧 Token 失败: {e}")


def load_accounts() -> list:
    """加载账号列表"""
    _migrate_old_token()
    if not os.path.exists(ACCOUNTS_FILE):
        return []
    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            accounts = json.load(f)
        # 迁移: 确保每个账号都有 sign_in 字段
        need_save = False
        for acc in accounts:
            if "sign_in" not in acc:
                acc["sign_in"] = {
                    "finish_count": 0,
                    "gain_award_count": 0,
                    "display_status": 0,
                    "last_check": "",
                    "auto_sign_in": True,
                }
                need_save = True
        if need_save:
            save_accounts(accounts)
        return accounts
    except Exception as e:
        logger.warning(f"加载账号列表失败: {e}")
        return []


def save_accounts(accounts: list):
    """保存账号列表"""
    try:
        with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
            json.dump(accounts, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"保存账号列表失败: {e}")


def add_account(access_token: str, user_id: str = "", cookies: dict = None,
                label: str = "", config: dict = None) -> dict:
    """新增账号"""
    accounts = load_accounts()
    account = {
        "id": f"acc_{int(time.time() * 1000)}",
        "label": label or f"账号{len(accounts) + 1}",
        "access_token": access_token,
        "user_id": user_id,
        "cookies": cookies or {"PDDAccessToken": access_token},
        "enabled": True,
        "config": config or dict(_default_config),
        "sign_in": {
            "finish_count": 0,
            "gain_award_count": 0,
            "display_status": 0,
            "last_check": "",
            "auto_sign_in": True,
        },
        "saved_at": datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S"),
    }
    accounts.append(account)
    save_accounts(accounts)
    return account


def update_account(account_id: str, **kwargs) -> dict:
    """更新账号"""
    accounts = load_accounts()
    for acc in accounts:
        if acc["id"] == account_id:
            for k, v in kwargs.items():
                if k in ("label", "access_token", "user_id", "cookies", "enabled", "config", "sign_in"):
                    acc[k] = v
            save_accounts(accounts)
            return acc
    return {}


def delete_account(account_id: str):
    """删除账号"""
    accounts = load_accounts()
    accounts = [a for a in accounts if a["id"] != account_id]
    save_accounts(accounts)


def get_enabled_accounts() -> list:
    """获取所有启用的账号"""
    return [a for a in load_accounts() if a.get("enabled", True)]


def load_token_data() -> dict:
    """兼容旧接口: 返回第一个启用账号的数据"""
    accounts = load_accounts()
    enabled = [a for a in accounts if a.get("enabled", True)]
    if enabled:
        acc = enabled[0]
        return {
            "access_token": acc.get("access_token", ""),
            "user_id": acc.get("user_id", ""),
            "cookies": acc.get("cookies", {}),
        }
    # 兆底：尝试读旧文件
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


# sync_time() 和 start_continuous_sync() 在 main() 中调用，
# 避免在 import 时触发 (dashboard 会 import main)


# ============================================================
# PDD 签到 API 集成
# ============================================================
# task_id 是动态的(每个签到周期变化)，不传则服务端自动使用当前有效task_id
_PDD_TASK_TEMPLATE_ID = "1"
_PDD_QUERY_URL = "https://mobile.yangkeduo.com/proxy/api/api/aurum/check_in/task/query"
_PDD_SIGN_URL = "https://mobile.yangkeduo.com/proxy/api/api/aurum/check_in/task/sub_task/finish"


def _make_pdd_session(account: dict):
    """为指定账号创建已认证的 PDD HTTP Session（启用Keep-Alive）"""
    from pdd_token import generate_anti_content
    s = requests.Session()
    
    # 【核心优化】启用 TCP Keep-Alive，复用连接减少握手开销
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=10,  # 连接池大小
        pool_maxsize=10,      # 最大连接数
        max_retries=0         # 不自动重试（我们自己控制）
    )
    s.mount('https://', adapter)
    s.mount('http://', adapter)
    
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36",
        "Referer": "https://mobile.yangkeduo.com/charge_sign_coupon.html?source=deposit",
        "Origin": "https://mobile.yangkeduo.com",
        "Content-Type": "application/json;charset=UTF-8",
        # Keep-Alive 相关头
        "Connection": "keep-alive",
    })
    # 添加 Access Token 到 Header（如果存在）
    token = account.get("access_token", "")
    if token:
        s.headers["Access-Token"] = token
        s.headers["PDDAccessToken"] = token
    s.cookies.update(account.get("cookies", {}))
    return s


def query_sign_in_status(account: dict) -> dict:
    """
    查询账号的签到状态。
    返回: {success, finish_count, gain_award_count, display_status, can_sign, can_grab, raw_result}
    """
    from pdd_token import generate_anti_content
    user_id = account.get("user_id", "") or account.get("cookies", {}).get("pdd_user_id", "")
    label = account.get("label", user_id or "?")
    session = _make_pdd_session(account)

    try:
        anti_token = generate_anti_content(int(time.time() * 1000 + get_time_offset() * 1000))
        session.headers["anti-content"] = anti_token
        # pdduid 放在 URL 参数中（与PDD真实页面一致）
        query_url = f"{_PDD_QUERY_URL}?pdduid={user_id}" if user_id else _PDD_QUERY_URL
        query_body = {
            "request_source": 1,
            "anti_content": anti_token,
            "task_template_id": _PDD_TASK_TEMPLATE_ID,
            "pdduid": user_id,
        }
        logger.info(f"[签到查询] [{label}] 请求: {query_url}, body={query_body}")
        resp = session.post(query_url, json=query_body, timeout=10)
        logger.info(f"[签到查询] [{label}] 响应 status={resp.status_code}, text={resp.text[:500]}")

        data = resp.json()
        if not data.get("success"):
            return {"success": False, "error": data.get("errorMsg", "查询失败")}

        result = data.get("result", {})
        finish_count = result.get("finish_count", 0)
        gain_award_count = result.get("gain_award_count", 0)
        display_status = result.get("display_status", 0)

        # 判断能否签到
        sub_tasks = result.get("sub_task_list", [])
        can_sign = False
        if sub_tasks:
            btn = sub_tasks[0].get("check_in_button", {})
            can_sign = btn.get("can_click", False)

        # 判断能否抢券: 签到满5天且未领取
        can_grab = finish_count >= 5 and gain_award_count < finish_count

        return {
            "success": True,
            "finish_count": finish_count,
            "gain_award_count": gain_award_count,
            "display_status": display_status,
            "can_sign": can_sign,
            "can_grab": can_grab,
            "task_id": result.get("task_id", ""),
            "task_name": result.get("task_name", ""),
            "raw_result": result,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def perform_sign_in(account: dict) -> dict:
    """
    执行每日签到。
    返回: {success, message}
    """
    from pdd_token import generate_anti_content
    user_id = account.get("user_id", "") or account.get("cookies", {}).get("pdd_user_id", "")
    label = account.get("label", "?")
    session = _make_pdd_session(account)

    try:
        # 1. 先查询签到状态（获取动态 task_id）
        anti_token = generate_anti_content(int(time.time() * 1000 + get_time_offset() * 1000))
        session.headers["anti-content"] = anti_token
        query_url = f"{_PDD_QUERY_URL}?pdduid={user_id}" if user_id else _PDD_QUERY_URL
        query_body = {
            "request_source": 1,
            "anti_content": anti_token,
            "task_template_id": _PDD_TASK_TEMPLATE_ID,
            "pdduid": user_id,
        }
        logger.info(f"[签到] [{label}] 查询请求: {query_body}")
        resp = session.post(query_url, json=query_body, timeout=10)
        logger.info(f"[签到] [{label}] 查询响应 status={resp.status_code}")

        data = resp.json()
        logger.info(f"[签到] [{label}] 查询响应体: {str(data)[:500]}")

        if not data.get("success"):
            err = data.get("errorMsg", "查询失败")
            code = data.get("errorCode", "")
            return {"success": False, "message": f"签到前查询失败: {err} (code: {code})"}

        result = data.get("result", {})
        sub_tasks = result.get("sub_task_list", [])
        logger.info(f"[签到] [{label}] sub_task_list 长度={len(sub_tasks)}, 原始数据: {str(sub_tasks)[:800]}")

        if not sub_tasks:
            return {"success": False, "message": "无可用子任务(可能已签到或活动未开启)"}

        # 取第一个可点击签到的子任务
        sub_task = sub_tasks[0]
        btn = sub_task.get("check_in_button", {})
        can_click = btn.get("can_click", False)
        finish_count = result.get("finish_count", 0)

        logger.info(f"[签到] [{label}] can_click={can_click}, finish_count={finish_count}")

        if not can_click:
            display_status = result.get("display_status", 0)
            if finish_count >= 5:
                return {"success": False, "message": f"已满{finish_count}天，无需签到"}
            return {"success": False, "message": f"今日不可签到 (display_status={display_status}, 已签到{finish_count}天)"}

        # 2. 执行签到 — 使用查询返回的动态 task_id
        task_id = result.get("task_id", "")
        anti_token = generate_anti_content(int(time.time() * 1000 + get_time_offset() * 1000))
        session.headers["anti-content"] = anti_token
        
        sign_body = {
            "request_source": 1,
            "anti_content": anti_token,
            "task_id": task_id,
            "task_template_id": _PDD_TASK_TEMPLATE_ID,
        }
        if task_id:
            sign_body["task_id"] = task_id
        logger.info(f"[签到] [{label}] 签到请求: {sign_body}")
        # pdduid 放在 URL 参数中（与PDD真实页面一致）
        sign_url = f"{_PDD_SIGN_URL}?pdduid={user_id}" if user_id else _PDD_SIGN_URL
        resp = session.post(sign_url, json=sign_body, timeout=10)
        logger.info(f"[签到] [{label}] 签到响应 status={resp.status_code}")

        data = resp.json()
        resp_str = str(data)
        logger.info(f"[签到] [{label}] 签到响应体: {resp_str[:500]}")
        
        if data.get("success"):
            new_count = finish_count + 1
            return {"success": True, "message": f"签到成功 ({new_count}/5天)"}
        else:
            err = data.get("errorMsg", "签到失败")
            code = data.get("errorCode", "")
            
            # 如果还是失败，记录详细错误
            if code == "8070001":
                logger.warning(f"[签到] [{label}] 8070001错误，请检查Cookie是否过期")
            
            return {"success": False, "message": f"{err} (code: {code})"}
    except Exception as e:
        logger.error(f"[签到] [{label}] 异常: {e}", exc_info=True)
        return {"success": False, "message": str(e)}


def refresh_account_sign_in(account_id: str) -> dict:
    """查询并更新指定账号的签到状态"""
    accounts = load_accounts()
    acc = next((a for a in accounts if a["id"] == account_id), None)
    if not acc:
        return {"success": False, "error": "账号不存在"}

    status = query_sign_in_status(acc)
    if status["success"]:
        sign_in = acc.get("sign_in", {})
        sign_in["finish_count"] = status["finish_count"]
        sign_in["gain_award_count"] = status["gain_award_count"]
        sign_in["display_status"] = status["display_status"]
        sign_in["last_check"] = datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S")
        sign_in["can_sign"] = status["can_sign"]
        sign_in["can_grab"] = status["can_grab"]
        acc["sign_in"] = sign_in
        save_accounts(accounts)
        STATE["accounts"] = load_accounts()
    return status


def auto_sign_in_all():
    """为所有启用且开启自动签到的账号执行签到 (每个账号之间随机延迟防风控)"""
    import random as _rand
    accounts = get_enabled_accounts()
    results = []
    for i, acc in enumerate(accounts):
        # 每个账号之间随机延迟 30~180 秒，模拟人工操作
        if i > 0:
            delay = _rand.randint(30, 180)
            logger.info(f"[签到] 防风控延迟 {delay}秒 后处理下一个账号...")
            time.sleep(delay)
        sign_in = acc.get("sign_in", {})
        if not sign_in.get("auto_sign_in", True):
            continue
        label = acc.get("label", acc["id"])

        # 先查询状态
        status = query_sign_in_status(acc)
        if not status["success"]:
            logger.warning(f"[签到] [{label}] 查询失败: {status.get('error')}")
            results.append({"label": label, "action": "查询失败"})
            continue

        # 更新缓存的签到状态
        sign_in["finish_count"] = status["finish_count"]
        sign_in["gain_award_count"] = status["gain_award_count"]
        sign_in["display_status"] = status["display_status"]
        sign_in["last_check"] = datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S")
        sign_in["can_sign"] = status["can_sign"]
        sign_in["can_grab"] = status["can_grab"]

        if status["can_sign"]:
            # 执行签到
            result = perform_sign_in(acc)
            if result["success"]:
                logger.info(f"[签到] [{label}] 签到成功! ({status['finish_count']+1}/5天)")
                sign_in["finish_count"] = status["finish_count"] + 1
                results.append({"label": label, "action": "签到成功"})
            else:
                logger.warning(f"[签到] [{label}] 签到失败: {result['message']}")
                results.append({"label": label, "action": f"签到失败: {result['message']}"})
        elif status["can_grab"]:
            logger.info(f"[签到] [{label}] 已满5天可抢券 ({status['finish_count']}/5天)")
            results.append({"label": label, "action": "可抢券"})
        elif status["display_status"] == 40:
            logger.info(f"[签到] [{label}] 已领取，等待新周期 ({status['finish_count']}/5天)")
            results.append({"label": label, "action": "已领取"})
        else:
            logger.info(f"[签到] [{label}] 今日已签到 ({status['finish_count']}/5天)")
            results.append({"label": label, "action": "今日已签到"})

        # 保存更新的签到状态
        update_account(acc["id"], sign_in=sign_in)

    # 更新面板 STATE
    STATE["accounts"] = load_accounts()
    return results


def auto_query_all_sign_in():
    """每2小时自动查询所有账号的签到状态"""
    accounts = load_accounts()
    if not accounts:
        return

    logger.info(f"[自动查询] 开始查询 {len(accounts)} 个账号的签到状态...")
    for acc in accounts:
        label = acc.get("label", acc["id"])
        status = query_sign_in_status(acc)
        if status["success"]:
            sign_in = acc.get("sign_in", {})
            old_ds = sign_in.get("display_status", 0)
            new_ds = status["display_status"]

            sign_in["finish_count"] = status["finish_count"]
            sign_in["gain_award_count"] = status["gain_award_count"]
            sign_in["display_status"] = new_ds
            sign_in["last_check"] = datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S")
            sign_in["can_sign"] = status["can_sign"]
            sign_in["can_grab"] = status["can_grab"]

            # 检测新周期: 之前是已领取(40)，现在变了
            if old_ds == 40 and new_ds != 40:
                logger.info(f"[自动查询] [{label}] 检测到新周期开始! (旧状态=40 -> 新状态={new_ds})")

            update_account(acc["id"], sign_in=sign_in)
            fc = status["finish_count"]
            gc = status["gain_award_count"]
            can_grab = "可抢券" if status["can_grab"] else "不可抢券"
            can_sign = "可签到" if status["can_sign"] else ""
            logger.info(f"[自动查询] [{label}] 签到{fc}/5天 | 领奖{gc}次 | {can_grab} {can_sign}")
        else:
            logger.warning(f"[自动查询] [{label}] 查询失败: {status.get('error')}")

    # 更新面板 STATE
    STATE["accounts"] = load_accounts()
    logger.info("[自动查询] 查询完成")


# ============================================================
# Playwright 抢券核心
# ============================================================
def run_grab_session():
    """
    抢券流程 (多账号并发模式):
    1. 时间同步 + 加载所有启用账号
    2. 每个账号独立线程，按各自配置的时间窗口抢券
    3. 任一账号成功即停止所有线程
    4. 汇总结果
    """
    import threading as _threading
    from collections import deque
    from pdd_token import generate_anti_content

    # 1. 时间同步
    sync_time()
    logger.info(f"时间源: {TIME_SOURCE} | 偏移: {get_time_offset()*1000:+.2f}ms")

    # 2. 加载启用账号
    accounts = get_enabled_accounts()
    if not accounts:
        logger.error("没有启用的账号")
        STATE["status"] = "failed"
        add_history(False, "没有启用的账号")
        return

    # 签到状态查询（默认关闭，兼容旧版直接抢券行为）
    # 如需启用，设置环境变量 PDD_QUERY_SIGN_IN=true
    _query_sign_in = os.getenv("PDD_QUERY_SIGN_IN", "false").lower() == "true"

    eligible_accounts = []
    if _query_sign_in:
        # 检查签到状态，过滤除不可抢券的账号
        for acc in accounts:
            label = acc.get("label", acc["id"])
            # 抢券前始终从 PDD 刷新最新签到状态
            logger.info(f"[{label}] 查询签到状态...")
            status = query_sign_in_status(acc)
            si = acc.get("sign_in", {})
            if status["success"]:
                si["finish_count"] = status["finish_count"]
                si["gain_award_count"] = status["gain_award_count"]
                si["display_status"] = status["display_status"]
                si["can_sign"] = status["can_sign"]
                si["can_grab"] = status["can_grab"]
                si["task_id"] = status.get("task_id", "")
                si["last_check"] = datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S")
                update_account(acc["id"], sign_in=si)
            else:
                logger.warning(f"[{label}] 查询签到状态失败: {status.get('error')}，使用缓存数据")

            # 检查是否可抢券：优先使用签到查询返回的 can_grab 标志，失败则用缓存状态兜底
            if status["success"] and "can_grab" in status:
                can_grab = status["can_grab"]
            else:
                fc = si.get("finish_count", 0)
                ds = si.get("display_status", 0)
                can_grab = (fc >= 5 and ds != 40)

            # 默认启用强制抢券模式：即使签到不满5天也尝试抢券（兼容旧版行为）
            # 如需严格检查，设置环境变量 PDD_FORCE_GRAB=false
            force_grab = os.getenv("PDD_FORCE_GRAB", "true").lower() != "false"

            if not can_grab and not force_grab:
                fc = si.get("finish_count", 0)
                ds = si.get("display_status", 0)
                if ds == 40:
                    logger.warning(f"[{label}] 已领取过优惠券，需重新签到5天才能抢券 (跳过)")
                else:
                    logger.warning(f"[{label}] 签到{fc}/5天，不可抢券 (跳过)")
                continue

            if not can_grab and force_grab:
                logger.warning(f"[{label}] 签到{si.get('finish_count', 0)}/5天，但强制抢券模式已启用，继续尝试")
            else:
                logger.info(f"[{label}] 签到{si.get('finish_count', 0)}天 ✅ 可抢券 (ds={si.get('display_status', 0)}, gain={si.get('gain_award_count', 0)})")
            eligible_accounts.append(acc)
    else:
        # 默认：不查签到状态，直接全部账号抢券（与旧版一致）
        eligible_accounts = accounts
        logger.info(f"跳过签到状态查询，直接使用 {len(eligible_accounts)} 个账号抢券")

    if not eligible_accounts:
        logger.error("没有可抢券的账号 (需签到满5天)")
        STATE["status"] = "failed"
        add_history(False, "无可抢券账号(签到不满5天)")
        return

    accounts = eligible_accounts
    logger.info(f"可抢券账号: {len(accounts)} 个")

    logger.info("=" * 55)
    logger.info(f"开始抢券流程 (多账号并发模式 | {len(accounts)} 个账号)")
    for acc in accounts:
        cfg = acc.get("config", {})
        logger.info(f"  [{acc['label']}] "
                    f"目标 {cfg.get('grab_hour',0):02d}:{cfg.get('grab_minute',0):02d}:{cfg.get('grab_second',0):02d} "
                    f"提前{cfg.get('pre_start_sec',10)}s "
                    f"结束 {cfg.get('end_hour',0):02d}:{cfg.get('end_minute',0):02d}:{cfg.get('end_second',30):02d} "
                    f"{cfg.get('thread_count',5)}线程")
    logger.info("=" * 55)

    STATE["status"] = "grabbing"

    # 各账号独立抢券，互不影响
    account_results = {}  # {account_id: {success, detail, total_requests}}
    results_lock = _threading.Lock()
    skip_wait = os.getenv("SKIP_WAIT", "false").lower() == "true"

    api_url = "https://mobile.yangkeduo.com/proxy/api/api/aurum/check_in/task/gain/award"
    base_headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Mobile Safari/537.36",
        "Content-Type": "application/json;charset=UTF-8",
        "Referer": "https://mobile.yangkeduo.com/charge_sign_coupon.html",
        "Origin": "https://mobile.yangkeduo.com",
    }
    task_template_id = os.getenv("PDD_TASK_TEMPLATE_ID", "1")

    def account_grab_worker(account):
        """单个账号的抢券线程"""
        acc_id = account["id"]
        acc_label = account.get("label", acc_id)
        cookies = account.get("cookies", {})
        user_id = account.get("user_id", "") or account.get("cookies", {}).get("pdd_user_id", "")
        cfg = account.get("config", {})

        # 每个账号独立的成功标志
        acc_success = _threading.Event()

        grab_h = cfg.get("grab_hour", 0)
        grab_m = cfg.get("grab_minute", 0)
        grab_s = cfg.get("grab_second", 0)
        pre_sec = cfg.get("pre_start_sec", 10)
        end_h = cfg.get("end_hour", 0)
        end_m = cfg.get("end_minute", 0)
        end_s = cfg.get("end_second", 30)
        t_count = cfg.get("thread_count", 5)

        tag = f"[{acc_label}]"
        task_id = os.getenv("PDD_TASK_ID", "") or acc.get("sign_in", {}).get("task_id", "") or "MT829143858423691176"
        logger.info(f"{tag} 使用 task_id={task_id} (env PDD_TASK_ID={'已设置' if os.getenv('PDD_TASK_ID') else '未设置'}, sign_in_task_id={acc.get('sign_in', {}).get('task_id', '')})")

        # 计算时间窗口
        now = now_bjt()
        target = now.replace(hour=grab_h, minute=grab_m, second=grab_s, microsecond=0)
        end_time = now.replace(hour=end_h, minute=end_m, second=end_s, microsecond=0)

        grace = timedelta(seconds=5)
        if now >= target + grace:
            target += timedelta(days=1)
            end_time += timedelta(days=1)

        start_time = target - timedelta(seconds=pre_sec)
        if end_time <= start_time:
            end_time += timedelta(days=1)

        logger.info(f"{tag} 窗口: {start_time:%H:%M:%S} ~ {end_time:%H:%M:%S}")

        # 【核心优化】抢券前1分钟开始高频时间同步（每秒1次）
        high_freq_sync_start = start_time - timedelta(seconds=60)
        high_freq_sync_stop = start_time - timedelta(seconds=10)  # 最后10秒停止，避免网络抖动
        
        def _high_freq_sync():
            """高频时间同步线程（使用当前账号的Cookie）"""
            global PDD_OFFSET, TIME_SOURCE
            while True:
                now_check = now_bjt()
                if now_check >= high_freq_sync_stop:
                    break  # 到达停止时间，退出
                if now_check < high_freq_sync_start:
                    time.sleep(1)  # 还没到开始时间，等待
                    continue
                try:
                    # 【修复】使用当前账号的Cookie，而非全局load_token_data()
                    if not cookies:
                        time.sleep(1)
                        continue
                    offset_ms, rtt_ms = _single_pdd_sample(cookies, user_id)
                    if offset_ms is not None:
                        with OFFSET_HISTORY_LOCK:
                            OFFSET_HISTORY.append((time.time() * 1000, offset_ms, rtt_ms))
                            if len(OFFSET_HISTORY) > MAX_HISTORY:
                                OFFSET_HISTORY = OFFSET_HISTORY[-MAX_HISTORY:]
                            recent = OFFSET_HISTORY[-5:]  # 用最近5次做平滑
                            weights = list(range(1, len(recent) + 1))
                            total_w = sum(weights)
                            smoothed = sum(o * w for (_, o, _), w in zip(recent, weights)) / total_w
                        PDD_OFFSET = smoothed / 1000.0
                        TIME_SOURCE = "pdd"
                        logger.debug(f"[{acc_label}] 高频同步: 偏移={offset_ms:+.1f}ms → 平滑={smoothed:+.2f}ms")
                except Exception as e:
                    pass
                time.sleep(1)  # 每1秒同步一次
        
        _sync_thread = _threading.Thread(target=_high_freq_sync, daemon=True)
        _sync_thread.start()

        # 等待开始时间
        if not skip_wait:
            while True:
                remaining = (start_time - now_bjt()).total_seconds()
                if remaining <= 0:
                    break
                if remaining > 1.0:
                    time.sleep(min(remaining - 0.5, 60))
                elif remaining > 0.001:
                    time.sleep(0.001)

        logger.info(f"{tag} 开火! {now_bjt():%H:%M:%S.%f}")

        # 【核心优化】创建 HTTP Session（启用Keep-Alive + 连接池）
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=t_count * 2,  # 每个线程2个连接
            pool_maxsize=t_count * 2,
            max_retries=0
        )
        session.mount('https://', adapter)
        session.mount('http://', adapter)
        session.headers.update(base_headers)
        session.cookies.update(cookies)

        stop_event = _threading.Event()
        results = deque(maxlen=1000)
        r_lock = _threading.Lock()
        total_req = [0]

        def worker(tid):
            """工作子线程（含智能重试 + 超密集爆发）"""
            count = 0
            consecutive_failures = 0  # 连续失败次数（用于指数退避）
            burst_start_time = None   # 爆发期开始时间
            
            while not stop_event.is_set() and not acc_success.is_set():
                t0 = time.time()
                try:
                    # 【核心】每次请求实时生成 Token（与旧版一致，避免队列token时间戳过时）
                    anti_token = generate_anti_content(
                        int(time.time() * 1000 + get_time_offset() * 1000)
                    )
                    h = {"anti-content": anti_token}
                    payload = {
                        "request_source": 1,
                        "anti_content": anti_token,
                        "task_id": task_id,
                        "task_template_id": task_template_id,
                    }
                    resp = session.post(api_url, json=payload, headers=h, timeout=3)
                    elapsed = (time.time() - t0) * 1000
                    data = resp.json()
                    count += 1
                    with r_lock:
                        results.append({"idx": count, "thread": tid, "data": data, "elapsed": elapsed})
                        total_req[0] += 1
                    
                    # 【超密集爆发】记录爆发期开始时间
                    if burst_start_time is None:
                        burst_start_time = time.time()
                    
                    # 【智能重试】分析错误类型
                    error_code = data.get("error_code", data.get("errorCode", 0))
                    error_msg = data.get("errorMsg", "")
                    
                    _data_str = json.dumps(data, ensure_ascii=False)[:100]
                    logger.info(f"{tag} 线程-{tid} #{count} ({elapsed:.0f}ms) {resp.status_code} | {_data_str}")
                    if data.get("success") or error_code == 0:
                        add_log("success", "grab", f"{tag} thread-{tid} success! (#{count})")
                        logger.info(f"{tag} *** 抢券成功! *** 线程-{tid} #{count}")
                        acc_success.set()
                        stop_event.set()
                        return
                    elif error_code in [502, 503, 504, 'timeout'] or '超时' in error_msg or '网络' in error_msg:
                        # 网络类错误 → 立即重试（可能是临时故障）
                        consecutive_failures = 0
                        if count % 10 == 0:
                            logger.warning(f"{tag} 线程-{tid} 已发{count}个请求 (网络波动中...)")
                            add_log("warn", "grab", f"{tag} thread-{tid} sent {count} reqs (network: {error_msg[:30]})")
                    elif error_code in [6070001, 8070001]:
                        # Cookie/Token过期 → 停止该账号的抢券
                        logger.error(f"{tag} 线程-{tid} Cookie可能已过期 (code={error_code})，停止抢券")
                        add_log("error", "grab", f"{tag} thread-{tid} cookie expired code={error_code}")
                        stop_event.set()
                        return
                    elif '库存不足' in error_msg or '已领完' in error_msg or '售罄' in error_msg:
                        # 库存不足 → 停止所有线程
                        logger.warning(f"{tag} 线程-{tid} 库存不足/已领完，停止抢券")
                        add_log("warn", "grab", f"{tag} thread-{tid} sold out")
                        stop_event.set()
                        return
                    else:
                        # 其他错误 → 继续重试
                        consecutive_failures += 1
                        if count % 10 == 0:
                            logger.warning(f"{tag} 线程-{tid} 已发{count}个请求 (错误码: {error_code})")
                            add_log("warn", "grab", f"{tag} thread-{tid} sent {count} reqs (error: {error_code})")
                except Exception as e:
                    elapsed = (time.time() - t0) * 1000
                    count += 1
                    with r_lock:
                        total_req[0] += 1
                    consecutive_failures += 1
                    logger.info(f"{tag} 线程-{tid} #{count} 失败 ({elapsed:.0f}ms): {str(e)[:50]}")

        # 启动子线程
        threads = []
        for i in range(t_count):
            t = _threading.Thread(target=worker, args=(i,), daemon=True)
            threads.append(t)
            t.start()

        # 等待结束时间或成功标志
        if not skip_wait:
            while True:
                if acc_success.is_set() and stop_event.is_set():
                    break
                remaining = (end_time - now_bjt()).total_seconds()
                if remaining <= 0:
                    break
                if remaining > 0.5:
                    time.sleep(0.1)
                elif remaining > 0.001:
                    time.sleep(0.001)
        else:
            # 测试模式: 发送 5 秒
            time.sleep(5)

        # 停止子线程
        stop_event.set()
        for t in threads:
            t.join(timeout=3)
        session.close()

        # 分析结果
        success = acc_success.is_set() and any(
            r.get("data", {}).get("success") or r.get("data", {}).get("error_code") == 0
            for r in results
        )
        detail = ""
        if success:
            detail = f"{tag} 抢券成功!"
        elif results:
            for r in results:
                data = r.get("data", {})
                err = str(data.get("error_msg", "")) + str(data.get("errorMsg", ""))
                if any(kw in err for kw in ["已领完", "已抢完", "库存不足", "今日已领"]):
                    detail = f"{tag} {err[:80]}"
                    break
            if not detail:
                detail = f"{tag} 未抢到 (已发{total_req[0]}个请求)"
        else:
            detail = f"{tag} 无有效响应"

        logger.info(f"{tag} 结束: {total_req[0]}个请求 | {detail}")
        add_log("success" if success else "warn", "grab", f"{tag} {detail} ({total_req[0]} reqs)")
        with results_lock:
            account_results[acc_id] = {
                "success": success, "detail": detail, "total_requests": total_req[0]
            }

    # 3. 为每个账号启动独立线程
    acc_threads = []
    for acc in accounts:
        t = _threading.Thread(target=account_grab_worker, args=(acc,), daemon=True)
        acc_threads.append(t)
        t.start()

    # 4. 等待所有账号完成或全局成功
    for t in acc_threads:
        t.join(timeout=300)  # 最多等 5 分钟

    # 5. 汇总结果
    any_success = any(r.get("success") for r in account_results.values())
    total_all = sum(r.get("total_requests", 0) for r in account_results.values())
    details = [r.get("detail", "") for r in account_results.values() if r.get("detail")]
    detail = " | ".join(details[:3])  # 最多显示 3 个账号的结果

    logger.info(f"抢券结束! 成功: {any_success} | 总请求: {total_all} | {detail}")
    STATE["status"] = "success" if any_success else "failed"
    STATE["last_grab_time"] = datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S")
    STATE["last_grab_result"] = detail
    add_history(any_success, detail)

    return any_success


# ============================================================
# 初始化面板状态
# ============================================================
def init_state():
    """初始化 Web 面板状态"""
    accounts = load_accounts()
    STATE.update({
        "grab_hour": GRAB_HOUR,
        "grab_minute": GRAB_MINUTE,
        "grab_second": GRAB_SECOND,
        "next_grab": f"{GRAB_HOUR:02d}:{GRAB_MINUTE:02d}:{GRAB_SECOND:02d}",
        "pre_start_sec": PRE_START_SEC,
        "end_hour": END_HOUR,
        "end_minute": END_MINUTE,
        "end_second": END_SECOND,
        "thread_count": THREAD_COUNT,
        "uptime_start": time.time(),
        "accounts": accounts,
    })
    enabled = [a for a in accounts if a.get("enabled", True)]
    if enabled:
        STATE["token_valid"] = True
        STATE["user_id"] = enabled[0].get("user_id", "")
        STATE["cookie_count"] = len(enabled[0].get("cookies", {}))
    else:
        STATE["token_valid"] = False
        STATE["user_id"] = ""
        STATE["cookie_count"] = 0


# 全局调度器引用 (供 dashboard 更新触发时间)
_scheduler = None


def update_scheduler_time(hour: int, minute: int, second: int):
    """更新调度器的触发时间 (供 dashboard 配置保存时调用)"""
    global _scheduler
    if _scheduler is None:
        logger.warning("调度器未初始化，无法更新时间")
        return False
    try:
        from apscheduler.triggers.cron import CronTrigger
        new_trigger = CronTrigger(hour=hour, minute=minute, second=second, timezone=BJT)
        _scheduler.reschedule_job("pdd_coupon_grab", trigger=new_trigger)
        logger.info(f"调度器时间已更新: {hour:02d}:{minute:02d}:{second:02d}")
        return True
    except Exception as e:
        logger.warning(f"更新调度器失败: {e}")
        return False


def calc_earliest_trigger() -> tuple:
    """计算所有启用账号中最早的调度触发时间 (hour, minute, second)"""
    accounts = get_enabled_accounts()
    if not accounts:
        # 没有账号时用全局默认值
        pre = STATE.get("pre_start_sec", PRE_START_SEC)
        h, m, s = STATE.get("grab_hour", GRAB_HOUR), STATE.get("grab_minute", GRAB_MINUTE), STATE.get("grab_second", GRAB_SECOND)
        s = s - pre
        if s < 0:
            s += 60
            m -= 1
            if m < 0:
                m += 60
                h = (h - 1) % 24
        logger.info(f"[调度] 无启用账号，使用全局默认: {h:02d}:{m:02d}:{s:02d}")
        return h, m, s

    earliest_total_sec = None
    earliest_acc_label = None
    for acc in accounts:
        cfg = acc.get("config", {})
        total_sec = (cfg.get("grab_hour", 0) * 3600 +
                     cfg.get("grab_minute", 0) * 60 +
                     cfg.get("grab_second", 0) -
                     cfg.get("pre_start_sec", 10))
        if total_sec < 0:
            total_sec += 86400  # 跨天
        if earliest_total_sec is None or total_sec < earliest_total_sec:
            earliest_total_sec = total_sec
            earliest_acc_label = acc.get("label", "未命名")

    h = (earliest_total_sec // 3600) % 24
    m = (earliest_total_sec % 3600) // 60
    s = earliest_total_sec % 60
    logger.info(f"[调度] 最早触发账号: [{earliest_acc_label}], 时间: {h:02d}:{m:02d}:{s:02d} (总秒数={earliest_total_sec})")
    return h, m, s


# ============================================================
# 入口
# ============================================================
def main():
    # 初始化面板状态
    init_state()

    logger.info("拼多多签到券自动抢券脚本启动")
    logger.info(f"目标: 每日 {GRAB_HOUR:02d}:{GRAB_MINUTE:02d}:{GRAB_SECOND:02d} 抢30元话费券")

    # 启动 Web 面板
    port = int(os.getenv("PORT", "8080"))
    start_dashboard(port)
    logger.info(f"Web 面板已启动: http://0.0.0.0:{port}")

    # 检查账号
    accounts = get_enabled_accounts()
    if not accounts:
        logger.warning("未找到有效账号，请通过 Web 面板添加账号")
        logger.info("Web 面板地址: http://0.0.0.0:8080")
    else:
        logger.info(f"已加载 {len(accounts)} 个启用账号")
        for acc in accounts:
            logger.info(f"  - {acc.get('label', '未命名')}: Cookie {len(acc.get('cookies', {}))}个")

    STATE["status"] = "waiting"

    # 初始化时间同步
    sync_time()
    start_continuous_sync()
    
    # 启动 Token 预生成队列（核心优化）
    start_token_queue()

    # 更新时间偏移到面板
    STATE["ntp_offset_ms"] = get_time_offset() * 1000
    STATE["time_source"] = TIME_SOURCE
    STATE["pdd_offset_ms"] = PDD_OFFSET * 1000

    # ============================================================
    # Railway 自保活：每 3 分钟 ping /health，防止容器休眠
    # ============================================================
    def _keepalive():
        import requests as _req
        port = int(os.environ.get("PORT", 8080))
        while True:
            time.sleep(180)
            try:
                _req.get(f"http://127.0.0.1:{port}/health", timeout=5)
                logger.debug("保活心跳: ok")
            except Exception:
                pass

    _ka = threading.Thread(target=_keepalive, daemon=True)
    _ka.start()
    logger.info("保活线程已启动 (每180s心跳)")

    # 测试模式: 设置抢券时间为当前时间 +5 秒
    if os.getenv("RUN_TEST", "false").lower() == "true":
        now = now_bjt()
        test_time = now + timedelta(seconds=5)
        STATE["grab_hour"] = test_time.hour
        STATE["grab_minute"] = test_time.minute
        STATE["grab_second"] = test_time.second
        STATE["next_grab"] = test_time.strftime("%H:%M:%S")
        logger.info(f"测试模式: {STATE['next_grab']} 执行抢券 (5秒后)")
        run_grab_session()
        STATE["status"] = "waiting"
        return

    # 定时调度：在所有账号最早的开始时间触发
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger

        global _scheduler
        _scheduler = BackgroundScheduler(timezone=BJT)

        # 计算最早触发时间
        trigger_hour, trigger_minute, trigger_second = calc_earliest_trigger()

        trigger = CronTrigger(
            hour=trigger_hour,
            minute=trigger_minute,
            second=trigger_second,
            timezone=BJT,
        )
        _scheduler.add_job(
            run_grab_session,
            trigger=trigger,
            id="pdd_coupon_grab",
            name="拼多多抢券",
            max_instances=1,
        )

        logger.info(f"调度器已启动，触发时间 {trigger_hour:02d}:{trigger_minute:02d}:{trigger_second:02d} ...")

        # 每日自动签到 (固定基线时间 + 每账号随机延迟防风控)
        sign_in_hour = int(os.getenv("SIGN_IN_HOUR", "6"))
        sign_in_minute = int(os.getenv("SIGN_IN_MINUTE", "0"))
        sign_in_trigger = CronTrigger(hour=sign_in_hour, minute=sign_in_minute, second=0, timezone=BJT)
        _scheduler.add_job(
            auto_sign_in_all,
            trigger=sign_in_trigger,
            id="pdd_auto_sign_in",
            name="PDD每日自动签到",
            max_instances=1,
        )
        logger.info(f"自动签到已启用: 每天 {sign_in_hour:02d}:{sign_in_minute:02d} 开始 (每账号随机延迟30~180秒)")

        # 每 2 小时自动查询签到状态
        from apscheduler.triggers.interval import IntervalTrigger
        _scheduler.add_job(
            auto_query_all_sign_in,
            trigger=IntervalTrigger(hours=2),
            id="pdd_sign_in_query",
            name="PDD签到状态查询",
            max_instances=1,
        )
        logger.info("签到状态查询已启用: 每 2 小时自动查询")

        _scheduler.start()
        # 注册调度器到 dashboard，使配置保存时能更新时间
        register_scheduler(_scheduler)

        # 后台调度器不阻塞主线程，用 sleep 保持主进程运行
        try:
            while True:
                time.sleep(60)
        except (KeyboardInterrupt, SystemExit):
            logger.info("脚本已停止")
            _scheduler.shutdown()

    except ImportError:
        # 没有 APScheduler，用简单的 sleep 循环
        logger.info("APScheduler 未安装，使用简单定时")
        while True:
            now = now_bjt()
            target = now.replace(
                hour=GRAB_HOUR, minute=GRAB_MINUTE,
                second=GRAB_SECOND, microsecond=0,
            )
            if now >= target:
                target += timedelta(days=1)

            wait_sec = (target - now).total_seconds() - 10  # 提前10秒醒来
            if wait_sec > 0:
                logger.info(f"休眠 {wait_sec:.0f} 秒...")
                time.sleep(wait_sec)

            run_grab_session()
            time.sleep(60)  # 防止重复触发

    except (KeyboardInterrupt, SystemExit):
        logger.info("脚本已停止")


if __name__ == "__main__":
    main()
