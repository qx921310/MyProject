# 反爬机制与应对速查（2026-08 学习笔记）

> 背景：本 VPS 访问东方财富接口遇到 `RemoteDisconnected`（AKShare issue #7099/#6986/#6100 均确认），
> 学习反爬知识以便以后遇到类似问题能快速定位和绕过。
> 来源：Bright Data《2026 最受欢迎的反爬虫技术》、阿里云开发者社区《Python 反爬策略突破与逆向技巧》。

## 一、7 大反爬技术（按常见程度）

### 1. IP 地址黑名单 / 限流（最普遍，我们遇到的东财就是这个）
- 单个 IP 短时间请求过多 → 封禁/断开（`RemoteDisconnected` = 服务器主动断开）
- 数据中心 IP（VPS/AWS/阿里云等）信誉低，比住宅 IP 更容易被封
- **应对**：
  - 降低请求频率（加 sleep、错峰）
  - 轮换代理（住宅代理 > 数据中心代理）
  - 多数据源备用（我们这次就是：新浪主用 + 腾讯备用）

### 2. User-Agent 及 HTTP 头过滤
- 默认 UA（如 python-requests）一眼识别
- 缺 Referer / Accept-Language / Accept-Encoding / Connection 头也会被怀疑
- **应对**：完整浏览器指纹头 + UA 轮换（TradingView 就是这么过的，见 market_snapshot.py）

### 3. JavaScript 挑战（动态内容）
- 数据靠 JS 渲染，直接请求拿不到
- **应对**：无头浏览器（Playwright/Selenium）执行 JS

### 4. CAPTCHA 验证码
- Cloudflare/Akamai 集成
- **应对**：打码服务（2captcha 等）或人工

### 5. 蜜罐陷阱（Honeypot）
- 隐藏链接/表单（`display:none`），低级爬虫碰到就被标记
- **应对**：跳过不可见元素

### 6. 行为分析
- 请求间隔固定、访问路径规律、无鼠标轨迹 → 判定机器人
- **应对**：随机化间隔、模拟人类行为

### 7. 浏览器指纹识别
- 屏幕分辨率、字体、时区、扩展等组合成唯一指纹
- **应对**：随机化特征、频繁换 IP

## 二、Python requests 基本对抗手段（代码模板）

```python
# 1. UA 伪装（最基础）
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36',
    'Referer': 'https://example.com/',
    'Accept-Language': 'en-US,en;q=0.9',
}

# 2. 代理绕过 IP 限制
proxies = {'http': 'http://127.0.0.1:8080', 'https': 'http://127.0.0.1:8080'}
resp = requests.get(url, headers=headers, proxies=proxies, timeout=15)

# 3. 频率控制
import time, random
time.sleep(random.uniform(1, 3))  # 随机间隔，避免固定节奏

# 4. 会话复用 + 完整头
import requests
s = requests.Session()
s.headers.update(headers)  # Session 自动带 Cookie
```

## 三、本次实战经验（本机 2026-08-11）

| 数据源 | 现象 | 原因 | 对策 |
|--------|------|------|------|
| 东方财富（em 接口） | RemoteDisconnected 时好时坏 | 数据中心 IP 被反爬/限流 | 弃用，换新浪源 |
| 新浪（sina） | 稳定，4-5s | 无强反爬 | AKShare 新浪源主用 |
| 腾讯 qt.gtimg.cn | 稳定 | 无强反爬 | 备用 |
| TradingView scanner | 需完整浏览器头否则 404 | UA/Referer/Origin 校验 | 全套头已固化 |
| Yahoo chart API | 需 UA 否则报错 | UA 校验 | UA 头已固化 |

## 四、关键结论

1. **数据源多备几个永远是对的** —— 单点故障 + 反爬封禁双保险
2. **数据中心 IP 是原罪** —— VPS 上跑爬虫天然易被大厂反爬盯上；住宅代理是终极方案但贵
3. **先查社区 issue 再自己猜** —— 我们遇到的问题 AKShare issue 早有人报过（#7099 同款错误）
4. **合法合规**：只抓公开数据、控制频率、尊重 robots，学术/个人用途
