# maintainer: guoping.liu@3reality.com
"""
Supervisor命令行模块
提供统一的命令注册和处理机制，方便扩展新命令
"""

import sys
import json
from typing import Dict, Callable, Optional, List
from .cli import SupervisorClient
from .const import VERSION, DEVICE_BUILD_NUMBER


class CommandHandler:
    """命令处理器基类"""
    
    def __init__(self, name: str, description: str, requires_arg: bool = False):
        self.name = name
        self.description = description
        self.requires_arg = requires_arg
    
    def execute(self, arg: Optional[str] = None) -> int:
        """
        执行命令
        
        Args:
            arg: 命令参数
            
        Returns:
            退出码 (0表示成功，非0表示失败)
        """
        raise NotImplementedError("Subclass must implement execute method")
    
    def get_usage(self) -> str:
        """返回命令使用说明"""
        if self.requires_arg:
            return f"Usage: supervisor {self.name} <parameter>"
        return f"Usage: supervisor {self.name}"


class DaemonCommand(CommandHandler):
    """守护进程命令"""
    
    def __init__(self):
        super().__init__('daemon', 'Run supervisor as daemon service', requires_arg=False)
    
    def execute(self, arg: Optional[str] = None) -> int:
        # 延迟导入以避免循环导入
        import supervisor.supervisor as supervisor_module
        
        supervisor = supervisor_module.Supervisor()
        try:
            supervisor.run()
        except KeyboardInterrupt:
            supervisor.cleanup()
            supervisor_module.logger.info("Supervisor terminated by user")
            return 0
        except Exception as e:
            supervisor_module.logger.error(f"Unhandled exception: {e}")
            supervisor.cleanup()
            return 1


class LedCommand(CommandHandler):
    """LED控制命令"""
    
    def __init__(self):
        super().__init__('led', 'Control LED', requires_arg=True)
    
    def execute(self, arg: Optional[str] = None) -> int:
        if arg is None:
            print("Usage: supervisor led <arg>")
            print("Supported: on|off|clear, colors [red|blue|yellow|green|white|cyan|magenta|purple], "
                  "states [reboot|startup|factory_reset|sys_normal_operation|...]")
            return 1
        
        try:
            print(f"[Main]input led arg: {arg}")
            client = SupervisorClient()
            resp = client.send_command("led", arg, "Led command")
            if resp:
                print(resp)
            return 0
        except Exception as e:
            print(f"Error sending LED command: {e}")
            return 1
    
    def get_usage(self) -> str:
        return "Usage: supervisor led <arg>\n" \
               "Supported: on|off|clear, colors [red|blue|yellow|green|white|cyan|magenta|purple], " \
               "states [reboot|startup|factory_reset|sys_normal_operation|...]"


class ClientCommand(CommandHandler):
    """通过客户端发送的命令基类"""
    
    def __init__(self, name: str, description: str, cmd_type: str, 
                 requires_arg: bool = True, special_handlers: Optional[Dict[str, Callable]] = None):
        super().__init__(name, description, requires_arg)
        self.cmd_type = cmd_type
        self.special_handlers = special_handlers or {}
    
    def execute(self, arg: Optional[str] = None) -> int:
        if self.requires_arg and arg is None:
            print(self.get_usage())
            return 1
        
        try:
            client = SupervisorClient()
            response = client.send_command(self.cmd_type, arg, f"{self.name} command")
            
            if response is None:
                print(f"Error: Failed to send {self.name} command")
                return 1
            
            # 对于ptest命令，响应处理在客户端完成
            if self.cmd_type == 'ptest':
                return 0
            
            # 处理特殊响应格式
            if arg in self.special_handlers:
                self.special_handlers[arg](response)
            elif arg == 'info':
                # 默认info命令处理：尝试解析并美化JSON输出
                try:
                    json_data = json.loads(response)
                    print(json.dumps(json_data, indent=2, ensure_ascii=False))
                except (json.JSONDecodeError, TypeError):
                    print(response)
            else:
                print(f"{self.name.capitalize()} command sent successfully: {arg}")
            
            return 0
        except Exception as e:
            print(f"Error sending {self.name} command: {e}")
            return 1


