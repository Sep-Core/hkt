# Python Eye Tracking + Browser Extension Demo

这是一个最小样例：通过 Python 读取摄像头估计视线，在浏览器任意网页上用红框标记大致注视区域。

## 目录

- `python-eye-server/`: 本地眼动服务（WebSocket）
- `eye-extension/`: Chrome 插件（内容脚本绘制红框）

## 1) 启动 Python 眼动服务

```powershell
cd python-eye-server
pip install -r requirements.txt
python eye_server.py
```

启动成功后会看到：

`[eye-server] ws://127.0.0.1:8765 started`

## 2) 加载 Chrome 插件

1. 打开 `chrome://extensions/`
2. 开启右上角 `开发者模式`
3. 点击 `加载已解压的扩展程序`
4. 选择 `eye-extension/` 文件夹

## 3) 测试

1. 保持 Python 服务运行
2. 打开任意普通网页（`http/https`）
3. 页面会出现红色半透明框，随视线粗略移动
4. 插件图标徽标显示：
   - `ON`：已连接 Python 服务
   - `OFF`：未连接

## 注意事项

- 这是**演示级**估算，未做严谨校准，精度有限。
- 需要摄像头权限与稳定光照。
- 首次在 Python 3.13 上运行时，程序会自动下载 `face_landmarker.task` 到 `python-eye-server/models/`。
- `chrome://`、扩展页等特殊页面不允许内容脚本注入，这是浏览器限制。
- 如果红框不动，先检查：
  - Python 服务是否在运行
  - 插件 popup 状态是否为 connected（可点 Reconnect）
  - 浏览器是否允许摄像头被 Python 进程访问
