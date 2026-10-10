#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import math
import socket
import hashlib
import mimetypes
import threading
import unicodedata
from datetime import datetime
from collections import deque
from flask import Flask, request, send_from_directory, render_template_string, jsonify, redirect, url_for, abort
from flask_socketio import SocketIO, emit, join_room, leave_room
import signal

# 初始化应用
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-me')
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024  # 1GB限制
socketio = SocketIO(app, async_mode='gevent', logger=True, engineio_logger=True)

# 注册音频MIME类型
mimetypes.add_type('audio/mpeg', '.mp3')
mimetypes.add_type('audio/wav', '.wav')
mimetypes.add_type('audio/ogg', '.ogg')
mimetypes.add_type('audio/flac', '.flac')

# 文件分类配置
FILE_CATEGORIES = {
    'images': {'ext': ['.jpg', '.jpeg', '.png', '.gif', '.bmp'], 'path': 'images', 'name': '图片'},
    'videos': {'ext': ['.mp4', '.avi', '.mov', '.mkv'], 'path': 'videos', 'name': '视频'},
    'music': {'ext': ['.mp3', '.wav', '.flac', '.ogg'], 'path': 'music', 'name': '音乐'},
    'others': {'ext': [], 'path': 'others', 'name': '其他'}
}

# 确保上传目录和分类子目录存在
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
for category in FILE_CATEGORIES.values():
    os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], category['path']), exist_ok=True)

# 存储在线用户 {sid: {'nickname': str, 'ip': str, 'last_active': float}}
online_users = {}
users_lock = threading.Lock()

# 存储上传进度 {file_id: {'progress': int, 'total_size': int, 'filename': str}}
upload_progress = {}
progress_lock = threading.Lock()

# 聊天历史记录（最大保留100条）
CHAT_HISTORY = deque(maxlen=100)

# 清理线程 - 定期清理离线用户
def cleanup_users():
    while True:
        time.sleep(60)  # 每分钟清理一次
        now = time.time()
        with users_lock:
            to_remove = [sid for sid, user in online_users.items() 
                        if now - user['last_active'] > 120]  # 2分钟无活动视为离线
            for sid in to_remove:
                nickname = online_users[sid]['nickname']
                del online_users[sid]
                socketio.emit('user_left', {'nickname': nickname}, room='chatroom')
                socketio.emit('online_users', list_online_users(), room='chatroom')

# 启动清理线程
cleanup_thread = threading.Thread(target=cleanup_users, daemon=True)
cleanup_thread.start()

# 获取本机IP地址
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# 生成文件唯一ID
def generate_file_id(filename):
    timestamp = str(time.time()).encode('utf-8')
    return hashlib.md5(timestamp + filename.encode('utf-8')).hexdigest()

# 获取在线用户列表
def list_online_users():
    with users_lock:
        return [{
            'nickname': u['nickname'],
            'ip': u['ip'],
            'last_active': datetime.fromtimestamp(u['last_active']).strftime('%H:%M:%S')
        } for u in online_users.values()]

# 格式化文件大小
def format_size(size_bytes):
    if size_bytes == 0:
        return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

# 支持中文的安全文件名处理
def custom_secure_filename(filename):
    """支持中文的安全文件名处理"""
    filename = unicodedata.normalize('NFKD', filename)
    filename = re.sub(r'[^\w\u4e00-\u9fa5.-]', '', filename)
    filename = filename.replace(' ', '_').strip().lstrip('.')
    return filename[:255]

def get_file_category(filename):
    """获取文件分类"""
    ext = os.path.splitext(filename)[1].lower()
    for cat, info in FILE_CATEGORIES.items():
        if ext in info['ext']:
            return cat
    return 'others'

def get_files(category_id, search_query=None):
    """获取指定分类的文件列表（带搜索过滤）"""
    category = FILE_CATEGORIES.get(category_id, FILE_CATEGORIES['others'])
    path = os.path.join(app.config['UPLOAD_FOLDER'], category['path'])
    files = []
    
    for filename in os.listdir(path):
        if search_query and search_query.lower() not in filename.lower():
            continue
            
        file_path = os.path.join(path, filename)
        if os.path.isfile(file_path):
            size = os.path.getsize(file_path)
            mime_type, _ = mimetypes.guess_type(filename)
            files.append({
                'name': filename,
                'path': f"{category['path']}/{filename}",
                'mime': mime_type or 'application/octet-stream',
                'size': size,
                'formatted_size': format_size(size),
                'upload_time': datetime.fromtimestamp(
                    os.path.getctime(file_path)).strftime('%Y-%m-%d %H:%M')
            })
    # 按上传时间倒序排序
    return sorted(files, key=lambda x: x['upload_time'], reverse=True)

# ====================================
# 路由定义
# ====================================
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE,
        categories=FILE_CATEGORIES,
        current_category=None,
        files=[],
        search_query='',
        chat_history=list(CHAT_HISTORY),
        online_users=list_online_users()
    )

@app.route('/files/<category>')
def files_page(category):
    search_query = request.args.get('search', '')
    # 获取分类配置，如果不存在则重定向到首页
    if category not in FILE_CATEGORIES:
        return redirect(url_for('index'))
    
    category_info = FILE_CATEGORIES[category]
    files = get_files(category, search_query)
    return render_template_string(HTML_TEMPLATE,
        categories=FILE_CATEGORIES,
        current_category=category_info['name'],
        files=files,
        search_query=search_query,
        chat_history=list(CHAT_HISTORY),
        online_users=list_online_users()
    )

