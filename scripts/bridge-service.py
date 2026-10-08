#!/usr/bin/env python3
"""Run pip's bridge under the macOS user's launchd (survives closing this chat)."""
import argparse
import os
from pathlib import Path
import plistlib
import subprocess

ROOT = Path(__file__).resolve().parent.parent
LABEL = 'local.rsms.pip-bridge'
DOMAIN = f'gui/{os.getuid()}'
PLIST = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('action', choices=['install', 'restart', 'status', 'stop'])
args = parser.parse_args()
if args.action == 'install':
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    configuration = {'Label': LABEL, 'ProgramArguments': [str(ROOT / '.tools/python/bin/python'), '-u', str(ROOT / 'scripts/bridge.py')],
        'WorkingDirectory': str(ROOT), 'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 5,
        'StandardOutPath': str(ROOT / '.tools/bridge.log'), 'StandardErrorPath': str(ROOT / '.tools/bridge-error.log')}
    if PLIST.exists():
        old = plistlib.loads(PLIST.read_bytes())
        if old.get('ProgramArguments') != configuration['ProgramArguments']:
            raise SystemExit('Existing service points elsewhere; refusing to replace it')
        subprocess.run(['launchctl', 'bootout', DOMAIN, str(PLIST)], capture_output=True)
    PLIST.write_bytes(plistlib.dumps(configuration))
    subprocess.run(['launchctl', 'bootstrap', DOMAIN, str(PLIST)], check=True)
elif args.action == 'restart':
    subprocess.run(['launchctl', 'kickstart', '-k', DOMAIN + '/' + LABEL], check=True)
elif args.action == 'stop':
    subprocess.run(['launchctl', 'bootout', DOMAIN, str(PLIST)], check=True)
else:
    subprocess.run(['launchctl', 'print', DOMAIN + '/' + LABEL], check=True)
