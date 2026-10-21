"""Build the web UI stylesheet (static/tailwind.css) with the Tailwind CSS standalone CLI.

The official standalone executable (no Node.js needed) is downloaded once, checked against the
release's SHA-256 and kept in .cache/ (or TAILWIND_CACHE). Development tool only: the server
itself never needs it.

Usage:
    python tools/tailwind.py                 build static/tailwind.css (minified)
    python tools/tailwind.py --watch         rebuild on every change of static/ (add --poll in Docker)
    python tools/tailwind.py --version-only  print the pinned version

Extra arguments are passed to the Tailwind CLI.
"""
import hashlib
import os
import platform
import stat
import subprocess
import sys
import urllib.request

VERSION = '3.4.19'
RELEASE_URL = f'https://github.com/tailwindlabs/tailwindcss/releases/download/v{VERSION}/'
# From the release's sha256sums.txt
SHA256 = {
    'tailwindcss-linux-arm64': 'e5b2d27694daa80cc52ec29553ba2c6bd43d86bd51a9d633ed24058b9c05a676',
    'tailwindcss-linux-armv7': 'e3610b109a64720295e1c00a18dd2d6d79d3cddc618219aa0830de97a55429a4',
    'tailwindcss-linux-x64': '4af3198c015616ea7d6617974ec3d70d987ecc00c1ca8463b0a30fd65cc7c06e',
    'tailwindcss-macos-arm64': '7fdeb00818b6214a337383063282b2361ecb08bbc08f8c8a7ba97ee1e2eaa4fe',
    'tailwindcss-macos-x64': 'a597f407e0f1f03535731f5b42f1576a8152cb5fffc2f38e754722bc0c280045',
    'tailwindcss-windows-arm64.exe': 'f2b6b999747aa0ae31999d59db117b1ba1e4e15e17675d7108e30aac4b680686',
    'tailwindcss-windows-x64.exe': 'a15158c4c5e0e7a75f7229bfe4986fe7710d2edc468b6f96c8981f78ab211347',
}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_ARGS = [
    '-c', os.path.join(ROOT, 'tailwind.config.js'),
    '-i', os.path.join(ROOT, 'styles', 'tailwind.css'),
    '-o', os.path.join(ROOT, 'static', 'tailwind.css'),
    '--minify',
]


def asset_name() -> str:
    """Release asset of the standalone CLI for this machine."""
    system = {'Linux': 'linux', 'Darwin': 'macos', 'Windows': 'windows'}.get(platform.system())
    machine = platform.machine().lower()
    arch = {'x86_64': 'x64', 'amd64': 'x64', 'aarch64': 'arm64', 'arm64': 'arm64', 'armv7l': 'armv7'}.get(machine)
    name = f'tailwindcss-{system}-{arch}' + ('.exe' if system == 'windows' else '')
    if name not in SHA256:
        sys.exit(f'No Tailwind CSS standalone CLI for {platform.system()} {platform.machine()}')
    return name


def executable() -> str:
    """Path of the standalone CLI, downloaded and checked on first use."""
    name = asset_name()
    cache = os.environ.get('TAILWIND_CACHE') or os.path.join(ROOT, '.cache', 'tailwindcss')
    path = os.path.join(cache, VERSION, name)
    if os.path.exists(path):
        return path
    print(f'Downloading Tailwind CSS {VERSION} ({name})…', file=sys.stderr)
    with urllib.request.urlopen(RELEASE_URL + name, timeout=120) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != SHA256[name]:
        sys.exit(f'Checksum mismatch for {name}: download refused')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    partial = path + '.part'
    with open(partial, 'wb') as f:
        f.write(data)
    os.chmod(partial, os.stat(partial).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    os.replace(partial, path)
    return path


def main(args: list[str]) -> int:
    """Run the Tailwind CLI on the project's stylesheet, extra arguments passed through."""
    if args == ['--version-only']:
        print(VERSION)
        return 0
    try:
        return subprocess.call([executable(), *BUILD_ARGS, *args], cwd=ROOT)
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