@app.route('/preview/<path:filepath>')
def file_preview(filepath):
    safe_path = os.path.join(app.config['UPLOAD_FOLDER'], filepath)
    if not os.path.exists(safe_path):
        abort(404)
    
    mime_type, _ = mimetypes.guess_type(filepath)
    return send_from_directory(
        app.config['UPLOAD_FOLDER'],
        filepath,
        mimetype=mime_type,
        conditional=True
    )

@app.route('/download/<path:filepath>')
def download_file(filepath):
    return send_from_directory(
        app.config['UPLOAD_FOLDER'],
        filepath,
        as_attachment=True,
        download_name=os.path.basename(filepath)
    )

@app.route('/delete/<path:filepath>')
def delete_file(filepath):
    safe_path = os.path.join(app.config['UPLOAD_FOLDER'], filepath)
    if os.path.exists(safe_path):
        os.remove(safe_path)
        # 通知所有用户文件列表更新
        socketio.emit('file_list_update', get_files(get_file_category(os.path.basename(filepath))), room='chatroom')
    return redirect(request.referrer or url_for('index'))

@app.route('/delete_message/<int:msg_id>')
def delete_message(msg_id):
    # 删除指定ID的聊天记录
    try:
        del CHAT_HISTORY[msg_id - 1]
    except IndexError:
        pass
    return redirect(request.referrer or url_for('index'))

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': '没有选择文件'}), 400
    
    uploaded_files = request.files.getlist('file')
    if not uploaded_files:
        return jsonify({'error': '无效文件'}), 400
    
    # 获取客户端信息
    client_ip = request.remote_addr
    nickname = request.form.get('nickname', 'Anonymous')
    task_id = request.form.get('task_id', '')
    
    if not task_id:
        return jsonify({'error': '缺少任务ID'}), 400
    
    for file in uploaded_files:
        if file.filename == '':
            continue
            
        filename = custom_secure_filename(file.filename)
        category = get_file_category(filename)
        save_dir = os.path.join(app.config['UPLOAD_FOLDER'], FILE_CATEGORIES[category]['path'])
        
        # 处理文件重名
        counter = 1
        name, ext = os.path.splitext(filename)
        save_path = os.path.join(save_dir, filename)
        while os.path.exists(save_path):
            filename = f"{name}_{counter}{ext}"
            save_path = os.path.join(save_dir, filename)
            counter += 1
        
        # 分块传输处理
        chunk_size = 4096 * 1024  # 4MB chunks
        total_size = int(request.headers.get('Content-Length', 0))
        received = 0
        
        try:
            with open(save_path, 'wb') as f:
                while True:
                    chunk = file.stream.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    received += len(chunk)
                    
                    # 计算并发送进度
                    progress = min(100, int((received / total_size) * 100)) if total_size > 0 else 0
                    with progress_lock:
                        upload_progress[task_id] = {
                            'progress': progress,
                            'total_size': total_size,
                            'filename': filename
                        }
                    
                    # 通过SocketIO发送进度更新
                    socketio.emit('upload_progress', {
                        'task_id': task_id,
                        'progress': progress,
                        'filename': filename
                    }, room=request.sid)
            
            # 上传完成
            with progress_lock:
                if task_id in upload_progress:
                    del upload_progress[task_id]
            
            # 通知所有用户文件列表更新
            socketio.emit('file_list_update', get_files(category), room='chatroom')
        
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    return jsonify({
        'message': '文件上传成功',
        'filename': filename,
        'size': received
    }), 200

# ====================================
# SocketIO事件处理
# ====================================
@socketio.on('connect')
def handle_connect():
    client_ip = request.remote_addr
    print(f"客户端连接: {request.sid} from {client_ip}")
    emit('connected', {'sid': request.sid, 'ip': client_ip})

@socketio.on('join')
def handle_join(data):
    nickname = data.get('nickname', 'Anonymous')
    client_ip = request.remote_addr
    
    with users_lock:
        online_users[request.sid] = {
            'nickname': nickname,
            'ip': client_ip,
            'last_active': time.time()
        }
    
    join_room('chatroom')
    join_room(request.sid)  # 加入私人房间用于进度更新
    
    # 发送欢迎消息
    emit('message', {
        'type': 'system',
        'text': f"{nickname}加入了聊天室",
        'timestamp': datetime.now().strftime('%H:%M:%S'),
        'id': len(CHAT_HISTORY) + 1
    }, room='chatroom')
    
    # 发送在线用户列表
    emit('online_users', list_online_users(), room='chatroom')
    
    # 发送聊天历史
    emit('chat_history', list(CHAT_HISTORY), room=request.sid)

@socketio.on('disconnect')
def handle_disconnect():
    with users_lock:
        if request.sid in online_users:
            nickname = online_users[request.sid]['nickname']
            del online_users[request.sid]
            
            # 通知其他用户
            emit('message', {
                'type': 'system',
                'text': f"{nickname}离开了聊天室",
                'timestamp': datetime.now().strftime('%H:%M:%S'),
                'id': len(CHAT_HISTORY) + 1
            }, room='chatroom')
            
            # 更新在线用户列表
            emit('online_users', list_online_users(), room='chatroom')

