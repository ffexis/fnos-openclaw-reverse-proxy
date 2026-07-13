# OpenClaw Reverse Proxy / OpenClaw 反向代理

[English](#english) | [中文](#中文)

---

## English

A reverse proxy for OpenClaw gateway, exposing the Chat Completions API to your local network with access control and session management.

### Why?

OpenClaw's gateway is locked to loopback mode on certain NAS systems (e.g., FeiNiuOS), making it inaccessible from other devices. This proxy solves that by:

- Exposing the API on a configurable port with Bearer token authentication
- Auto-forwarding the upstream auth token (read from `openclaw.json`)
- Managing session keys per token to isolate conversations
- Providing a Web UI for token management

### Features

- **Token-based access control** - Create/delete tokens via Web UI or API
- **Session isolation** - Each token maintains a session key; new conversations (`messages.length == 1`) auto-rotate to prevent context pollution
- **Config polling** - Auto-detects changes to `openclaw.json` (port/token)
- **SSE streaming** - Full support for streaming chat completions
- **Web UI** - Dark-themed management interface

### Quick Start

#### Docker (Recommended)

```bash
git clone https://github.com/YOUR_USERNAME/fnos-openclaw-reverse-proxy.git
cd fnos-openclaw-reverse-proxy

# Edit docker-compose.yml to mount your openclaw.json
docker compose up -d
```

#### Manual

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 41000
```

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENCLAW_CONFIG` | `/config/openclaw.json` | Path to OpenClaw config file |
| `TOKENS_PATH` | `/data/tokens.json` | Path to token storage |
| `CONFIG_POLL_INTERVAL` | `30` | Config file poll interval (seconds) |

### Usage

#### Web UI

Open `http://<your-host>:41000/` in browser. Enter your proxy token when prompted.

#### API

```bash
# Chat completions
curl http://<host>:41000/v1/chat/completions \
  -H "Authorization: Bearer <your-token>" \
  -H "Content-Type: application/json" \
  -d '{"model":"openclaw/main","messages":[{"role":"user","content":"Hello"}]}'

# Streaming
curl http://<host>:41000/v1/chat/completions \
  -H "Authorization: Bearer <your-token>" \
  -H "Content-Type: application/json" \
  -d '{"model":"openclaw/main","messages":[{"role":"user","content":"Hello"}],"stream":true}'
```

#### Token Management API

```bash
# List tokens
curl http://<host>:41000/api/tokens -H "Authorization: Bearer <admin-token>"

# Create token
curl -X POST http://<host>:41000/api/tokens \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{"name":"my-device"}'

# Delete token
curl -X DELETE http://<host>:41000/api/tokens/<name> \
  -H "Authorization: Bearer <admin-token>"
```

### Architecture

```
Client (with proxy token)
    ↓ Authorization: Bearer <proxy_token>
Reverse Proxy (port 41000)
    ↓ Validates proxy token
    ↓ Reads upstream token from openclaw.json
    ↓ Adds x-openclaw-session-key header
    ↓ Authorization: Bearer <openclaw_token>
OpenClaw Gateway (127.0.0.1:11149)
```

### Docker Compose

```yaml
services:
  openclaw-proxy:
    build: .
    container_name: openclaw-proxy
    restart: unless-stopped
    network_mode: host  # Required to reach OpenClaw on localhost
    volumes:
      - /path/to/openclaw.json:/config/openclaw.json:ro
      - openclaw-proxy-data:/data
    environment:
      - TZ=Asia/Shanghai

volumes:
  openclaw-proxy-data:
```

---

## 中文

OpenClaw 网关的反向代理，将 Chat Completions API 暴露到局域网，支持访问控制和会话管理。

### 为什么需要这个？

OpenClaw 的网关在某些 NAS 系统（如飞牛OS）上被锁定为 loopback 模式，无法从其他设备访问。本代理通过以下方式解决：

- 在可配置端口上暴露 API，使用 Bearer Token 认证
- 自动转发上游认证 Token（从 `openclaw.json` 读取）
- 为每个访问 Token 管理会话密钥，隔离不同对话
- 提供 Web UI 管理界面

### 功能特性

- **基于 Token 的访问控制** - 通过 Web UI 或 API 创建/删除 Token
- **会话隔离** - 每个 Token 维护一个会话密钥；新对话（`messages.length == 1`）自动轮换，防止上下文污染
- **配置轮询** - 自动检测 `openclaw.json` 变更（端口/Token）
- **SSE 流式传输** - 完整支持流式 Chat Completions
- **Web UI** - 深色主题管理界面

### 快速开始

#### Docker（推荐）

```bash
git clone https://github.com/YOUR_USERNAME/fnos-openclaw-reverse-proxy.git
cd fnos-openclaw-reverse-proxy

# 编辑 docker-compose.yml，挂载你的 openclaw.json
docker compose up -d
```

#### 手动部署

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 41000
```

### 配置项

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OPENCLAW_CONFIG` | `/config/openclaw.json` | OpenClaw 配置文件路径 |
| `TOKENS_PATH` | `/data/tokens.json` | Token 存储路径 |
| `CONFIG_POLL_INTERVAL` | `30` | 配置文件轮询间隔（秒） |

### 使用方法

#### Web UI

浏览器打开 `http://<你的主机>:41000/`，按提示输入代理 Token。

#### API 调用

```bash
# Chat completions
curl http://<主机>:41000/v1/chat/completions \
  -H "Authorization: Bearer <你的token>" \
  -H "Content-Type: application/json" \
  -d '{"model":"openclaw/main","messages":[{"role":"user","content":"你好"}]}'

# 流式输出
curl http://<主机>:41000/v1/chat/completions \
  -H "Authorization: Bearer <你的token>" \
  -H "Content-Type: application/json" \
  -d '{"model":"openclaw/main","messages":[{"role":"user","content":"你好"}],"stream":true}'
```

#### Token 管理 API

```bash
# 列出所有 Token
curl http://<主机>:41000/api/tokens -H "Authorization: Bearer <管理员token>"

# 创建 Token
curl -X POST http://<主机>:41000/api/tokens \
  -H "Authorization: Bearer <管理员token>" \
  -H "Content-Type: application/json" \
  -d '{"name":"my-device"}'

# 删除 Token
curl -X DELETE http://<主机>:41000/api/tokens/<name> \
  -H "Authorization: Bearer <管理员token>"
```

### 架构

```
客户端（携带代理 Token）
    ↓ Authorization: Bearer <proxy_token>
反向代理（端口 41000）
    ↓ 验证代理 Token
    ↓ 从 openclaw.json 读取上游 Token
    ↓ 添加 x-openclaw-session-key 请求头
    ↓ Authorization: Bearer <openclaw_token>
OpenClaw 网关（127.0.0.1:11149）
```

### Docker Compose 配置

```yaml
services:
  openclaw-proxy:
    build: .
    container_name: openclaw-proxy
    restart: unless-stopped
    network_mode: host  # 必须使用 host 网络以访问本机的 OpenClaw
    volumes:
      - /path/to/openclaw.json:/config/openclaw.json:ro
      - openclaw-proxy-data:/data
    environment:
      - TZ=Asia/Shanghai

volumes:
  openclaw-proxy-data:
```

---

## Contributors / 贡献者

- **Google Gemini** - 架构规划协力 / Architecture planning
- **MiMo Code** - 代码实现、部署与调试 / Implementation, deployment & debugging

---

## License / 许可证

MIT
