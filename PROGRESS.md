# PDD 签到券自动抢券 Bot - 开发进度记录
> 本文件供 AI 助手或开发者阅读，快速了解项目当前状态、已完成功能和待办事项。
> 最后更新: 2026-06-21 (v2)

---

## 一、项目概述

**项目名称**: 拼多多签到券自动抢券 Bot  
**功能目标**: 自动化完成拼多多签到、并在签到满5天后自动抢30元话费券  
**部署环境**: Railway (云端) + 本地均可运行  
**技术栈**: Python (Flask + APScheduler + requests) + Node.js (anti_content Token 生成)

---

## 二、项目文件结构

```
pdd/
├── main.py              # 核心逻辑: 时间同步、签到、抢券、调度器 (1271行) ✅完成
├── dashboard.py         # Web管理面板: Flask + 前端HTML/JS (1752行) ✅完成
├── auth.py              # Flask登录鉴权: 账号密码登录+session (306行) ✅完成
├── login.py             # PDD Token抓包助手: 引导用户获取Token (509行) ✅完成
├── pdd_token.py         # Node.js Token进程池: 复用Node进程生成Token (155行) ✅完成
├── pdd_token_gen.js     # Node.js Token生成脚本: RC4加密 (128行) ✅完成
├── pdd_anti_content.py  # 纯Python Token实现: 逆向分析版 ⚠️未完成(已弃用，改用Node.js方案)
├── risk-control-anti.js # PDD原始风控JS: RC4加密模块 ✅已提取
├── check.py             # 快速状态检查脚本 (24行) ✅完成
├── deobfuscate.py       # JS反混淆工具 ⚠️辅助工具，非核心
├── requirements.txt     # Python依赖: ntplib, APScheduler, flask, requests ✅
├── Procfile             # Railway部署配置 ✅
├── railway.toml         # Railway部署配置 ✅
├── nixpacks.toml        # Railway构建配置(Nixpacks) ✅
├── .pdd_accounts.json   # 账号数据存储文件 (当前3个账号)
├── .credentials.json    # Web面板登录凭证
└── .pdd_token           # 旧版单账号Token文件(已迁移到多账号格式)
```

---

## 三、已完成功能清单

### 3.1 核心功能 ✅
- [x] PDD 服务器时间同步 (优先PDD接口，备用NTP)
- [x] 持续后台同步 (每30秒采样，加权移动平均平滑)
- [x] 多账号管理 (添加/编辑/删除/启用/禁用)
- [x] 每个账号独立抢券配置 (时间/线程数)
- [x] 签到API集成 (查询状态 + 执行签到)
- [x] 抢券API集成 (多账号并发，多线程持续发送)
- [x] anti_content Token生成 (Node.js进程池方案，~5ms/token)
- [x] APScheduler定时调度 (抢券 + 自动签到 + 状态查询)
- [x] 抢券前强制刷新签到状态 (禁止用缓存数据判断资格)

### 3.2 Web管理面板 ✅
- [x] 登录鉴权 (Flask session + 密码哈希)
- [x] 概览页 (状态卡片、倒计时、配置摘要、实时日志)
- [x] 运行日志页 (全量日志、跟随/暂停滚动)
- [x] 抢券历史页
- [x] 配置页 (全局默认配置、环境变量说明)
- [x] 账号页 (卡片式列表、签到进度条、状态标签)
- [x] 账号操作 (查询签到/手动签到/编辑/删除/测试Cookie/启用禁用)
- [x] 自动签到开关 (每个账号可独立开启)
- [x] 安全设置 (修改用户名/密码、退出登录) — 已迁移到账号页
- [x] 自动查询间隔配置 (30分钟/1小时/2小时/3小时/6小时/12小时)
- [x] 立即测试抢券按钮 (跳过时间等待)
- [x] 清除日志/历史

### 3.3 部署与运维 ✅
- [x] Railway部署配置 (Procfile + railway.toml + nixpacks.toml)
- [x] 保活心跳 (每180秒ping /health，防止容器休眠)
- [x] 旧Token文件自动迁移到多账号格式

---

## 四、当前账号状态 (截至 2026-06-21)

