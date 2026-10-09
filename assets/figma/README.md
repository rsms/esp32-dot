# Figma v1 source assets

Original SVG exports from the local Figma MCP for
[pip-esp32](https://www.figma.com/design/76QFDYtjdgU9P8IXpHycwz/pip-esp32),
2026-10-08. Only the flow in the user's 13:45 screenshot is v1:
sleeping `2:834`, listening `2:849`, idle `2:869`, thinking `2:875`,
attention `2:603`, message pages `2:699`, `2:723`, `2:728`,
dismissal `2:914`, and errors `2:961`, `2:966`, `2:976`.
Other frames are WIP and must not be implemented without a new instruction.

The user also approved reply choices in the 15:37 screenshot, group `4:1568`:
question pages `4:1163` and `4:1172`, choice A `4:1181`, confirmation A `4:1318`,
and the corresponding B/C pages in that group. These share the message color
and type. Choice cards are 200×128 at (12,12), radius 16; confirmation circles
are 96×96 at (64,28), with 11 px SemiBold "Confirm" and a 44 px checkmark.
Choice markers are 16.5 px Bold within 18 px circles; everything renders at 2×.
The hollow/filled 12 px page-dot SVGs are saved locally and embedded as A8 masks.

Frames are 224 × 184; render at 2× for the 448 × 368 display.
Message text is Inter Variable Medium 27.5 px, line height 32 px,
optical size 27.5, with cap-height trimming, 20 px side/top padding. Colors are #0028b9
(message), #a44200 (error), #ff472a (listening), #ffd900 (attention).
The frame corner radius is 28 px. Faces occupy 120 × 120 at (52, 32).

`scripts/design-assets.py` rasterizes these source vectors using resvg 2.6.2
and emits LVGL A8 masks. White/black color comes from the renderer. Sound
waves use the Figma instance's mirrored transform. No screenshot is used as
an implementation asset. Rebuild with:

```sh
.tools/python/bin/python scripts/design-assets.py
```

Generated assets and font C files are checked in; normal firmware builds
need neither Figma nor the asset conversion dependencies.


Audio tuning was approved in the 18:34 screenshot, group `5:99`: ready `5:23`,
recording `5:46`, and review `5:63`. Phrase text is Medium 20 px, optical size 14,
line height 24 px, cap-trimmed at (20,20), width 184. The recording frame and dot
are the original SVG `962877912cb75d4c1261f5d702ca54234f78bb22.svg`, 242×202,
positioned at (-9,-9) and clipped by the screen. Color is #ff472a.

Review buttons: Retry (12,12,94,74), Exit (12,98,94,74), Submit (118,12,94,160),
radius 16. Labels are Medium 22 px, optical size 32. Retry uses ↻ at 22 px
ExtraBold, Exit uses × at 30 px SemiBold, both with `case` enabled; Submit uses
a 44 px Medium checkmark at optical size 14. All dimensions render at 2×. Rebuild tuning
type with `scripts/tuning-fonts.py`; other screens' existing fonts are unchanged.
