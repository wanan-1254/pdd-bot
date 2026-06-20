"""
PDD anti_content Token 生成器 - Python 封装
通过 Node.js 子进程调用 PDD 的 RC4 加密算法生成 anti_content Token
支持进程复用，大幅降低生成延迟
"""
import subprocess
import json
import time
import os
import logging
import threading

logger = logging.getLogger("pdd_coupon")

# Node.js 脚本路径
_JS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdd_token_gen.js")


class NodeTokenPool:
    """
    Node.js Token 生成进程池。
    复用单个 Node.js 进程，通过 stdin/stdout 通信，
    避免每次生成 Token 都 spawn 新进程 (~150ms 开销)。
    """

    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()

    def _ensure_process(self):
        """确保 Node.js 进程正在运行"""
        if self._proc is None or self._proc.poll() is not None:
            try:
                self._proc = subprocess.Popen(
                    ["node", _JS_PATH],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=os.path.dirname(os.path.abspath(__file__)),
                    text=True,
                )
                logger.info("Node.js Token 进程已启动")
            except FileNotFoundError:
                raise RuntimeError("未找到 Node.js，请安装: https://nodejs.org")

    def generate(self, server_time: int) -> str:
        """生成单个 Token"""
        with self._lock:
            self._ensure_process()
            try:
                self._proc.stdin.write(json.dumps({"serverTime": server_time}) + "\n")
                self._proc.stdin.flush()
                token = self._proc.stdout.readline().strip()
                if not token:
                    # 进程可能崩溃，重启重试
                    self._proc = None
                    self._ensure_process()
                    self._proc.stdin.write(json.dumps({"serverTime": server_time}) + "\n")
                    self._proc.stdin.flush()
                    token = self._proc.stdout.readline().strip()
                if not token:
                    raise RuntimeError("Token 生成为空")
                return token
            except Exception as e:
                self._proc = None
                raise RuntimeError(f"Token 生成失败: {e}")

    def generate_batch(self, count: int, server_time: int) -> list:
        """批量生成 Token"""
        tokens = []
        for i in range(count):
            try:
                t = self.generate(server_time + i)
                tokens.append(t)
            except Exception as e:
                logger.warning(f"Token #{i+1} 生成失败: {e}")
        return tokens

    def close(self):
        """关闭 Node.js 进程"""
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None


# 全局 Token 池实例
_pool = NodeTokenPool()


def generate_anti_content(server_time: int = None) -> str:
    """
    生成 PDD anti_content Token

    Args:
        server_time: PDD 服务器时间 (毫秒时间戳)，为 None 时使用本地时间

    Returns:
        anti_content token 字符串

    Raises:
        RuntimeError: Token 生成失败
    """
    if server_time is None:
        server_time = int(time.time() * 1000)
    return _pool.generate(server_time)


def generate_batch(count: int = 10, server_time: int = None) -> list:
    """
    批量生成 Token

    Args:
        count: 生成数量
        server_time: 基准服务器时间

    Returns:
        Token 列表
    """
    if server_time is None:
        server_time = int(time.time() * 1000)
    return _pool.generate_batch(count, server_time)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("=== PDD anti_content Token 生成器 ===\n")

    # 测试单个生成 (冷启动)
    t0 = time.time()
    token = generate_anti_content()
    elapsed = (time.time() - t0) * 1000
    print(f"单个 Token (冷启动 {elapsed:.0f}ms):")
    print(f"  长度: {len(token)}")
    print(f"  前缀: {token[:20]}\n")

    # 测试单个生成 (热启动，进程复用)
    t0 = time.time()
    token2 = generate_anti_content()
    elapsed = (time.time() - t0) * 1000
    print(f"单个 Token (热启动 {elapsed:.0f}ms):")
    print(f"  长度: {len(token2)}")
    print(f"  前缀: {token2[:20]}\n")

    # 测试批量生成
    t0 = time.time()
    tokens = generate_batch(15)
    elapsed = (time.time() - t0) * 1000
    print(f"批量 15 个 Token ({elapsed:.0f}ms, 平均 {elapsed/max(len(tokens),1):.0f}ms/个):")
    for i, t in enumerate(tokens):
        print(f"  #{i+1}: len={len(t)} pre={t[:20]}")
"""
PDD anti_content Token 生成器 - Python 封装
通过 Node.js 子进程调用 PDD 的 RC4 加密算法生成 anti_content Token
"""
import subprocess
import json
import time
import os
import logging

logger = logging.getLogger("pdd_coupon")

# Node.js 脚本路径
_JS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdd_token_gen.js")


def generate_anti_content(server_time: int = None) -> str:
    """
    生成 PDD anti_content Token
    
    Args:
        server_time: PDD 服务器时间 (毫秒时间戳)，为 None 时使用本地时间
    
    Returns:
        anti_content token 字符串
    
    Raises:
        RuntimeError: Token 生成失败
    """
    if server_time is None:
        server_time = int(time.time() * 1000)
    
    try:
        result = subprocess.run(
            ["node", _JS_PATH],
            input=json.dumps({"serverTime": server_time}),
            capture_output=True,
            text=True,
            timeout=10,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"Node.js 退出码 {result.returncode}: {result.stderr.strip()}")
        
        token = result.stdout.strip()
        if not token:
            raise RuntimeError("Token 为空")
        
        return token
        
    except subprocess.TimeoutExpired:
        raise RuntimeError("Token 生成超时 (10s)")
    except FileNotFoundError:
        raise RuntimeError("未找到 Node.js，请安装 Node.js: https://nodejs.org")
    except Exception as e:
        raise RuntimeError(f"Token 生成失败: {e}")


def generate_batch(count: int = 10, server_time: int = None) -> list:
    """
    批量生成 Token
    
    Args:
        count: 生成数量
        server_time: 基准服务器时间
    
    Returns:
        Token 列表
    """
    if server_time is None:
        server_time = int(time.time() * 1000)
    
    tokens = []
    for i in range(count):
        # 每次递增 1ms 模拟不同时间
        st = server_time + i
        try:
            token = generate_anti_content(st)
            tokens.append(token)
        except Exception as e:
            logger.warning(f"Token #{i+1} 生成失败: {e}")
    
    return tokens


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    print("=== PDD anti_content Token 生成器 ===\n")
    
    # 测试单个生成
    t0 = time.time()
    token = generate_anti_content()
    elapsed = (time.time() - t0) * 1000
    print(f"单个 Token ({elapsed:.0f}ms):")
    print(f"  长度: {len(token)}")
    print(f"  前缀: {token[:20]}")
    print(f"  完整: {token}\n")
    
    # 测试批量生成
    t0 = time.time()
    tokens = generate_batch(5)
    elapsed = (time.time() - t0) * 1000
    print(f"批量 5 个 Token ({elapsed:.0f}ms, 平均 {elapsed/len(tokens):.0f}ms/个):")
    for i, t in enumerate(tokens):
        print(f"  #{i+1}: len={len(t)} pre={t[:20]}")
