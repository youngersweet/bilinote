"""复用本地无控制台启动方式创建桌面快捷方式。"""
from pathlib import Path
import subprocess

from bili_service import CREATE_NO_WINDOW


def create_desktop_shortcut():
    script = Path(__file__).resolve().parents[1] / '创建桌面快捷方式.vbs'
    if not script.is_file():
        raise RuntimeError('没有找到快捷方式创建工具。')
    result = subprocess.run(['cscript.exe', '//nologo', str(script)], capture_output=True,
                            timeout=20, creationflags=CREATE_NO_WINDOW)
    if result.returncode != 0:
        raise RuntimeError('桌面快捷方式创建失败，请检查桌面访问权限和软件文件是否完整。')
    return '桌面快捷方式已创建，可以直接打开 Bili Note。'
