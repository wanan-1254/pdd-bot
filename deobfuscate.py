"""
PDD risk-control-anti.js 反混淆 v3
重点: 提取完整的 XOR 旋转解码器，生成完整查找表
"""
import re
import base64
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

with open("risk-control-anti.js", "r", encoding="utf-8") as f:
    src = f.read()

# ============================================================
# 1. 提取字符串数组
# ============================================================
def extract_array(func_name):
    m = re.search(rf'function\s+{re.escape(func_name)}\s*\(\)\s*\{{\s*var\s+\w+\s*=\s*\[', src)
    if not m: return []
    start = m.end() - 1
    depth = 0
    for i in range(start, min(start+50000, len(src))):
        if src[i] == '[': depth += 1
        elif src[i] == ']':
            depth -= 1
            if depth == 0:
                return re.findall(r'"((?:[^"\\]|\\.)*)"', src[start:i+1])
    return []

arr_c  = extract_array("c")
arr_et = extract_array("et")
arr_h  = extract_array("h")

# ============================================================
# 2. 提取完整解码器函数源码
# ============================================================
def extract_function(func_name):
    """提取 function funcName(t,n){...} 的完整源码"""
    m = re.search(rf'function\s+{re.escape(func_name)}\s*\(t,n\)\s*\{{', src)
    if not m: return ""
    start = m.start()
    depth = 0
    for i in range(m.end()-1, min(m.end()+5000, len(src))):
        if src[i] == '{': depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                return src[start:i+1]
    return ""

func_a_src = extract_function("a")
func_M_src = extract_function("M")
func_s_src = extract_function("s")

print(f"解码器 a 源码: {len(func_a_src)} 字符")
print(f"解码器 M 源码: {len(func_M_src)} 字符")
print(f"解码器 s 源码: {len(func_s_src)} 字符")

# 打印解码器 a 的前 800 字符
print(f"\n=== 解码器 a 源码 ===")
print(func_a_src[:800])

# ============================================================
# 3. 分析解码器中的 XOR 旋转逻辑
# ============================================================
# 从源码中提取 XOR key 和旋转参数
# PDD 的解码器通常包含:
# 1. base64 解码
# 2. 字符串数组重排 (shuffle)
# 3. XOR 旋转

# 找到数组重排函数 (通常在解码器定义之前)
# 模式: function push(arr, num) { while(true) { try { ... } catch(e) { arr.push(arr.shift()) } } }
shuffle_match = re.search(r'function\s+\w+\s*\(\s*\w+\s*,\s*\w+\s*\)\s*\{[^}]*while\s*\(\s*!\s*0\s*\)', src[:10000])
if shuffle_match:
    shuffle_area = src[shuffle_match.start():shuffle_match.start()+1000]
    print(f"\n=== 数组重排函数 ===")
    print(shuffle_area[:500])

# ============================================================
# 4. 用 Node.js 直接执行解码器获取完整查找表
# ============================================================
# 生成一个 Node.js 脚本来解码所有条目
node_script = """
const fs = require('fs');
const src = fs.readFileSync('risk-control-anti.js', 'utf-8');

// 提取并执行 module 32455
const modStart = src.indexOf('32455:function(t)');
const modSrc = src.substring(modStart);

// 找到模块的结尾
self.__LOADABLE_LOADED_CHUNKS__ = [];
const origPush = Array.prototype.push;
let capturedModules = null;
self.__LOADABLE_LOADED_CHUNKS__.push = function(chunk) {
    if (chunk && chunk[1]) capturedModules = chunk[1];
    return origPush.call(this, chunk);
};

try { eval(src); } catch(e) {}
self.__LOADABLE_LOADED_CHUNKS__.push = origPush;

if (!capturedModules) {
    console.log(JSON.stringify({error: 'No modules captured'}));
    process.exit(1);
}

// 执行 module 32455 来获取内部模块
const mockModule = {exports: {}};
capturedModules[32455].call(mockModule.exports, mockModule);

// 现在需要访问解码器函数 - 它们在内部模块的作用域中
// 方法: 修改 module 4 (入口模块) 的源码来暴露解码器

// 尝试通过 AntiContent class 来间接访问
const AntiContent = mockModule.exports.default || mockModule.exports;

// 生成多个 token 来验证
const results = [];
for (let i = 0; i < 3; i++) {
    const st = Date.now() + i * 1000;
    const instance = new AntiContent({serverTime: st});
    try {
        const token = instance.messagePack();
        if (token && typeof token.then === 'function') {
            // It's a promise
            token.then(t => {
                results.push({serverTime: st, token: t, tokenLen: t ? t.length : 0});
            });
        } else {
            results.push({serverTime: st, token: token, tokenLen: token ? String(token).length : 0});
        }
    } catch(e) {
        results.push({serverTime: st, error: e.message});
    }
}

// 等待 promises
setTimeout(() => {
    console.log(JSON.stringify(results, null, 2));
}, 2000);
"""

