# lan-file-share

A **single-file LAN file sharing & chat web app** built with Flask + Flask-SocketIO.
Share files between phones / tablets / PCs on the same local network, with a built-in
real-time chat room.

[中文文档](#中文文档)

## Features

- **File categories** — images / videos / music / others, auto-created folders
- **Upload & download** — supports large files with chunked transfer and progress display
- **File preview** — images, audio and video play inline in the browser
- **Real-time chat** — chat history (last 100 messages) + online user list
- **Multi-device** — works on phones, tablets and PCs on the same LAN
- **Auto cleanup** — offline users are removed automatically
- Single Python file — no database, no build step

## Quick start

```bash
# 1. Install dependencies (Python 3.8+)
pip install -r requirements.txt

# 2. Run
python app.py
```

The server listens on `0.0.0.0:5000` and prints the LAN access URL, e.g.:

```
访问地址: http://192.168.1.23:5000
```

Open that URL from any device on the same network.

## Requirements

```
flask
flask-socketio
gevent
gevent-websocket
```

## Configuration

- `UPLOAD_FOLDER` — upload directory (default `uploads/`, created automatically)
- `MAX_CONTENT_LENGTH` — max upload size per request (default 1 GB)
- `SECRET_KEY` — set via environment variable `SECRET_KEY`; for production, always set your own:

```bash
export SECRET_KEY='your-random-secret'
```

> The default is `dev-secret-key-change-me` — change it before deploying.

## Project structure

```
lan-file-share/
├── app.py               # the whole application (server + inline HTML/JS)
├── requirements.txt
└── uploads/             # created automatically (gitignored)
```

## License

MIT © 2026 Haojie Jin

---

# 中文文档

## 简介

一个**单文件实现的局域网文件共享与聊天 Web 应用**（Flask + Flask-SocketIO）。
在同一局域网内，手机 / 平板 / 电脑之间互传文件，并内置实时聊天室。

## 功能

- **文件分类** — 图片 / 视频 / 音乐 / 其他，自动创建对应目录
- **上传 / 下载** — 支持大文件分块传输与进度显示
- **文件预览** — 图片、音频、视频直接在浏览器中播放
- **实时聊天** — 聊天历史（保留最近 100 条）+ 在线用户列表
- **多设备** — 同一局域网内的手机 / 平板 / 电脑均可访问
- **自动清理** — 离线用户自动移除
- 单 Python 文件，无数据库、无需构建

## 快速开始

```bash
# 1. 安装依赖（Python 3.8+）
pip install -r requirements.txt

# 2. 运行
python app.py
```

服务监听 `0.0.0.0:5000` 并打印局域网访问地址，例如：

```
访问地址: http://192.168.1.23:5000
```

同一网络内的设备打开该地址即可使用。

## 依赖

```
flask
flask-socketio
gevent
gevent-websocket
```

## 配置

- `UPLOAD_FOLDER` — 上传目录（默认 `uploads/`，自动创建）
- `MAX_CONTENT_LENGTH` — 单次上传大小上限（默认 1 GB）
- `SECRET_KEY` — 通过环境变量 `SECRET_KEY` 设置；生产环境务必自行设置：

```bash
export SECRET_KEY='your-random-secret'
```

> 默认值为 `dev-secret-key-change-me`，部署前请修改。

## 项目结构

```
lan-file-share/
├── app.py               # 整个应用（服务端 + 内联 HTML/JS）
├── requirements.txt
└── uploads/             # 自动创建（已 gitignore）
```

## 许可证

MIT © 2026 Haojie Jin