@socketio.on('chat_message')
def handle_chat_message(data):
    message = data.get('message', '').strip()
    if not message:
        return
    
    with users_lock:
        if request.sid in online_users:
            user = online_users[request.sid]
            user['last_active'] = time.time()
            nickname = user['nickname']
            
            # 创建消息对象
            msg_obj = {
                'user': nickname,
                'message': message,
                'time': datetime.now().strftime('%H:%M:%S'),
                'id': len(CHAT_HISTORY) + 1
            }
            
            # 添加到历史记录
            CHAT_HISTORY.append(msg_obj)
            
            # 广播消息
            emit('message', {
                'type': 'user',
                'nickname': nickname,
                'text': message,
                'timestamp': datetime.now().strftime('%H:%M:%S'),
                'id': msg_obj['id']
            }, room='chatroom')

# ====================================
# HTML模板
# ====================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>局域网文件共享与聊天系统</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        
        :root {
            --primary: #4361ee;
            --secondary: #3f37c9;
            --success: #4cc9f0;
            --light: #f8f9fa;
            --dark: #212529;
            --gray: #6c757d;
            --danger: #e63946;
        }
        
        body {
            background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
            min-height: 100vh;
            padding: 20px;
            color: var(--dark);
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
            display: grid;
            grid-template-columns: 1fr;
            gap: 20px;
        }
        
        @media (min-width: 992px) {
            .container {
                grid-template-columns: 1fr 1fr;
            }
        }
        
        .panel {
            background: white;
            border-radius: 12px;
            box-shadow: 0 8px 30px rgba(0, 0, 0, 0.12);
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }
        
        .panel-header {
            background: var(--primary);
            color: white;
            padding: 15px 20px;
            font-size: 1.2rem;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .panel-header i {
            font-size: 1.4rem;
        }
        
        .panel-content {
            padding: 20px;
            flex-grow: 1;
            overflow-y: auto;
            max-height: 500px;
        }
        
        .nav {
            margin-bottom: 20px;
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            padding: 10px 0;
            border-bottom: 1px solid #eee;
        }
        
        .nav a {
            padding: 8px 15px;
            border-radius: 6px;
            text-decoration: none;
            color: var(--dark);
            background: #f0f2f5;
            transition: all 0.3s;
        }
        
        .nav a:hover, .nav a.active {
            background: var(--primary);
            color: white;
        }
        
        .section-title {
            margin-bottom: 15px;
            font-size: 1.1rem;
            color: var(--secondary);
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .btn {
            background: var(--primary);
            color: white;
            border: none;
            padding: 10px 15px;
            border-radius: 6px;
            cursor: pointer;
            font-weight: 500;
            transition: all 0.3s;
            display: inline-flex;
            align-items: center;
            gap: 5px;
        }
        
        .btn:hover {
            background: var(--secondary);
            transform: translateY(-2px);
            box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1);
        }
        
        .btn i {
            font-size: 1.1rem;
        }
        
        .file-input {
            display: none;
        }
        
        .upload-area {
            border: 2px dashed #ccc;
            border-radius: 8px;
            padding: 25px;
            text-align: center;
            margin-bottom: 20px;
            cursor: pointer;
            transition: all 0.3s;
        }
        
        .upload-area:hover {
            border-color: var(--primary);
            background: rgba(67, 97, 238, 0.05);
        }
        
        .upload-area i {
            font-size: 3rem;
            color: var(--gray);
            margin-bottom: 10px;
        }
        
        .file-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 15px;
        }
        
        .file-item {
            border: 1px solid #ddd;
            border-radius: 8px;
            padding: 10px;
            text-align: center;
            transition: all 0.3s;
        }
        
        .file-item:hover {
            transform: translateY(-5px);
            box-shadow: 0 5px 15px rgba(0, 0, 0, 0.1);
        }
        
        .preview {
            max-width: 100%;
            height: 120px;
            object-fit: contain;
            border-radius: 4px;
            background: #f8f9fa;
            padding: 5px;
        }
        
        .file-info {
            margin-top: 10px;
        }
        
        .file-name {
            font-weight: 500;
            margin-bottom: 4px;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        
        .file-meta {
            font-size: 0.85rem;
            color: var(--gray);
            display: flex;
            flex-direction: column;
            gap: 3px;
        }
        
        .progress-container {
            margin: 15px 0;
        }
        
        .progress-header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 5px;
            font-size: 0.9rem;
        }
        
        .progress-bar {
            height: 10px;
            background: #e9ecef;
            border-radius: 5px;
            overflow: hidden;
        }
        
        .progress-fill {
            height: 100%;
            background: var(--success);
            border-radius: 5px;
            width: 0%;
            transition: width 0.3s;
        }
        
        .chat-messages {
            height: 300px;
            overflow-y: auto;
            padding: 15px;
            background: #f8f9fa;
            border-radius: 8px;
            margin-bottom: 15px;
        }
        
        .message {
            margin-bottom: 15px;
            padding: 10px 15px;
            border-radius: 8px;
            max-width: 80%;
        }
        
        .message-user {
            background: #e3f2fd;
            margin-left: auto;
        }
        
        .message-system {
            background: #e8f5e9;
            text-align: center;
            max-width: 100%;
        }
        
        .message-header {
            display: flex;
            justify-content: space-between;
            font-size: 0.85rem;
            margin-bottom: 5px;
            color: var(--gray);
        }
        
        .message-text {
            line-height: 1.4;
        }
        
        .chat-input {
            display: flex;
            gap: 10px;
        }
        
        .chat-input input {
            flex-grow: 1;
            padding: 12px 15px;
            border: 1px solid #ddd;
            border-radius: 6px;
            font-size: 1rem;
        }
        
        .online-users {
            background: #f8f9fa;
            border-radius: 8px;
            padding: 15px;
            margin-top: 20px;
        }
        
        .user-list {
            list-style: none;
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
            gap: 10px;
            margin-top: 10px;
        }
        
        .user-item {
            background: white;
            border-radius: 6px;
            padding: 8px 10px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.05);
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .user-status {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: #4ade80;
        }
        
        .user-name {
            font-size: 0.9rem;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        
        .notification {
            position: fixed;
            top: 20px;
            right: 20px;
            background: var(--success);
            color: white;
            padding: 15px 25px;
            border-radius: 8px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.2);
            transform: translateX(150%);
            transition: transform 0.3s ease;
            z-index: 1000;
        }
        
        .notification.show {
            transform: translateX(0);
        }
        
        .status-bar {
            background: var(--dark);
            color: white;
            padding: 10px 20px;
            text-align: center;
            font-size: 0.9rem;
            display: flex;
            justify-content: center;
            gap: 20px;
            flex-wrap: wrap;
        }
        
        .connection-status {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .connection-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: #4ade80;
        }
        
        .connection-dot.disconnected {
            background: #ef4444;
        }
        
        .action-buttons {
            display: flex;
            gap: 10px;
            margin-top: 15px;
        }
        
        .btn-secondary {
            background: #6c757d;
        }
        
        .btn-danger {
            background: var(--danger);
        }
        
        .search-box {
            margin: 15px 0;
            display: flex;
            gap: 10px;
        }
        
        .search-box input {
            flex-grow: 1;
            padding: 10px 15px;
            border: 1px solid #ddd;
            border-radius: 6px;
            font-size: 1rem;
        }
        
        .delete-message-btn {
            color: var(--danger);
            cursor: pointer;
            margin-left: 10px;
            font-size: 0.9rem;
        }
    </style>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
