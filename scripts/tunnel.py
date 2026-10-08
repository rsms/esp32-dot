#!/usr/bin/env python3
"""Install, configure, start, or inspect pip's official Secure MCP Tunnel client."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
CLIENT = ROOT / '.tools/tunnel-client/tunnel-client'
SETTINGS = ROOT / '.tools/tunnel-settings.json'
KEY = ROOT / '.tools/tunnel-runtime-key'
PROFILES = ROOT / '.tools/tunnel-profiles'


def private_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as file:
        os.fchmod(file.fileno(), 0o600)
        file.write(value)


def install():
    release = json.load(urllib.request.urlopen('https://api.github.com/repos/openai/tunnel-client/releases/latest', timeout=30))
    system = {'Darwin': 'darwin', 'Linux': 'linux'}[platform.system()]
    arch = {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'amd64'}[platform.machine()]
    name = f'tunnel-client-{release["tag_name"]}-{system}-{arch}.zip'
    assets = {a['name']: a['browser_download_url'] for a in release['assets']}
    checksums = urllib.request.urlopen(assets['SHA256SUMS.txt'], timeout=30).read().decode()
    data = urllib.request.urlopen(assets[name], timeout=60).read()
    expected = next(line.split()[0] for line in checksums.splitlines() if line.split()[-1].lstrip('*') == name)
    if hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError('Tunnel client checksum mismatch')
    archive = ROOT / '.tools/tunnel-client.zip'
    archive.write_bytes(data)
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            if Path(item.filename).is_absolute() or '..' in Path(item.filename).parts:
                raise RuntimeError('Unsafe release archive path')
        package.extractall(CLIENT.parent)
    CLIENT.chmod(0o755)
    bundled = CLIENT.parent / 'cloudflared'
    if bundled.exists():
        bundled.chmod(0o755)
    print('Installed official tunnel-client', release['tag_name'])


def start():
    settings = json.loads(SETTINGS.read_text())
    command = shlex.join([str(ROOT / '.tools/python/bin/python'), str(ROOT / 'scripts/mcp-stdio.py')])
    subprocess.run([str(CLIENT), 'runtimes', 'connect', '--alias', 'rsms-dot', '--profile', 'rsms-dot',
        '--profile-dir', str(PROFILES), '--tunnel-id', settings['tunnel_id'],
        '--runtime-api-key', 'file:' + str(KEY), '--mcp-command', command], check=True)
    status()


def status():
    result = subprocess.run([str(CLIENT), 'runtimes', 'status', 'rsms-dot', '--json'],
        check=True, capture_output=True, text=True)
    value = json.loads(result.stdout)
    # The full runtime response includes long logs; keep routine checks concise.
    print(json.dumps({key: value.get(key) for key in (
        'alias', 'tunnel_id', 'process_running', 'healthy', 'ready', 'runtime_state', 'error', 'ui_url')}, indent=4))
    if not all(value.get(key) for key in ('process_running', 'healthy', 'ready')):
        raise SystemExit('Tunnel is not ready. Run scripts/tunnel.py doctor for diagnostics.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install-client', 'configure', 'start', 'status', 'doctor'])
    args = parser.parse_args()
    if args.action == 'install-client':
        install()
        return
    if not CLIENT.exists():
        parser.error('First run: python3 scripts/tunnel.py install-client')
    if args.action == 'configure':
        print('Create a tunnel and associate your ChatGPT workspace at:')
        print('https://platform.openai.com/settings/organization/tunnels')
        print('The runtime key needs Tunnels Read + Use. Do not use an admin key.')
        tunnel_id = input('Tunnel ID: ').strip()
        if not re.fullmatch(r'tunnel_[A-Za-z0-9_-]+', tunnel_id):
            parser.error('Expected a tunnel_… identifier')
        key = getpass.getpass('Runtime API key (hidden): ').strip()
        if not key or any(c.isspace() for c in key):
            parser.error('Expected a nonempty runtime key without whitespace')
        private_write(KEY, key + '\n')
        private_write(SETTINGS, json.dumps({'tunnel_id': tunnel_id}, indent=4) + '\n')
        start()
    elif args.action == 'start':
        start()
    elif args.action == 'status':
        status()
    elif args.action == 'doctor':
        subprocess.run([str(CLIENT), 'doctor', '--profile', 'rsms-dot', '--profile-dir', str(PROFILES), '--explain'], check=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit('Tunnel setup incomplete: ' + type(error).__name__ + '. Check the client diagnostics above.')
