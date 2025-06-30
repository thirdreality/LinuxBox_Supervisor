# WebSocket Manager and Token Manager

## 概述

本项目新增了两个核心模块来管理HomeAssistant的访问令牌和WebSocket通信：

1. **Token Manager** (`supervisor/token_manager.py`) - 管理HomeAssistant访问令牌
2. **WebSocket Manager** (`supervisor/websocket_manager.py`) - 通过WebSocket与HomeAssistant通信

## 功能特性

### Token Manager

- **长期访问令牌管理**: 从配置文件读取长期有效的访问令牌
- **网页登录令牌管理**: 通过模拟网页登录获取短期令牌（30分钟有效期）
- **令牌缓存**: 自动缓存短期令牌，避免重复登录
- **令牌验证**: 检查令牌是否仍然有效

### WebSocket Manager

- **WebSocket连接管理**: 自动建立和维护与HomeAssistant的WebSocket连接
- **认证处理**: 使用Token Manager获取的令牌进行自动认证
- **请求ID管理**: 自动管理WebSocket请求的ID，确保响应匹配
- **同步/异步支持**: 提供同步和异步两种接口

## 新增功能

### 1. ZHA Channel切换
```bash
# 切换到ZHA channel 11
zigbee channel_11

# 切换到ZHA channel 25
zigbee channel_25
```

### 2. Thread Channel切换
```bash
# 切换到Thread channel 11
thread channel_11

# 切换到Thread channel 25
thread channel_25
```

### 3. ZHA设备固件更新通知
```bash
# 通知所有ZHA设备更新固件
zigbee firmware_update
```

## 安装依赖

```bash
pip install -r requirements.txt
```

主要依赖：
- `websockets>=10.0` - WebSocket通信
- `requests>=2.25.0` - HTTP请求

## 使用方法

### Token Manager

```python
from supervisor.token_manager import TokenManager

# 创建Token Manager实例
token_manager = TokenManager()

# 获取长期访问令牌
long_token = token_manager.get_long_lived_access_tokens()

# 获取网页访问令牌（会自动缓存）
web_token = token_manager.get_web_access_tokens()

# 检查令牌是否有效
is_valid = token_manager.is_web_token_valid()

# 清除令牌缓存
token_manager.clear_web_token_cache()
```

### WebSocket Manager

```python
from supervisor.websocket_manager import WebSocketManager

# 创建WebSocket Manager实例
ws_manager = WebSocketManager()

# 同步方法
success = ws_manager.switch_zha_channel_sync(11)
success = ws_manager.switch_thread_channel_sync(11)
success = ws_manager.notify_zha_devices_firmware_update_sync()
devices = ws_manager.get_zha_devices_sync()
thread_devices = ws_manager.get_thread_devices_sync()

# 异步方法
import asyncio
success = await ws_manager.switch_zha_channel(11)
success = await ws_manager.switch_thread_channel(11)
success = await ws_manager.notify_zha_devices_firmware_update()
devices = await ws_manager.get_zha_devices()
thread_devices = await ws_manager.get_thread_devices()
```

## 集成到现有系统

### 任务管理

新功能已集成到现有的TaskManager中：

```python
# 启动ZHA channel切换任务
task_manager.start_zha_channel_switch(11)

# 启动Thread channel切换任务
task_manager.start_thread_channel_switch(11)

# 启动ZHA固件更新通知任务
task_manager.start_zha_firmware_update_notification()
```

### 命令处理

新功能已集成到Supervisor的命令处理系统中：

```python
# ZHA channel切换
supervisor.set_zigbee_command("channel_11")

# Thread channel切换
supervisor.set_thread_command("channel_11")

# ZHA固件更新通知
supervisor.set_zigbee_command("firmware_update")
```

## 配置文件

Token Manager需要以下配置文件：

### `/etc/automation-robot.conf`
包含长期访问令牌的配置文件：
```
token=your_long_lived_access_token_here
```

## 错误处理

所有模块都包含完善的错误处理：

- 网络连接错误
- 认证失败
- 配置文件不存在
- WebSocket连接超时
- 无效的channel参数

## 日志

所有操作都会记录详细的日志信息：

```bash
# 查看日志
tail -f /var/log/supervisor.log
```

## 测试

运行测试脚本验证功能：

```bash
python3 test_websocket_manager.py
```

## 注意事项

1. **Channel范围**: ZHA和Thread的channel必须在11-26之间
2. **令牌有效期**: 网页登录令牌有效期为30分钟
3. **网络连接**: 需要确保HomeAssistant服务正在运行
4. **权限**: 需要适当的HomeAssistant访问权限

## 故障排除

### 常见问题

1. **令牌获取失败**
   - 检查配置文件是否存在
   - 验证HomeAssistant服务状态
   - 确认网络连接

2. **WebSocket连接失败**
   - 检查HomeAssistant WebSocket端口（默认8123）
   - 验证令牌有效性
   - 检查防火墙设置

3. **Channel切换失败**
   - 确认channel在有效范围内（11-26）
   - 检查ZHA/Thread服务状态
   - 查看详细错误日志

### 调试模式

启用详细日志：

```python
import logging
logging.basicConfig(level=logging.DEBUG)
``` 