</head>
<body>
    <div class="container">
        <div class="panel">
            <div class="panel-header">
                <i class="fas fa-file-import"></i>
                <span>文件共享</span>
            </div>
            <div class="panel-content">
                <!-- 导航 -->
                <div class="nav">
                    <a href="/" {% if not current_category %}class="active"{% endif %}>聊天室</a>
                    {% for cat_key, cat in categories.items() %}
                    <a href="{{ url_for('files_page', category=cat_key) }}" 
                       {% if current_category == cat.name %}class="active"{% endif %}>
                        {{ cat.name }}
                    </a>
                    {% endfor %}
                </div>

                {% if not current_category %}
                    <!-- 聊天室内容 -->
                    <div class="chat-section">
                        <h2 class="section-title">实时聊天室</h2>
                        <div class="chat-messages" id="chat-messages">
                            {% for msg in chat_history %}
                            <div class="message message-user" id="msg-{{ msg.id }}">
                                <div class="message-header">
                                    <span class="message-sender">{{ msg.user }}</span>
                                    <span class="message-time">{{ msg.time }}</span>
                                </div>
                                <div class="message-text">{{ msg.message }}</div>
                                <span class="delete-message-btn" onclick="deleteMessage('{{ msg.id }}')">🗑️</span>
                            </div>
                            {% endfor %}
                        </div>
                        <div class="chat-input">
                            <input type="text" id="message-input" placeholder="输入消息">
                            <button class="btn" id="send-btn">
                                <i class="fas fa-paper-plane"></i> 发送
                            </button>
                        </div>
                    </div>
                {% else %}
                    <!-- 文件页面内容 -->
                    <h2 class="section-title">{{ current_category }}文件管理</h2>
                    
                    <!-- 搜索和上传 -->
                    <div class="search-box">
                        <form method="GET" style="display: flex; width: 100%; gap: 10px;">
                            <input type="text" name="search" placeholder="搜索文件名..." value="{{ search_query }}">
                            <button class="btn" type="submit">搜索</button>
                        </form>
                    </div>
                    
                    <div class="upload-area" id="upload-area">
                        <i class="fas fa-cloud-upload-alt"></i>
                        <h3>点击或拖放文件到此处</h3>
                        <p>支持多文件上传，最大文件大小1GB</p>
                        <input type="file" id="file-input" class="file-input" multiple>
                    </div>
                    
                    <div class="action-buttons">
                        <button class="btn" id="upload-btn">
                            <i class="fas fa-upload"></i> 开始上传
                        </button>
                        <button class="btn btn-secondary" id="refresh-files">
                            <i class="fas fa-sync"></i> 刷新列表
                        </button>
                    </div>
                    
                    <div id="progress-bars"></div>
                    
                    <div class="file-grid" id="file-grid">
                        {% for file in files %}
                        <div class="file-item">
                            {% if current_category == '图片' %}
                                <img src="/preview/{{ file.path }}" class="preview">
                            {% elif current_category == '视频' %}
                                <video class="preview" controls>
                                    <source src="/preview/{{ file.path }}" type="{{ file.mime }}">
                                </video>
                            {% elif current_category == '音乐' %}
                                <audio class="preview" controls>
                                    <source src="/preview/{{ file.path }}" type="{{ file.mime }}">
                                </audio>
                            {% else %}
                                <div class="preview" style="display: flex; align-items: center; justify-content: center;">
                                    <i class="fas fa-file" style="font-size: 3rem; color: #6c757d;"></i>
                                </div>
                            {% endif %}
                            <div class="file-info">
                                <div class="file-name">
                                    <i class="fas {{ get_file_icon(file.name) }}"></i>
                                    <span>{{ file.name }}</span>
                                </div>
                                <div class="file-meta">
                                    <span>大小: {{ file.formatted_size }}</span>
                                    <span>上传: {{ file.upload_time }}</span>
                                </div>
                            </div>
                            <div class="action-buttons" style="margin-top: 10px; justify-content: center;">
                                <a href="/download/{{ file.path }}" class="btn btn-secondary" download>
                                    <i class="fas fa-download"></i>
                                </a>
                                <button class="btn btn-danger" onclick="deleteFile('{{ file.path }}')">
                                    <i class="fas fa-trash"></i>
                                </button>
                            </div>
                        </div>
                        {% else %}
                        <div class="file-item" style="grid-column: 1 / -1; text-align: center; padding: 30px;">
                            <i class="fas fa-folder-open" style="font-size: 3rem; color: #6c757d;"></i>
                            <h3>暂无文件</h3>
                            <p>请上传文件或尝试其他搜索条件</p>
                        </div>
                        {% endfor %}
                    </div>
                {% endif %}
            </div>
        </div>
        
        <div class="panel">
            <div class="panel-header">
                <i class="fas fa-comments"></i>
                <span>实时聊天与在线用户</span>
            </div>
            <div class="panel-content">
                <div class="online-users">
                    <div class="section-title">
                        <i class="fas fa-users"></i>
                        <span>在线用户 (<span id="online-count">{{ online_users|length }}</span>)</span>
                    </div>
                    <ul class="user-list" id="online-users">
                        {% for user in online_users %}
                        <li class="user-item">
                            <div class="user-status"></div>
                            <span class="user-name" title="{{ user.nickname }} ({{ user.ip }})">
                                {{ user.nickname }}
                            </span>
                        </li>
                        {% else %}
                        <li class="user-item">
                            <div class="user-status" style="background: #ef4444;"></div>
                            <span class="user-name">暂无在线用户</span>
                        </li>
                        {% endfor %}
                    </ul>
                </div>
            </div>
        </div>
    </div>
    
    <div class="notification" id="notification">
        <div id="notification-text"></div>
    </div>
    
    <div class="status-bar">
        <div class="connection-status">
            <div class="connection-dot" id="connection-dot"></div>
            <span id="connection-status">连接中...</span>
        </div>
        <div>服务器: <span id="server-ip">加载中...</span></div>
        <div>您的IP: <span id="client-ip">加载中...</span></div>
        <div>昵称: <span id="nickname-display">未设置</span></div>
    </div>
    
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <script>
        // 全局变量
        let socket;
        let nickname = '';
        let clientIp = '';
        let serverIp = '';
        let currentUploads = {};
        let connectionStatus = 'disconnected';
        
        // DOM元素
        const uploadArea = document.getElementById('upload-area');
        const fileInput = document.getElementById('file-input');
        const uploadBtn = document.getElementById('upload-btn');
        const refreshBtn = document.getElementById('refresh-files');
        const fileGrid = document.getElementById('file-grid');
        const progressBars = document.getElementById('progress-bars');
        const chatMessages = document.getElementById('chat-messages');
        const messageInput = document.getElementById('message-input');
        const sendBtn = document.getElementById('send-btn');
        const onlineUsersList = document.getElementById('online-users');
        const onlineCount = document.getElementById('online-count');
        const notification = document.getElementById('notification');
        const notificationText = document.getElementById('notification-text');
        const connectionDot = document.getElementById('connection-dot');
        const connectionStatusText = document.getElementById('connection-status');
        const serverIpSpan = document.getElementById('server-ip');
        const clientIpSpan = document.getElementById('client-ip');
        const nicknameDisplay = document.getElementById('nickname-display');
        
        // 文件图标映射
        const fileIcons = {
            'pdf': 'fa-file-pdf',
            'doc': 'fa-file-word',
            'docx': 'fa-file-word',
            'xls': 'fa-file-excel',
            'xlsx': 'fa-file-excel',
            'ppt': 'fa-file-powerpoint',
            'pptx': 'fa-file-powerpoint',
            'jpg': 'fa-file-image',
            'jpeg': 'fa-file-image',
            'png': 'fa-file-image',
            'gif': 'fa-file-image',
            'mp3': 'fa-file-audio',
            'wav': 'fa-file-audio',
            'mp4': 'fa-file-video',
            'avi': 'fa-file-video',
            'mov': 'fa-file-video',
            'zip': 'fa-file-archive',
            'rar': 'fa-file-archive',
            '7z': 'fa-file-archive',
            'txt': 'fa-file-alt',
            'html': 'fa-file-code',
            'css': 'fa-file-code',
            'js': 'fa-file-code',
            'py': 'fa-file-code',
            'exe': 'fa-file-code'
        };
        
        // 初始化
        document.addEventListener('DOMContentLoaded', function() {
            // 获取昵称
            nickname = prompt('请输入您的昵称:', '用户' + Math.floor(Math.random() * 1000)) || '匿名用户';
            nicknameDisplay.textContent = nickname;
            
            // 初始化Socket连接
            initSocket();
            
            // 事件监听器
            setupEventListeners();
        });
        
        // 初始化Socket连接
        function initSocket() {
            // 获取当前页面URL的host
            const host = window.location.hostname;
            const port = window.location.port || (window.location.protocol === 'https:' ? 443 : 80);
            
            // 设置服务器IP显示
            serverIp = `${host}:${port}`;
            serverIpSpan.textContent = serverIp;
            
            // 连接Socket
            socket = io.connect(`//${serverIp}`, {
                reconnection: true,
                reconnectionAttempts: Infinity,
                reconnectionDelay: 1000,
                reconnectionDelayMax: 5000,
                timeout: 20000
            });
            
            // 连接事件
            socket.on('connect', () => {
                console.log('已连接到服务器');
                updateConnectionStatus('connected');
                
                // 加入聊天室
                socket.emit('join', { nickname });
            });
            
            socket.on('disconnect', () => {
                console.log('从服务器断开连接');
                updateConnectionStatus('disconnected');
            });
            
            socket.on('connect_error', (error) => {
                console.error('连接错误:', error);
                updateConnectionStatus('error');
            });
            
            socket.on('reconnecting', (attempt) => {
                console.log(`重新连接尝试 ${attempt}`);
                updateConnectionStatus('reconnecting');
            });
            
            socket.on('reconnect', () => {
                console.log('重新连接到服务器');
                updateConnectionStatus('connected');
                socket.emit('join', { nickname });
            });
            
            // 接收消息
            socket.on('message', (data) => {
                addMessage(data);
            });
            
            // 接收聊天历史
            socket.on('chat_history', (history) => {
                history.forEach(msg => {
                    addMessage({
                        type: 'user',
                        nickname: msg.user,
                        text: msg.message,
                        timestamp: msg.time,
                        id: msg.id
                    });
                });
            });
            
            // 在线用户更新
            socket.on('online_users', (users) => {
                updateOnlineUsers(users);
            });
            
            // 文件列表更新
            socket.on('file_list_update', (files) => {
                updateFileGrid(files);
            });
            
            // 上传进度
            socket.on('upload_progress', (data) => {
                updateProgress(data);
            });
            
            // 接收连接信息
            socket.on('connected', (data) => {
                clientIp = data.ip;
                clientIpSpan.textContent = clientIp;
            });
        }
        
        // 更新连接状态
        function updateConnectionStatus(status) {
            connectionStatus = status;
            const statusMap = {
                'connected': { text: '已连接', color: '#4ade80' },
                'disconnected': { text: '已断开', color: '#ef4444' },
                'reconnecting': { text: '重新连接中...', color: '#f59e0b' },
                'error': { text: '连接错误', color: '#ef4444' }
            };
            
            connectionStatusText.textContent = statusMap[status].text;
            connectionDot.style.backgroundColor = statusMap[status].color;
        }
        
        // 设置事件监听器
        function setupEventListeners() {
            // 文件上传区域点击
            if (uploadArea) {
                uploadArea.addEventListener('click', () => {
                    fileInput.click();
                });
            }
            
            // 文件选择
            if (fileInput) {
                fileInput.addEventListener('change', handleFileSelect);
            }
            
            // 开始上传按钮
            if (uploadBtn) {
                uploadBtn.addEventListener('click', startUpload);
            }
            
            // 刷新文件列表
            if (refreshBtn) {
                refreshBtn.addEventListener('click', refreshFiles);
            }
            
            // 发送消息按钮
            if (sendBtn && messageInput) {
                sendBtn.addEventListener('click', sendMessage);
                messageInput.addEventListener('keypress', (e) => {
                    if (e.key === 'Enter') {
                        sendMessage();
                    }
                });
            }
            
            // 拖放文件支持
            if (uploadArea) {
                uploadArea.addEventListener('dragover', (e) => {
                    e.preventDefault();
                    uploadArea.style.borderColor = '#4361ee';
                    uploadArea.style.backgroundColor = 'rgba(67, 97, 238, 0.1)';
                });
                
                uploadArea.addEventListener('dragleave', () => {
                    uploadArea.style.borderColor = '#ccc';
                    uploadArea.style.backgroundColor = '';
                });
                
                uploadArea.addEventListener('drop', (e) => {
                    e.preventDefault();
                    uploadArea.style.borderColor = '#ccc';
                    uploadArea.style.backgroundColor = '';
                    
                    if (e.dataTransfer.files.length > 0) {
                        fileInput.files = e.dataTransfer.files;
                        handleFileSelect();
                    }
                });
            }
        }
        
        // 处理文件选择
        function handleFileSelect() {
            const files = fileInput.files;
            if (files.length === 0) return;
            
            // 清空进度条区域
            progressBars.innerHTML = '';
            
            // 为每个文件创建进度条
            for (let i = 0; i < files.length; i++) {
                const file = files[i];
                const fileId = Date.now() + '_' + i;
                currentUploads[fileId] = {
                    filename: file.name,
                    size: file.size,
                    progress: 0
                };
                
                // 创建进度条UI
                const progressContainer = document.createElement('div');
                progressContainer.className = 'progress-container';
                progressContainer.id = `progress-${fileId}`;
                
                progressContainer.innerHTML = `
                    <div class="progress-header">
                        <span>${file.name}</span>
                        <span id="progress-text-${fileId}">0%</span>
                    </div>
                    <div class="progress-bar">
                        <div class="progress-fill" id="progress-fill-${fileId}"></div>
                    </div>
                    <div class="file-meta">大小: ${formatFileSize(file.size)}</div>
                `;
                
                progressBars.appendChild(progressContainer);
            }
        }
        
        // 开始上传
        function startUpload() {
            const files = fileInput.files;
            if (files.length === 0) {
                showNotification('请先选择文件', 'warning');
                return;
            }
            
            for (let i = 0; i < files.length; i++) {
                const file = files[i];
                const fileId = Object.keys(currentUploads)[i];
                
                if (!fileId) continue;
                
                const formData = new FormData();
                formData.append('file', file);
                formData.append('nickname', nickname);
                formData.append('task_id', fileId);
                
                fetch('/upload', {
                    method: 'POST',
                    body: formData
                })
                .then(response => response.json())
                .then(data => {
                    if (data.error) {
                        showNotification(`上传失败: ${data.error}`, 'error');
                    } else {
                        showNotification(`文件 ${data.filename} 上传成功!`, 'success');
                    }
                })
                .catch(error => {
                    console.error('上传错误:', error);
                    showNotification('上传失败: ' + error.message, 'error');
                });
            }
            
            // 清空文件选择
            fileInput.value = '';
        }
        
        // 更新上传进度
        function updateProgress(data) {
            const { task_id, progress, filename } = data;
            const progressText = document.getElementById(`progress-text-${task_id}`);
            const progressFill = document.getElementById(`progress-fill-${task_id}`);
            
            if (progressText && progressFill) {
                progressText.textContent = `${progress}%`;
                progressFill.style.width = `${progress}%`;
                
                if (progress === 100) {
                    setTimeout(() => {
                        const container = document.getElementById(`progress-${task_id}`);
                        if (container) {
                            container.style.opacity = '0';
                            setTimeout(() => container.remove(), 500);
                        }
                    }, 1000);
                }
            }
        }
        
        // 刷新文件列表
        function refreshFiles() {
            // 根据当前URL确定分类
            const pathSegments = window.location.pathname.split('/');
            let category = 'images';
            if (pathSegments.length > 2 && pathSegments[1] === 'files') {
                category = pathSegments[2];
            }
            
            fetch(`/files/${category}`)
                .then(response => response.text())
                .then(html => {
                    // 创建一个临时元素来解析HTML
                    const tempDiv = document.createElement('div');
                    tempDiv.innerHTML = html;
                    
                    // 获取文件网格内容
                    const newFileGrid = tempDiv.querySelector('#file-grid');
                    if (newFileGrid) {
                        fileGrid.innerHTML = newFileGrid.innerHTML;
                    }
                    
                    showNotification('文件列表已刷新', 'success');
                })
                .catch(error => {
                    console.error('刷新错误:', error);
                    showNotification('刷新失败: ' + error.message, 'error');
                });
        }
        
        // 更新文件网格
        function updateFileGrid(files) {
            if (!fileGrid) return;
            
            if (files.length === 0) {
                fileGrid.innerHTML = `
                    <div class="file-item" style="grid-column: 1 / -1; text-align: center; padding: 30px;">
                        <i class="fas fa-folder-open" style="font-size: 3rem; color: #6c757d;"></i>
                        <h3>暂无文件</h3>
                        <p>请上传文件或尝试其他搜索条件</p>
                    </div>
                `;
                return;
            }
            
            fileGrid.innerHTML = '';
            
            files.forEach(file => {
                const fileItem = document.createElement('div');
                fileItem.className = 'file-item';
                
                // 根据文件类型生成预览
                let previewHtml = '';
                const fileType = file.path.split('/')[0];
                
                if (fileType === 'images') {
                    previewHtml = `<img src="/preview/${file.path}" class="preview">`;
                } else if (fileType === 'videos') {
                    previewHtml = `<video class="preview" controls><source src="/preview/${file.path}" type="${file.mime}"></video>`;
                } else if (fileType === 'music') {
                    previewHtml = `<audio class="preview" controls><source src="/preview/${file.path}" type="${file.mime}"></audio>`;
                } else {
                    previewHtml = `<div class="preview" style="display: flex; align-items: center; justify-content: center;">
                        <i class="fas fa-file" style="font-size: 3rem; color: #6c757d;"></i>
                    </div>`;
                }
                
                fileItem.innerHTML = `
                    ${previewHtml}
                    <div class="file-info">
                        <div class="file-name">
                            <i class="fas ${getFileIcon(file.name)}"></i>
                            <span>${file.name}</span>
                        </div>
                        <div class="file-meta">
                            <span>大小: ${file.formatted_size}</span>
                            <span>上传: ${file.upload_time}</span>
                        </div>
                    </div>
                    <div class="action-buttons" style="margin-top: 10px; justify-content: center;">
                        <a href="/download/${file.path}" class="btn btn-secondary" download>
                            <i class="fas fa-download"></i>
                        </a>
                        <button class="btn btn-danger" onclick="deleteFile('${file.path}')">
                            <i class="fas fa-trash"></i>
                        </button>
                    </div>
                `;
                
                fileGrid.appendChild(fileItem);
            });
        }
        
        // 获取文件图标
        function getFileIcon(filename) {
            const ext = filename.split('.').pop().toLowerCase();
            return fileIcons[ext] || 'fa-file';
        }
        
        // 格式化文件大小
        function formatFileSize(bytes) {
            if (bytes === 0) return "0 B";
            const k = 1024;
            const sizes = ["B", "KB", "MB", "GB", "TB"];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
        }
        
        // 添加消息到聊天窗口
        function addMessage(data) {
            if (!chatMessages) return;
            
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${data.type === 'system' ? 'message-system' : 'message-user'}`;
            messageDiv.id = `msg-${data.id}`;
            
            let headerContent = '';
            if (data.type === 'user') {
                headerContent = `
                    <span class="message-sender">${data.nickname}</span>
                    <span class="message-time">${data.timestamp}</span>
                `;
            } else {
                headerContent = `<span class="message-time">${data.timestamp}</span>`;
            }
            
            messageDiv.innerHTML = `
                <div class="message-header">${headerContent}</div>
                <div class="message-text">${data.text}</div>
                ${data.type === 'user' ? `<span class="delete-message-btn" onclick="deleteMessage('${data.id}')">🗑️</span>` : ''}
            `;
            
            chatMessages.appendChild(messageDiv);
            chatMessages.scrollTop = chatMessages.scrollHeight;
        }
        
        // 发送消息
        function sendMessage() {
            if (!messageInput) return;
            
            const message = messageInput.value.trim();
            if (message === '') return;
            
            socket.emit('chat_message', { message });
            messageInput.value = '';
        }
        
        // 更新在线用户列表
        function updateOnlineUsers(users) {
            if (!onlineUsersList || !onlineCount) return;
            
            onlineCount.textContent = users.length;
            
            if (users.length === 0) {
                onlineUsersList.innerHTML = '<li class="user-item"><div class="user-status" style="background: #ef4444;"></div><span class="user-name">暂无在线用户</span></li>';
                return;
            }
            
            onlineUsersList.innerHTML = '';
            
            users.forEach(user => {
                const userItem = document.createElement('li');
                userItem.className = 'user-item';
                
                userItem.innerHTML = `
                    <div class="user-status"></div>
                    <span class="user-name" title="${user.nickname} (${user.ip})">${user.nickname}</span>
                `;
                
                onlineUsersList.appendChild(userItem);
            });
        }
        
        // 显示通知
        function showNotification(text, type = 'info') {
            if (!notification || !notificationText) return;
            
            notificationText.textContent = text;
            notification.className = 'notification show';
            
            // 设置背景颜色
            const colors = {
                'info': '#4361ee',
                'success': '#4cc9f0',
                'warning': '#ffaa00',
                'error': '#e63946'
            };
            notification.style.backgroundColor = colors[type] || '#4361ee';
            
            // 3秒后隐藏
            setTimeout(() => {
                notification.classList.remove('show');
            }, 3000);
        }
        
        // 删除文件
        function deleteFile(filepath) {
            if(confirm('确定要删除文件吗？')) {
                fetch(`/delete/${filepath}`)
                    .then(response => {
                        if (response.ok) {
                            showNotification('文件已删除', 'success');
                            refreshFiles();
                        } else {
                            showNotification('删除失败', 'error');
                        }
                    })
                    .catch(() => showNotification('删除失败', 'error'));
            }
        }
        
        // 删除消息
        function deleteMessage(id) {
            if(confirm('确定要删除这条消息吗？')) {
                fetch(`/delete_message/${id}`)
                    .then(response => {
                        if (response.ok) {
                            const msgElement = document.getElementById(`msg-${id}`);
                            if (msgElement) {
                                msgElement.remove();
                            }
                        } else {
                            showNotification('删除失败', 'error');
                        }
                    })
                    .catch(() => showNotification('删除失败', 'error'));
            }
        }
    </script>
</body>
</html>
"""

# 启动服务器
if __name__ == '__main__':
    local_ip = get_local_ip()
    port = 5000
    
    print(f"\n{'*' * 60}")
    print(f"*{' ' * 58}*")
    print(f"*{'局域网文件共享与聊天系统'.center(58)}*")
    print(f"*{' ' * 58}*")
    print(f"* 访问地址: http://{local_ip}:{port}".ljust(59) + "*")
    print(f"*{' ' * 58}*")
    print(f"* 功能说明:".ljust(59) + "*")
    print(f"*   - 文件分类管理 (图片/视频/音乐/其他)".ljust(59) + "*")
    print(f"*   - 文件上传/下载 (支持大文件分块传输)".ljust(59) + "*")
    print(f"*   - 实时聊天 (带历史记录和在线用户)".ljust(59) + "*")
    print(f"*   - 多设备支持 (手机/平板/电脑)".ljust(59) + "*")
    print(f"*{' ' * 58}*")
    print(f"{'*' * 60}\n")
    
    # 处理Ctrl+C退出
    def signal_handler(sig, frame):
        print("\n服务器正在关闭...")
        os._exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    
    # 启动应用
    socketio.run(app, host='0.0.0.0', port=port, debug=False)