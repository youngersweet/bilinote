"""Offline installation checks without login data or model requests."""
import importlib
from pathlib import Path
import sys


def main():
    failed = []
    for name in ('PySide6.QtWidgets', 'PIL', 'qrcode', 'websocket', 'imageio_ffmpeg', 'yt_dlp', 'av', 'faster_whisper'):
        try:
            importlib.import_module(name)
            print('OK  ' + name)
        except Exception as error:
            failed.append(name)
            print('FAIL ' + name + ': ' + str(error))
    from content_pipeline import skill_instructions, SKILL_DIR
    try:
        skill_instructions()
        for name in ('fetch_browser_ai_subtitles.py', 'edge_cdp.py', 'extract_bilibili.py', 'extract_video_keyframes.py', 'archive_bili_materials.py', 'score_bili_note.py'):
            if not (SKILL_DIR / 'scripts' / name).is_file():
                raise FileNotFoundError(name)
        import imageio_ffmpeg
        if not Path(imageio_ffmpeg.get_ffmpeg_exe()).is_file():
            raise FileNotFoundError('FFmpeg')
        print('OK  bundled resources and FFmpeg')
    except Exception as error:
        failed.append('resources')
        print('FAIL resources: ' + str(error))
    from bili_service import codex_command
    try:
        codex_command()
        print('OK  Codex found; test your model in Settings before summarizing.')
    except RuntimeError:
        print('OPTIONAL: Install and log in to Codex for AI summaries. Subtitle extraction remains available.')
    return bool(failed)


if __name__ == '__main__':
    sys.exit(main())
