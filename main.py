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

# --- Token 文件 ---
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pdd_token")

# 北京时间时区
BJT = timezone(timedelta(hours=8))


# ============================================================
# PDD 服务器时间同步 (持续) + NTP (备用)
# ============================================================
PDD_OFFSET = 0.0       # PDD 服务器与本地的偏移 (秒)
NTP_OFFSET = 0.0       # NTP 偏移 (秒)
TIME_SOURCE = "local"  # pdd / ntp / local
OFFSET_HISTORY = []    # 偏移历史 [(timestamp_ms, offset_ms, rtt_ms), ...]
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
            OFFSET_HISTORY.append((time.time() * 1000, offset_ms, rtt_ms))
            logger.info(f"PDD 采样 #{i+1}: 偏移={offset_ms:+.1f}ms RTT={rtt_ms:.1f}ms")
        if i < samples - 1:
            time.sleep(0.3)

    # 限制历史长度
    if len(OFFSET_HISTORY) > MAX_HISTORY:
        OFFSET_HISTORY = OFFSET_HISTORY[-MAX_HISTORY:]

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


def now_bjt() -> datetime:
    """校正后的北京时间 (基于 PDD 服务器或 NTP)"""
    return datetime.now(timezone.utc) + timedelta(seconds=get_time_offset()) + timedelta(hours=8)


# ============================================================
# Token 加载
# ============================================================
def load_token_data() -> dict:
    """从 .pdd_token 加载 Token 和 Cookies"""
    if not os.path.exists(TOKEN_FILE):
        return {}
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"加载 .pdd_token 失败: {e}")
        return {}


# sync_time() 和 start_continuous_sync() 在 main() 中调用，
# 避免在 import 时触发 (dashboard 会 import main)