with open("gen_tokens.js", "w", encoding="utf-8") as f:
    f.write(node_script)

print("\n已生成 gen_tokens.js, 请用 node gen_tokens.js 执行")

# ============================================================
# 5. 分析 case 块 - 还原算法步骤
# ============================================================
print("\n=== 分析 case 块 (算法步骤) ===")

case_blocks = re.findall(r'case\s*"(\d+)"\s*:\s*([^;]+?);\s*continue', src)
print(f"共 {len(case_blocks)} 个 case 块:")

for num, code in sorted(case_blocks, key=lambda x: int(x[0])):
    # 清理代码
    clean = code.strip()
    # 提取关键操作
    ops = []
    if '_ê' in clean: ops.append('DATA_ACCESS')
    if '_á' in clean: ops.append('PUSH')
    if 'isNaN' in clean: ops.append('CHECK_NAN')
    if 'void 0' in clean: ops.append('CHECK_UNDEF')
    if '63' in clean: ops.append('MASK_63')
    if '64' in clean: ops.append('THRESHOLD_64')
    if '>>>' in clean or '<<' in clean or '>>' in clean: ops.append('BITSHIFT')
    
    print(f"  case {num:>2}: [{', '.join(ops)}] {clean[:100]}")

# ============================================================
# 6. 识别 MessagePack 编码结构
# ============================================================
print("\n=== MessagePack 结构分析 ===")

# 从之前的浏览器分析中我们知道:
# 1. 构造函数创建了一个 MessagePack 编码器
# 2. messagePack() 将数据编码为 MessagePack 格式
# 3. 然后进行自定义 base64 + XOR 编码
# 4. 输出是 URL-safe 字符串

# MessagePack 格式参考:
# 0x80-0x8f: fixmap (0-15 entries)
# 0x90-0x9f: fixarray (0-15 elements)
# 0xa0-0xbf: fixstr (0-31 bytes)
# 0xc0: nil
# 0xc2: false, 0xc3: true
# 0xc4: bin8, 0xc5: bin16, 0xc6: bin32
# 0xca: float32, 0xcb: float64
# 0xcc: uint8, 0xcd: uint16, 0xce: uint32, 0xcf: uint64
# 0xd0: int8, 0xd1: int16, 0xd2: int32, 0xd3: int64
# 0xd9: str8, 0xda: str16, 0xdb: str32

print("Token 结构 (从浏览器捕获):")
print("  - 前缀: '0as' (固定)")
print("  - 编码: URL-safe base64")
print("  - 长度: ~570 字符")
print("  - 内部: MessagePack 编码的二进制数据")

# ============================================================
# 7. 生成 Python 版 Token 生成器框架
# ============================================================
print("\n=== Python Token 生成器框架 ===")

python_framework = '''
import struct
import time
import random
import base64
import hashlib

class PDDAntiContent:
    """PDD anti_content Token 生成器"""
    
    def __init__(self, server_time: int):
        self.server_time = server_time
        self.counter = 0
        self.data = bytearray()
    
    def _msgpack_encode(self, obj):
        """MessagePack 编码"""
        if isinstance(obj, dict):
            result = bytes([0x80 | len(obj)])
            for k, v in obj.items():
                result += self._msgpack_encode(k)
                result += self._msgpack_encode(v)
            return result
        elif isinstance(obj, str):
            encoded = obj.encode('utf-8')
            if len(encoded) < 32:
                return bytes([0xa0 | len(encoded)]) + encoded
            elif len(encoded) < 256:
                return bytes([0xd9, len(encoded)]) + encoded
            else:
                return bytes([0xda]) + struct.pack('>H', len(encoded)) + encoded
        elif isinstance(obj, int):
            if 0 <= obj < 128:
                return bytes([obj])
            elif obj < 256:
                return bytes([0xcc, obj])
            elif obj < 65536:
                return bytes([0xcd]) + struct.pack('>H', obj)
            else:
                return bytes([0xce]) + struct.pack('>I', obj)
        elif isinstance(obj, bytes):
            if len(obj) < 256:
                return bytes([0xc4, len(obj)]) + obj
            else:
                return bytes([0xc5]) + struct.pack('>H', len(obj)) + obj
        elif obj is None:
            return bytes([0xc0])
        elif isinstance(obj, bool):
            return bytes([0xc3 if obj else 0xc2])
        return bytes([0xc0])
    
    def _custom_b64_encode(self, data: bytes) -> str:
        """PDD 自定义 base64 编码"""
        chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/="
        result = []
        i = 0
        while i < len(data):
            b0 = data[i] if i < len(data) else 0
            b1 = data[i+1] if i+1 < len(data) else 0
            b2 = data[i+2] if i+2 < len(data) else 0
            
            n = (b0 << 16) | (b1 << 8) | b2
            
            result.append(chars[(n >> 18) & 0x3F])
            result.append(chars[(n >> 12) & 0x3F])
            result.append(chars[(n >> 6) & 0x3F] if i+1 < len(data) else '=')
            result.append(chars[n & 0x3F] if i+2 < len(data) else '=')
            
            i += 3
        
        return ''.join(result).rstrip('=')
    
    def generate(self) -> str:
        """生成 anti_content token"""
        # 构建 payload
        self.counter += 1
        
        payload = {
            "serverTime": self.server_time,
            "count": self.counter,
            # TODO: 添加更多字段 (从逆向分析中获取)
        }
        
        # MessagePack 编码
        packed = self._msgpack_encode(payload)
        
        # 自定义 base64 编码
        token = self._custom_b64_encode(packed)
        
        return token
'''

