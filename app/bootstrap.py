"""无控制台启动入口；启动故障写入本机诊断文件并显示提示。"""
from pathlib import Path
import json
import os
import sys
import traceback


def main():
    from desktop import App, ensure_application
    application = ensure_application()
    guard = None
    if '--check' not in sys.argv:
        from bili_service import DATA_DIR
        from single_instance import SingleInstance
        guard = SingleInstance(DATA_DIR, application)
        if not guard.acquire_or_activate():
            return
        application.aboutToQuit.connect(guard.close)
    app = App(check_online='--check' not in sys.argv, migrate_folders='--check' not in sys.argv,
              enable_tray='--check' not in sys.argv)
    if guard:
        guard.bind(app.restore_window)
    app.show()
    if '--check' in sys.argv:
        application.processEvents()
        info = {'window': app.windowTitle(), 'size': f'{app.width()}x{app.height()}',
                'tabs': app.tabs.count(), 'layout': 'three-pane', 'history_count': app.history_list.count(), 'ok': True}
        app.close()
        output = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'BiliNote/startup-check.json'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(info, ensure_ascii=False), encoding='utf-8')
    else:
        try:
            raise SystemExit(application.exec())
        finally:
            guard.close()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        folder = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'BiliNote'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'startup-error.log').write_text(traceback.format_exc(), encoding='utf-8')
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror('Bili Note 无法启动', '启动失败，诊断信息已保存到：\n' + str(folder / 'startup-error.log'))
            root.destroy()
        finally:
            raise SystemExit(1)