| 账号 | 标签 | 签到天数 | 领奖次数 | 状态码 | 可签到 | 可抢券 |
|------|------|---------|---------|--------|--------|--------|
| acc_1782012914 | 默认账号 | 5/5 | 5 | 40(已领取) | 否 | 否 |
| acc_1782038388491 | 账号2 | 5/5 | 5 | 31 | 否 | 否 |
| acc_1782046395163 | 账号3 | 0/5 | 5 | 10 | 是 | 否 |

**状态码说明**:
- `10`: 签到进行中 (需继续签到)
- `31`: 签到完成但状态异常 (需观察)
- `40`: 已领取奖励 (需重新开始5天签到周期)

---

## 五、已知问题 & 待完成事项

### 5.1 待修复问题 🔴
- [x] ~~**签到失败6070001**~~: 已修复! task_id是动态的，现已移除硬编码task_id
- [ ] **账号2状态异常**: display_status=31，签到满5天但不可抢券，需排查PDD接口返回值
- [ ] **账号3签到进度**: finish_count=0但gain_award_count=5，数据可能不一致

### 5.2 可优化项 🟡
- [ ] **pdd_anti_content.py**: 纯Python逆向实现未完成(有TODO标记)，当前已弃用改用Node.js方案，可删除或保留备用
- [ ] **Token过期检测**: 目前无主动检测Token是否过期的机制，Token过期后抢券会静默失败
- [ ] **Railway自动重部署**: 代码修改后需手动触发重新部署
- [ ] **签到失败重试**: 签到失败后没有自动重试机制

### 5.3 功能扩展建议 🟢
- [ ] 抢券结果通知 (微信/邮件/Telegram)
- [ ] Token自动刷新 (如果PDD有刷新接口)
- [ ] 多时段抢券 (不仅限于0点，支持设置多个时间点)
- [ ] 抢券成功率统计 (按天/按周统计)

---

## 六、关键配置

### 环境变量
```
GRAB_HOUR=0          # 抢券目标时间 - 小时
GRAB_MINUTE=0        # 抢券目标时间 - 分钟
GRAB_SECOND=0        # 抢券目标时间 - 秒
PRE_START_SEC=10     # 提前开火秒数
END_HOUR=0           # 结束时间 - 小时
END_MINUTE=0         # 结束时间 - 分钟
END_SECOND=30        # 结束时间 - 秒
THREAD_COUNT=5       # 并发线程数
PORT=8080            # Web面板端口
RUN_TEST=false       # 测试模式(true=跳过等待直接抢券)
SKIP_WAIT=false      # 跳过时间等待(内部使用)
```

### Web面板登录
- 默认用户名: `admin`
- 默认密码: `pdd2026`
- 凭证文件: `.credentials.json`

### 调度任务
- 抢券: 每天按各账号配置的最早时间触发 (目标时间 - 提前秒数)
- 自动签到: 每天随机时间 (8:00-19:59) 执行
- 状态查询: 每2小时自动查询 (可在面板调整)

---

## 七、运行方式

### 本地运行
```bash
pip install -r requirements.txt
python main.py
# 访问 http://localhost:8080
```

### Railway部署
- 已配置 Procfile: `web: python main.py`
- 自动使用 $PORT 环境变量
- 需要 Node.js 运行时 (nixpacks.toml 已配置)

---

## 八、开发决策记录

| 决策 | 选择 | 原因 |
|------|------|------|
| Token生成方案 | Node.js进程池(Scheme B) | 纯Python逆向(Scheme C)有1字节不可复现，放弃 |
| task_id策略 | 不传task_id，服务端自动识别 | task_id每个周期变化，硬编码会失效导致6070001 |
| 时间同步 | PDD服务器时间优先 | 比NTP更精确，偏移约-741ms |
| 抢券方式 | 直接HTTP API | 比Playwright浏览器快(50ms vs 5-10s) |
| 多账号策略 | 各账号独立线程并发 | 互不干扰，任一成功即停止 |
| 安全设置位置 | 账号页(非配置页) | 统一管理入口 |

---

> **阅读提示**: AI助手接手项目时，先读此文件了解全局，再按需查看具体源码文件。