print(python_framework)

# 保存框架代码
with open("pdd_anti_content.py", "w", encoding="utf-8") as f:
    f.write(python_framework)

print("\n已保存框架到 pdd_anti_content.py")
print("\n=== 待解决 ===")
print("1. 需要确定 payload 的完整字段结构")
print("2. 需要确认 XOR 旋转的 key 和算法")
print("3. 需要确认前缀 '0as' 的生成规则")
print("4. 建议: 用 node gen_tokens.js 生成对照 token 来验证")
"""
PDD risk-control-anti.js 反混淆脚本 v2
"""
import re
import base64
import json
import sys

# 强制 UTF-8 输出
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

with open("risk-control-anti.js", "r", encoding="utf-8") as f:
    src = f.read()

print(f"源码长度: {len(src)} 字符\n")

# ============================================================
# 1. 提取字符串数组
# ============================================================
def extract_array(src, func_name):
    pattern = rf'function\s+{re.escape(func_name)}\s*\(\)\s*\{{\s*var\s+\w+\s*=\s*\['
    match = re.search(pattern, src)
    if not match:
        return []
    start = match.end() - 1
    depth = 0
    for i in range(start, min(start + 50000, len(src))):
        if src[i] == '[': depth += 1
        elif src[i] == ']':
            depth -= 1
            if depth == 0:
                items = re.findall(r'"((?:[^"\\]|\\.)*)"', src[start:i+1])
                return items
    return []

arr_c  = extract_array(src, "c")
arr_et = extract_array(src, "et")
arr_h  = extract_array(src, "h")
print(f"数组 c:  {len(arr_c)} 条目")
print(f"数组 et: {len(arr_et)} 条目")
print(f"数组 h:  {len(arr_h)} 条目")

# ============================================================
# 2. PDD 自定义 Base64 解码 + XOR 旋转
# ============================================================
B64_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/="

def pdd_b64decode(s):
    """PDD 自定义 base64 解码 (用自定义字符表)"""
    out = []
    n = 0
    r = 0
    for ch in s:
        v = B64_CHARS.find(ch)
        if v < 0: continue
        if v == 64: break  # padding
        if n % 4 == 0:
            r = v
        else:
            r = r * 64 + v
        n += 1
        if n % 4 == 0:
            out.append((r >> 16) & 0xFF)
            out.append((r >> 8) & 0xFF)
            out.append(r & 0xFF)
            r = 0
    # 处理剩余
    if n % 4 == 2:
        out.append((r >> 4) & 0xFF)
    elif n % 4 == 3:
        out.append((r >> 10) & 0xFF)
        out.append((r >> 2) & 0xFF)
    return bytes(out)

def xor_rotate(data, key_str):
    """XOR 旋转解码"""
    if not data:
        return ""
    key = key_str
    result = []
    for i, b in enumerate(data):
        k = ord(key[i % len(key)]) if key else 0
        result.append(b ^ k)
    return bytes(result)

def decode_entry(arr, index, offset):
    """解码: arr[index - offset] -> pdd_b64decode -> xor_rotate"""
    idx = index - offset
    if idx < 0 or idx >= len(arr):
        return f"<OOR:{idx}>"
    encoded = arr[idx]
    try:
        decoded_bytes = pdd_b64decode(encoded)
        # 尝试 XOR 旋转 (key 通常是固定字符串)
        # PDD 的 XOR key 通常是 "pdd" 或类似的短字符串
        # 先不做 XOR，直接看原始解码结果
        text = decoded_bytes.decode("latin-1")
        # 过滤不可打印字符
        clean = ''.join(c if 32 <= ord(c) < 127 else f'\\x{ord(c):02x}' for c in text)
        return clean
    except Exception as e:
        return f"<ERR:{e}>"

