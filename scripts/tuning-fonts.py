#!/usr/bin/env python3
"""Generate the Figma audio-tuning typography, including case-sensitive symbols."""
from pathlib import Path
import subprocess
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

ROOT = Path(__file__).resolve().parent.parent
FONTS = (
    ('inter_tune', 40, 500, 14, None, False),
    ('inter_tune_label', 44, 500, 32, 'RetryExitSubmitH', False),
    ('inter_tune_retry', 44, 800, 14, '↻H', True),
    ('inter_tune_exit', 60, 600, 14, '×H', True),
    ('inter_tune_check', 88, 500, 14, '✓H', True),
)
for name, size, weight, optical, symbols, case in FONTS:
    font = instantiateVariableFont(TTFont(ROOT / 'assets/fonts/InterVariable.ttf'), {'wght':weight, 'opsz':optical})
    if case:
        table = font['GSUB'].table
        for feature in table.FeatureList.FeatureRecord:
            if feature.FeatureTag != 'case':
                continue
            for index in feature.Feature.LookupListIndex:
                for subtable in table.LookupList.Lookup[index].SubTable:
                    mapping = getattr(subtable, 'mapping', {})
                    for cmap in font['cmap'].tables:
                        if cmap.isUnicode():
                            for cp, glyph in list(cmap.cmap.items()):
                                cmap.cmap[cp] = mapping.get(glyph, glyph)
    path = ROOT / '.tools' / (name + '.ttf')
    font.save(path)
    output = ROOT / 'components/pip_board' / (name + '.c')
    args = [str(ROOT / '.tools/font-converter/node_modules/.bin/lv_font_conv'), '--font', str(path),
        '--size', str(size), '--bpp', '4', '--format', 'lvgl', '--no-compress', '--lv-font-name', name, '-o', str(output)]
    args += ['--symbols', symbols] if symbols else ['--range', '0x20-0x7e', '--symbols', '’']
    subprocess.run(args, check=True)
    output.write_text(output.read_text().replace(str(ROOT) + '/', '').rstrip() + '\n')