# ============================================================
# Playwright 抢券核心
# ============================================================
def run_grab_session():
    """
    抢券流程 (直接 HTTP API，持续点击窗口模式):
    1. 时间同步 + 加载 Cookie
    2. 精确等待到 开始时间 (目标时间 - 提前秒数)
    3. 多线程持续发送抢券请求，直到 结束时间
    4. 汇总结果
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from pdd_token import generate_anti_content
    import threading as _threading

    # 从 STATE 读取配置
    grab_hour = STATE.get("grab_hour", GRAB_HOUR)
    grab_minute = STATE.get("grab_minute", GRAB_MINUTE)
    grab_second = STATE.get("grab_second", GRAB_SECOND)
    pre_start_sec = STATE.get("pre_start_sec", PRE_START_SEC)
    end_hour = STATE.get("end_hour", END_HOUR)
    end_minute = STATE.get("end_minute", END_MINUTE)
    end_second = STATE.get("end_second", END_SECOND)
    thread_count = STATE.get("thread_count", THREAD_COUNT)

    logger.info("=" * 55)
    logger.info("开始抢券流程 (持续点击窗口模式)")
    logger.info(f"目标: {grab_hour:02d}:{grab_minute:02d}:{grab_second:02d} "
                f"提前 {pre_start_sec}s 开始 | 结束: {end_hour:02d}:{end_minute:02d}:{end_second:02d} "
                f"| {thread_count} 线程")
    logger.info("=" * 55)

    STATE["status"] = "grabbing"

    # 1. 时间同步
    sync_time()
    logger.info(f"时间源: {TIME_SOURCE} | 偏移: {get_time_offset()*1000:+.2f}ms")

    # 2. 加载 Cookie/Token
    token_data = load_token_data()
    cookies = token_data.get("cookies", {})
    access_token = token_data.get("access_token", "")

    if not cookies:
        logger.error("没有 Cookie，请先运行 python login.py")
        STATE["status"] = "failed"
        add_history(False, "没有 Cookie")
        return

    logger.info(f"Cookie: {len(cookies)}个 | AccessToken: {access_token[:20]}...")

    # 3. 计算时间窗口
    now = now_bjt()
    target = now.replace(hour=grab_hour, minute=grab_minute, second=grab_second, microsecond=0)
    end_time = now.replace(hour=end_hour, minute=end_minute, second=end_second, microsecond=0)

    # 如果目标时间已过，等明天
    if now >= target:
        target = target + timedelta(days=1)
        end_time = end_time + timedelta(days=1)
        logger.info(f"今日目标时间已过，等待明日: {target.strftime('%m-%d %H:%M:%S')}")

    # 开始时间 = 目标时间 - 提前秒数
    start_time = target - timedelta(seconds=pre_start_sec)

    # 如果结束时间小于开始时间 (跨天)
    if end_time <= start_time:
        end_time = end_time + timedelta(days=1)

    window_sec = (end_time - start_time).total_seconds()
    logger.info(f"点击窗口: {start_time.strftime('%H:%M:%S')} ~ {end_time.strftime('%H:%M:%S')} "
                f"({window_sec:.0f}秒)")

    # 预留 3 秒给 Token 预生成
    token_buffer = 3.0
    wait_until = start_time - timedelta(seconds=token_buffer)

    logger.info(f"当前时间: {now.strftime('%H:%M:%S.%f')[:-3]}")
    logger.info(f"等待至:   {wait_until.strftime('%H:%M:%S.%f')[:-3]} (预留 {token_buffer}s)")

    # SKIP_WAIT 跳过等待 (测试用)
    skip_wait = os.getenv("SKIP_WAIT", "false").lower() == "true"
    if skip_wait:
        logger.info("SKIP_WAIT=true, 跳过等待")
    else:
        while True:
            remaining = (wait_until - now_bjt()).total_seconds()
            if remaining <= 0:
                break
            if remaining > 1.0:
                time.sleep(min(remaining - 0.5, 60))
            elif remaining > 0.01:
                time.sleep(0.001)

    # 4. busy-wait 到精确开始时间
    if not skip_wait:
        while True:
            remaining = (start_time - now_bjt()).total_seconds()
            if remaining <= 0:
                break
            if remaining > 0.001:
                time.sleep(0.0001)

    fire_time = now_bjt()
    logger.info(f"开火! {fire_time.strftime('%H:%M:%S.%f')[:-3]}")

    # 5. 多线程持续发送抢券请求
    api_url = "https://mobile.yangkeduo.com/proxy/api/api/aurum/check_in/task/gain/award"
    base_headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Mobile Safari/537.36",
        "Content-Type": "application/json;charset=UTF-8",
        "Referer": "https://mobile.yangkeduo.com/charge_sign_coupon.html",
        "Origin": "https://mobile.yangkeduo.com",
    }

    task_id = os.getenv("PDD_TASK_ID", "MT829143858423691176")
    task_template_id = os.getenv("PDD_TASK_TEMPLATE_ID", "1")

    session = requests.Session()
    session.headers.update(base_headers)
    session.cookies.update(cookies)

    # 停止标志
    stop_event = _threading.Event()
    results = []
    results_lock = _threading.Lock()
    total_requests = [0]  # 用列表以便在线程中修改

    def worker(thread_id):
        """单个工作线程: 持续发送请求直到 stop_event"""
        count = 0
        while not stop_event.is_set():
            t0 = time.time()
            try:
                # 每次请求生成新的 anti_content token
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
                result = {"idx": count, "thread": thread_id, "status": resp.status_code,
                          "data": data, "elapsed": elapsed}
                with results_lock:
                    results.append(result)
                    total_requests[0] += 1
                if count % 5 == 0:
                    logger.info(f"[线程-{thread_id}] #{count} ({elapsed:.0f}ms): "
                                f"status={resp.status_code}")
            except Exception as e:
                elapsed = (time.time() - t0) * 1000
                with results_lock:
                    total_requests[0] += 1
                logger.warning(f"[线程-{thread_id}] 失败 ({elapsed:.0f}ms): {e}")

    # 启动工作线程
    threads = []
    for i in range(thread_count):
        t = _threading.Thread(target=worker, args=(i,))
        t.daemon = True
        threads.append(t)
        t.start()

    logger.info(f"{thread_count} 个工作线程已启动，持续发送直到 {end_time.strftime('%H:%M:%S')}")

    # 等待结束时间
    if not skip_wait:
        while True:
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

    # 停止所有线程
    stop_event.set()
    for t in threads:
        t.join(timeout=3)

    session.close()

    logger.info(f"点击窗口结束! 共发送 {total_requests[0]} 个请求")

    # 6. 分析结果
    success = False
    detail = ""
    if results:
        for r in sorted(results, key=lambda x: x.get("elapsed", 999)):
            data = r.get("data", {})
            if data.get("success") or data.get("error_code") == 0:
                logger.info(f"*** 抢券成功! *** 线程-{r.get('thread',0)} #{r['idx']}: "
                            f"{json.dumps(data, ensure_ascii=False)[:200]}")
                success = True
                detail = "抢券成功!"
                break

        if not success:
            for r in results:
                data = r.get("data", {})
                err = str(data.get("error_msg", "")) + str(data.get("errorMsg", ""))
                if any(kw in err for kw in ["已领完", "已抢完", "库存不足", "今日已领"]):
                    detail = err[:100]
                    logger.warning(f"券已抢完: {err}")
                    break
            if not detail:
                errors = [r.get("error", "") for r in results if r.get("error")]
                if errors:
                    detail = f"请求失败: {errors[0][:80]}"
                else:
                    detail = f"未抢到券 (已发 {total_requests[0]} 个请求)"
    else:
        detail = "所有请求均失败"

    # 7. 最终结果
    logger.info(f"抢券结束! 成功: {success} | {detail}")
    STATE["status"] = "success" if success else "failed"
    STATE["last_grab_time"] = datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S")
    STATE["last_grab_result"] = detail
    add_history(success, detail)

    return success


# ============================================================
# 初始化面板状态
# ============================================================
def init_state():
    """初始化 Web 面板状态"""
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
    })
    token_data = load_token_data()
    STATE["token_valid"] = bool(token_data.get("access_token"))
    STATE["user_id"] = token_data.get("user_id", "")
    STATE["cookie_count"] = len(token_data.get("cookies", {}))


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

    # 检查 Token
    token_data = load_token_data()
    if not token_data.get("cookies"):
        logger.warning("未找到有效 Token，请先运行: python login.py")
        try:
            from login import qrcode_login
            qrcode_login()
        except Exception as e:
            logger.error(f"登录失败: {e}")
            sys.exit(1)

    logger.info(f"Token 已就绪 (Cookie: {len(token_data.get('cookies', {}))}个)")
    STATE["status"] = "waiting"

    # 初始化时间同步
    sync_time()
    start_continuous_sync()

    # 更新时间偏移到面板
    STATE["ntp_offset_ms"] = get_time_offset() * 1000
    STATE["time_source"] = TIME_SOURCE
    STATE["pdd_offset_ms"] = PDD_OFFSET * 1000

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

    # 定时调度
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger

        global _scheduler
        _scheduler = BackgroundScheduler(timezone=BJT)
        trigger = CronTrigger(
            hour=STATE.get("grab_hour", GRAB_HOUR),
            minute=STATE.get("grab_minute", GRAB_MINUTE),
            second=STATE.get("grab_second", GRAB_SECOND),
            timezone=BJT,
        )
        _scheduler.add_job(
            run_grab_session,
            trigger=trigger,
            id="pdd_coupon_grab",
            name="拼多多抢券",
            max_instances=1,
        )

        grab_time = f"{STATE.get('grab_hour',0):02d}:{STATE.get('grab_minute',0):02d}:{STATE.get('grab_second',0):02d}"
        logger.info(f"调度器已启动，等待 {grab_time} ...")

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