# ============================================================
# 3. 提取偏移量
# ============================================================
offsets = {}
for name in ['a', 'M', 's']:
    m = re.search(rf'function\s+{name}\s*\([^)]*\)\s*\{{[^}}]*?n\s*-=\s*(\d+)', src[:15000])
    if m:
        offsets[name] = int(m.group(1))
    else:
        offsets[name] = {'a': 366, 'M': 161, 's': 496}.get(name, 0)
print(f"\n偏移量: a={offsets['a']}, M={offsets['M']}, s={offsets['s']}")

# ============================================================
# 4. 解码测试 - 直接解码数组条目
# ============================================================
print("\n=== 数组 c 解码样本 (offset=366) ===")
for i in range(min(30, len(arr_c))):
    idx = i + offsets['a']  # 对应的调用参数
    decoded = decode_entry(arr_c, idx, offsets['a'])
    if decoded and not decoded.startswith('<'):
        print(f"  c[{i}] (idx={idx}): \"{decoded}\"")

print("\n=== 数组 et 解码样本 (offset=161) ===")
for i in range(min(30, len(arr_et))):
    idx = i + offsets['M']
    decoded = decode_entry(arr_et, idx, offsets['M'])
    if decoded and not decoded.startswith('<'):
        print(f"  et[{i}] (idx={idx}): \"{decoded}\"")

print("\n=== 数组 h 解码样本 (offset=496) ===")
for i in range(min(30, len(arr_h))):
    idx = i + offsets['s']
    decoded = decode_entry(arr_h, idx, offsets['s'])
    if decoded and not decoded.startswith('<'):
        print(f"  h[{i}] (idx={idx}): \"{decoded}\"")

# ============================================================
# 5. 提取构造函数并解码所有调用
# ============================================================
print("\n=== 提取构造函数 ===")

# 找到包含 switch(o[i++]) 的构造函数
# 搜索 AntiContent class 的 constructor
ctor_patterns = [
    r'for\s*\(\s*;\s*;\s*\)\s*\{\s*switch\s*\(\s*\w+\s*\[\s*\w+\+\+\s*\]\s*\)',
    r'case\s*"[0-9]"\s*:',
]

# 找所有 case 块
case_blocks = re.findall(r'case\s*"(\d+)"\s*:\s*([^;]+?);\s*continue', src)
print(f"找到 {len(case_blocks)} 个 case 块")
for case_num, code in case_blocks[:10]:
    print(f"  case \"{case_num}\": {code[:80]}")

# ============================================================
# 6. 找到并解码 messagePack 中的调用
# ============================================================
print("\n=== 提取 messagePack/messagePackSync ===")

# 搜索 messagePack 相关代码
mp_area = src[src.find('messagePack'):src.find('messagePack')+2000] if 'messagePack' in src else ''
print(f"messagePack 区域: {len(mp_area)} 字符")
if mp_area:
    print(mp_area[:300])

# ============================================================
# 7. 尝试用标准 base64 解码所有数组条目
# ============================================================
print("\n=== 标准 base64 解码数组 c 样本 ===")
for i in range(min(20, len(arr_c))):
    encoded = arr_c[i]
    try:
        # 补齐 padding
        padded = encoded + "=" * (4 - len(encoded) % 4) if len(encoded) % 4 else encoded
        decoded = base64.b64decode(padded)
        text = decoded.decode("utf-8", errors="replace")
        clean = ''.join(c if 32 <= ord(c) < 127 else '.' for c in text)
        print(f"  c[{i}] = \"{encoded}\" -> \"{clean}\"")
    except:
        print(f"  c[{i}] = \"{encoded}\" -> <FAIL>")

print("\n=== 标准 base64 解码数组 h 样本 ===")
for i in range(min(20, len(arr_h))):
    encoded = arr_h[i]
    try:
        padded = encoded + "=" * (4 - len(encoded) % 4) if len(encoded) % 4 else encoded
        decoded = base64.b64decode(padded)
        text = decoded.decode("utf-8", errors="replace")
        clean = ''.join(c if 32 <= ord(c) < 127 else '.' for c in text)
        print(f"  h[{i}] = \"{encoded}\" -> \"{clean}\"")
    except:
        print(f"  h[{i}] = \"{encoded}\" -> <FAIL>")
