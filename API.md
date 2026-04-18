# Shrimp Eye API (Simple)

这份文档描述本项目后端坐标接口与前端调试字段，方便你和其他前端联调。

## Base URL

- 默认：`http://127.0.0.1:3000`
- 坐标接口：`GET /coordinate`
- 健康检查：`GET /health`
- 校准状态：`GET /calibration`
- 提交校准：`POST /calibration`
- 重置校准：`POST /calibration/reset`

---

## 1) 健康检查

### Request

`GET /health`

### Response

```json
{"ok": true}
```

---

## 2) 坐标接口（基础 + 调试全量）

### Request

`GET /coordinate`

可选 query：

- `format=object|nested|array|text|debug`
- `debug=1` 或 `verbose=1`（任意基础格式下返回全量调试结构）

### 默认响应（object）

```json
{"x": 320, "y": 240}
```

### 基础兼容响应格式（返回后端映射后的坐标）

1. `object`

```json
{"x": 320, "y": 240}
```

2. `nested`

```json
{"coordinate": {"x": 320, "y": 240}}
```

3. `array`

```json
[320, 240]
```

4. `text`

```text
320,240
```

> 说明：当前后端输出的是像素坐标（基于 `EYE_COORD_WIDTH` / `EYE_COORD_HEIGHT` 映射）。
> 当后端校准启用时，这里返回的是 **mapped 坐标**（不是原始 raw 坐标）。

### 全量调试响应（推荐联调用）

请求示例：

`GET /coordinate?format=debug`

或：

`GET /coordinate?format=object&debug=1`

响应示例（节选）：

```json
{
  "ok": true,
  "coordinate": {"x": 962, "y": 541},
  "coordinate_norm": {"x": 0.501, "y": 0.501},
  "coordinate_raw": {"x": 901, "y": 520},
  "coordinate_mapped": {"x": 962, "y": 541},
  "confidence": 1.0,
  "tracking": {
    "backend": "tasks",
    "sequence": 1267,
    "last_update_ms": 1776500000000,
    "age_ms": 24
  },
  "calibration": {
    "enabled": true,
    "applied": true,
    "updated_at_ms": 1776500000000,
    "sample_count": 5,
    "affine": {"ax": 1.02, "bx": 0.01, "cx": -22.3, "ay": -0.02, "by": 0.98, "cy": 17.8}
  },
  "server": {
    "host": "127.0.0.1",
    "port": 3000,
    "endpoint": "/coordinate",
    "coord_width": 1920,
    "coord_height": 1080,
    "flip_x": true,
    "default_format": "object"
  },
  "request": {
    "path": "/coordinate",
    "selected_format": "debug",
    "query": {"format": ["debug"]},
    "server_time_ms": 1776500000025
  },
  "compat": {
    "object": {"x": 962, "y": 541},
    "nested": {"coordinate": {"x": 962, "y": 541}},
    "array": [962, 541],
    "text": "962,541"
  }
}
```

这意味着前后端沟通里的关键数据（raw/mapped 坐标、置信度、校准状态、请求回显、格式兼容结果）都能直接通过 API 拿到。

---

## 3) 校准 API（后端映射）

### 获取当前校准状态

`GET /calibration`

```json
{
  "ok": true,
  "calibration": {
    "enabled": true,
    "updated_at_ms": 1776500000000,
    "sample_count": 5,
    "affine": {"ax": 1.02, "bx": 0.01, "cx": -22.3, "ay": -0.02, "by": 0.98, "cy": 17.8}
  }
}
```

### 提交样本由后端拟合

`POST /calibration`

```json
{
  "samples": [
    {"raw": {"x": 450, "y": 260}, "target": {"x": 290, "y": 160}},
    {"raw": {"x": 1490, "y": 260}, "target": {"x": 1630, "y": 160}},
    {"raw": {"x": 960, "y": 540}, "target": {"x": 960, "y": 540}}
  ]
}
```

后端会计算仿射矩阵并在后续 `/coordinate` 中应用映射。

### 直接提交仿射矩阵

`POST /calibration`

```json
{
  "affine": {"ax": 1, "bx": 0, "cx": 0, "ay": 0, "by": 1, "cy": 0},
  "sample_count": 0
}
```

### 重置校准

`POST /calibration/reset`

---

## 4) 调试面板字段（前端插件显示）

页面右下角 debug panel 中主要字段：

- `source`
  - `api`: 来自坐标接口
  - `calibration`: 校准流程采样中
  - `request-error`: 请求失败回退中
  - `config`: 配置缺失（如 URL 未设置）
- `basis`
  - 当前坐标基准解释模式：`auto|viewport|document`
- `pollMs`
  - 前端轮询间隔（毫秒）
- `latencyMs`
  - 本次接口请求耗时（毫秒）
- `fallback`
  - `none|mouse|center|center-no-url`
- `raw API`
  - 接口原始坐标（通常来自 API 的 `coordinate`）
- `viewport`
  - 按 `basis` 换算到当前视口后的坐标
- `mapped`
  - 后端返回的映射后坐标（高亮实际使用）
- `calibration(backend)`
  - 后端校准是否启用

---

## 5) 常用环境变量（后端）

- `EYE_SERVER_HOST`：服务地址，默认 `127.0.0.1`
- `EYE_SERVER_PORT`：服务端口，默认 `3000`
- `EYE_SERVER_ENDPOINT`：坐标路径，默认 `/coordinate`
- `EYE_COORD_FORMAT`：默认返回格式，默认 `object`
- `EYE_COORD_WIDTH`：坐标映射宽度，默认 `1920`
- `EYE_COORD_HEIGHT`：坐标映射高度，默认 `1080`
- `EYE_FLIP_X`：X 轴翻转，默认开启（`1`）

示例（Windows PowerShell）：

```powershell
set EYE_SERVER_PORT=3001
set EYE_COORD_FORMAT=nested
set EYE_FLIP_X=1
python python-eye-server/eye_server.py
```

---

## 6) 快速联调示例

```powershell
# 健康检查
curl http://127.0.0.1:3000/health

# 默认格式
curl http://127.0.0.1:3000/coordinate

# 嵌套格式
curl "http://127.0.0.1:3000/coordinate?format=nested"

# 全量调试格式
curl "http://127.0.0.1:3000/coordinate?format=debug"

# 基础格式 + 调试信息
curl "http://127.0.0.1:3000/coordinate?format=object&debug=1"

# 查看校准状态
curl "http://127.0.0.1:3000/calibration"
```
