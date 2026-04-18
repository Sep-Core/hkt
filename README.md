# Shrimp + Python Eye Server Demo

这个示例包含两部分：

- `python-eye-server/`: 本地眼动后端（HTTP 坐标 API）
- `eye-extension/`: Shrimp 调试插件（聚焦可视区 + 红框调试）

## 1) 启动 Python 后端

```powershell
cd python-eye-server
pip install -r requirements.txt
python eye_server.py
```

默认地址：

- `http://127.0.0.1:3000/coordinate`
- 健康检查：`http://127.0.0.1:3000/health`

首次在 Python 3.13 上运行时，程序会自动下载 `face_landmarker.task` 到 `python-eye-server/models/`。

## 2) 加载 Chrome 插件（Shrimp 调试工具）

1. 打开 `chrome://extensions/`
2. 开启“开发者模式”
3. 点击“加载已解压的扩展程序”
4. 选择 `eye-extension/` 目录
5. 点击扩展图标，配置坐标接口 URL（例如 `http://127.0.0.1:3000/coordinate`）

## 3) 本地接口返回格式（兼容）

后端支持以下四种格式，插件会自动识别：

- JSON 对象：`{"x":320,"y":240}`
- 嵌套对象：`{"coordinate":{"x":320,"y":240}}`
- 数组：`[320,240]`
- 文本：`320,240`

默认返回 `{"x":...,"y":...}`。你可以在请求时带参数切换格式：

- `?format=object`
- `?format=nested`
- `?format=array`
- `?format=text`
- `?format=debug`（返回全量调试信息，含置信度、时间戳、后端状态、兼容格式）

也可以在任意格式后附加：

- `?debug=1` 或 `?verbose=1`（返回全量调试结构）

也可以通过环境变量设置默认格式：

```powershell
set EYE_COORD_FORMAT=nested
python eye_server.py
```

如果你发现左右方向反了（看右边时高亮往左走），可切换水平翻转：

```powershell
set EYE_FLIP_X=1
python eye_server.py
```

关闭翻转：

```powershell
set EYE_FLIP_X=0
python eye_server.py
```

## 4) 坐标基准说明（插件）

插件支持三种坐标基准：

- `auto`（默认）：先按 viewport 解释，不合适时再尝试 document
- `viewport`：接口返回视口坐标
- `document`：接口返回文档坐标（会自动减去滚动偏移）

如果接口没有返回有效坐标，插件会自动回退：

- 优先使用当前鼠标位置
- 还没有鼠标记录时用屏幕中心

## 5) 注视校准（推荐）

如果你感觉方向或偏移不准，可以做一次校准：

1. 打开任意普通网页并保持页面可见
2. 点击扩展弹窗里的 `Start Calibration`
3. 页面会依次出现 5 个蓝色点，请每个点都盯住约 1 秒
4. 完成后会把样本提交到后端，由后端计算并保存映射参数

重置校准可点 `Reset Calibration`。

## 6) 注意事项

- 这是演示级估算，未做标定，精度有限。
- 需要摄像头权限与稳定光照。
- `chrome://`、扩展页等特殊页面无法注入内容脚本（浏览器限制）。
