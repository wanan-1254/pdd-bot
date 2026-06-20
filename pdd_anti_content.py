
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
