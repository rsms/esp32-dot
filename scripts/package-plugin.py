#!/usr/bin/env python3
"""Build a portable local plugin with a real skill directory (no packaged symlink)."""
import json
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parent.parent
output = ROOT / '.tools/rsms-dot-plugin'
output.mkdir(parents=True, exist_ok=True)
shutil.copy2(ROOT / 'plugins/rsms-dot/plugin.json', output / 'plugin.json')
shutil.copytree(ROOT / 'skills/rsms-dot', output / 'skills/rsms-dot', dirs_exist_ok=True)
# The stdio adapter is tied to this host. Cloud imports the same skill via MCP.
(output / 'mcp.json').write_text(json.dumps({
    '$schema': 'https://agent-plugins.org/schemas/1.0.0/mcp.schema.json',
    'mcpServers': {'rsms-dot': {'type': 'stdio',
        'command': str(ROOT / '.tools/python/bin/python'),
        'args': [str(ROOT / 'scripts/mcp-stdio.py')]}}
}, indent=4) + '\n')
archive = ROOT / '.tools/rsms-dot-plugin.zip'
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zip_file:
    for path in sorted(output.rglob('*')):
        if path.is_file():
            zip_file.write(path, path.relative_to(output))
print(output)
print(archive)
