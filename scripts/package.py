"""Build a source ZIP using an allowlist, excluding personal data."""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOP = ('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'CHANGELOG.md', 'requirements.txt', '.gitignore', '.gitattributes', 'install.cmd', 'start.cmd', '启动BiliNote.vbs', '创建桌面快捷方式.vbs')


def release_files(root=ROOT):
    paths = [root / name for name in TOP]
    for folder in ('app', 'resources', 'scripts', 'docs', '.github'):
        for path in (root / folder).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and (path.name == 'LICENSE' or path.suffix in ('.py', '.md', '.txt', '.png', '.svg', '.ico', '.yml')):
                paths.append(path)
    return sorted(set(paths))


def main():
    output = ROOT / 'dist/BiliNote-windows-source.zip'
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in release_files():
            archive.write(path, Path('BiliNote') / path.relative_to(ROOT))
    print(output)


if __name__ == '__main__':
    main()
