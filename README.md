# Shrimp + Python Eye Server Demo

这个示例当前主流程只有一部分：

- `python-eye-server/`: 本地眼动后端（HTTP 坐标 API + WebUI 校准）

## 1) 启动 Python 后端

```powershell
cd python-eye-server
pip install -r requirements.txt
python eye_server.py
```

如需显示摄像头画面和关键点叠加层：

```powershell
python eye_server.py --preview
```

可指定摄像头索引：

```powershell
python eye_server.py --preview --camera-index 1
```

预览窗口按 `Q` 或 `Esc` 可退出。

默认地址：

- `http://127.0.0.1:3000/coordinate`
- 健康检查：`http://127.0.0.1:3000/health`
- WebUI：`http://127.0.0.1:3000/`

首次在 Python 3.13 上运行时，程序会自动下载 `face_landmarker.task` 到 `python-eye-server/models/`。

## 2) 使用 WebUI 完成校准（推荐）

1. 打开浏览器访问 `http://127.0.0.1:3000/`
2. 点击 `同步屏幕尺寸`
3. 点击 `开始校准`
4. 按提示依次盯住 9 个蓝点，保持每个点阶段头部稳定
5. 完成后即可直接看到 `Mapped 屏幕坐标`

重置校准可点击 `重置校准`。

## 3) 本地接口返回格式（兼容）

后端支持以下四种格式：

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

可调上下灵敏度（默认已增强）：

```powershell
set EYE_VERTICAL_GAIN=1.8
set EYE_Y_SMOOTHING=0.3
python eye_server.py
```

说明：
- `EYE_VERTICAL_GAIN` 越大，上下移动越敏感（建议 1.2 ~ 2.2）
- `EYE_Y_SMOOTHING` 越小，响应更快但抖动更明显（建议 0.2 ~ 0.5）
- `EYE_SIZE_COMPENSATION=1`（默认）会按眼睛开合/宽度做归一化，降低“眼睛大小/半眯眼”造成的灵敏度变化
- `EYE_DYNAMIC_ALPHA_MIN` / `EYE_DYNAMIC_ALPHA_MAX` 可调“静止更稳、移动更跟手”的自适应平滑范围
- `EYE_JUMP_GUARD` / `EYE_MAX_STEP` 可抑制异常跳点，减少光标瞬移

## 4) 屏幕坐标说明

- `/coordinate` 返回的是屏幕坐标（单位为像素）
- WebUI 会在进入页面时调用 `/screen` 同步屏幕宽高
- 校准样本使用“raw 屏幕坐标 -> target 屏幕坐标”拟合，最终输出更贴近真实屏幕位置

## 5) 注意事项

- 这是演示级估算，未做标定，精度有限。
- 需要摄像头权限与稳定光照。
- 建议使用同一块显示器完成采样与使用，避免跨屏导致坐标偏移。
