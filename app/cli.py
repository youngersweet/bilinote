"""与桌面界面共用加密登录状态的命令行入口。"""
import argparse
import json
from pathlib import Path
import time
import qrcode

from bili_service import BiliClient, DATA_DIR, summarize
from content_pipeline import collect_video
from model_settings import ModelSettings, load_settings, save_settings, model_catalog, check_connection
from note_library import load_preferences


def main():
    parser = argparse.ArgumentParser(description='Bili Note：加密登录、字幕提取与总结')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('status')
    login = sub.add_parser('login')
    login.add_argument('--qr', default=str(DATA_DIR / 'login-current.png'))
    extract = sub.add_parser('extract')
    extract.add_argument('source')
    extract.add_argument('--out', default=load_preferences()['output_dir'])
    extract.add_argument('--summarize', action='store_true')
    extract.add_argument('--mode', choices=('auto', 'subtitles', 'browser', 'audio'), default='auto')
    extract.add_argument('--no-visual', action='store_true')
    summary = sub.add_parser('summarize')
    summary.add_argument('result', help='已导出的video.json')
    for command in (extract, summary):
        command.add_argument('--model', help='单次指定模型，不改变保存的配置')
        command.add_argument('--reasoning', help='单次指定推理强度，如 medium 或 high')
    settings_parser = sub.add_parser('settings', help='独立模型配置')
    settings_parser.add_argument('--model')
    settings_parser.add_argument('--reasoning')
    settings_parser.add_argument('--save', action='store_true')
    settings_parser.add_argument('--check', action='store_true', help='短文本测试，会消耗少量额度')
    settings_parser.add_argument('--list', action='store_true', help='显示本机缓存模型目录，不代表已验证账户可用')
    args = parser.parse_args()
    def selected_settings():
        if args.model and args.reasoning:
            return ModelSettings(args.model, args.reasoning)
        saved = load_settings()
        return ModelSettings(args.model or saved.model, args.reasoning or saved.reasoning_effort)
    if args.action == 'settings':
        if args.list:
            print(json.dumps(model_catalog(), ensure_ascii=False, indent=2))
            return
        settings = selected_settings()
        if args.save:
            save_settings(settings)
        record = check_connection(settings) if args.check else settings.to_dict()
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return
    pinned = selected_settings() if args.action == 'summarize' or (args.action == 'extract' and args.summarize) else None
    client = BiliClient()
    if args.action == 'status':
        status = client.status()
        print(json.dumps({'logged_in': status['logged_in'], 'saved': status['saved']}, ensure_ascii=False))
    elif args.action == 'login':
        if client.status()['logged_in']:
            print('SAVED_SESSION_VALID')
            return
        ticket = client.begin_login()
        path = Path(args.qr)
        path.parent.mkdir(parents=True, exist_ok=True)
        qrcode.make(ticket['url']).save(path)
        print('SCAN_QR: ' + str(path), flush=True)
        while True:
            status = client.poll_login(ticket)
            if status['state'] == 'confirmed':
                print('LOGIN_SAVED_ENCRYPTED', flush=True)
                return
            if status['state'] == 'expired':
                raise RuntimeError('二维码已过期，请重新登录。')
            print('AWAITING_PHONE_CONFIRMATION' if status['state'] == 'confirm' else 'WAITING_FOR_SCAN', flush=True)
            time.sleep(3)
    elif args.action == 'extract':
        result = collect_video(args.source, args.out, client, mode=args.mode, visual=not args.no_visual,
                               progress=lambda text: print(text, flush=True))
        print(json.dumps({'title': result['title'], 'folder': result['folder'], 'complete': result['complete'],
                          'pages': len(result['pages']), 'missing': result['missing']}, ensure_ascii=False), flush=True)
        if args.summarize:
            print(summarize(result, lambda text: print(text, flush=True), settings=pinned))
    elif args.action == 'summarize':
        result = json.loads(Path(args.result).read_text(encoding='utf-8'))
        print(summarize(result, lambda text: print(text, flush=True), settings=pinned))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('ERROR: ' + str(error))
        raise SystemExit(1)
        raise SystemExit(1)