class ZigbeeCommand(ClientCommand):
    """Zigbee命令"""
    
    def __init__(self):
        super().__init__(
            'zigbee', 
            'Zigbee control commands', 
            'zigbee',
            requires_arg=True
        )
    
    def get_usage(self) -> str:
        return "Usage: supervisor zigbee <parameter>\n" \
               "Examples: supervisor zigbee info"


class ThreadCommand(ClientCommand):
    """Thread命令"""
    
    def __init__(self):
        super().__init__(
            'thread',
            'Thread network control commands',
            'thread',
            requires_arg=True
        )
    
    def get_usage(self) -> str:
        return "Usage: supervisor thread <parameter>\n" \
               "Examples: supervisor thread info"


class SettingCommand(ClientCommand):
    """设置命令"""
    
    def __init__(self):
        super().__init__(
            'setting',
            'System settings commands',
            'setting',
            requires_arg=True
        )
    
    def get_usage(self) -> str:
        return "Usage: supervisor setting <parameter>"


class PtestCommand(ClientCommand):
    """生产测试命令"""
    
    def __init__(self):
        super().__init__(
            'ptest',
            'Production test commands',
            'ptest',
            requires_arg=True
        )
    
    def get_usage(self) -> str:
        return "Usage: supervisor ptest <mode>\n" \
               "Available modes: start, finish, restore"


class OtaCommand(ClientCommand):
    """OTA更新命令"""
    
    def __init__(self):
        super().__init__(
            'ota',
            'OTA update commands',
            'ota',
            requires_arg=True
        )
    
    def get_usage(self) -> str:
        return "Usage: supervisor ota <parameter>\n" \
               "Supported parameters:\n" \
               "  - bridge: Upgrade bridge package\n" \
               "  - z2m: Upgrade zigbee-mqtt package"
    
    def execute(self, arg: Optional[str] = None) -> int:
        """Override execute to check for error responses"""
        if self.requires_arg and arg is None:
            print(self.get_usage())
            return 1
        
        try:
            client = SupervisorClient()
            response = client.send_command(self.cmd_type, arg, f"{self.name} command")
            
            if response is None:
                print(f"Error: Failed to send {self.name} command")
                return 1
            
            # Check if response indicates an error
            response_lower = response.lower() if isinstance(response, str) else ""
            if "unknown" in response_lower or "failed" in response_lower or "error" in response_lower:
                print(response)
                return 1
            
            # Success response
            print(response)
            return 0
        except Exception as e:
            print(f"Error sending {self.name} command: {e}")
            return 1


class CommandRegistry:
    """命令注册表"""
    
    def __init__(self):
        self._commands: Dict[str, CommandHandler] = {}
        self._register_default_commands()
    
    def _register_default_commands(self):
        """注册默认命令"""
        default_commands = [
            DaemonCommand(),
            LedCommand(),
            ZigbeeCommand(),
            ThreadCommand(),
            SettingCommand(),
            PtestCommand(),
            OtaCommand(),
        ]
        
        for cmd in default_commands:
            self.register(cmd)
    
    def register(self, handler: CommandHandler):
        """注册命令处理器"""
        self._commands[handler.name] = handler
    
    def get(self, name: str) -> Optional[CommandHandler]:
        """获取命令处理器"""
        return self._commands.get(name)
    
    def list_commands(self) -> List[str]:
        """列出所有已注册的命令"""
        return sorted(self._commands.keys())
    
    def get_all_commands(self) -> Dict[str, CommandHandler]:
        """获取所有命令"""
        return self._commands.copy()


# 全局命令注册表实例
_registry = CommandRegistry()


def get_registry() -> CommandRegistry:
    """获取全局命令注册表"""
    return _registry


def register_command(handler: CommandHandler):
    """注册新命令的便捷函数"""
    _registry.register(handler)


def execute_command(command: str, arg: Optional[str] = None) -> int:
    """
    执行命令
    
    Args:
        command: 命令名称
        arg: 命令参数
        
    Returns:
        退出码
    """
    handler = _registry.get(command)
    if handler is None:
        print(f"Unknown command: {command}")
        print(f"Available commands: {', '.join(_registry.list_commands())}")
        return 1
    
    return handler.execute(arg)


def show_version():
    """显示版本信息"""
    print(f"Supervisor {VERSION} ({DEVICE_BUILD_NUMBER})")

