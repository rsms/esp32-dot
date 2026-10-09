// Build-time only: the firmware embeds the resulting alpha masks.
const {Resvg} = require('../.tools/font-converter/node_modules/@resvg/resvg-js');
const fs = require('fs');
// resvg does not support CSS Color 4 display-p3 declarations. Figma exports
// equivalent sRGB attributes and earlier declarations; retain those fallbacks.
let svg = fs.readFileSync(process.argv[2], 'utf8')
    .replace(/(?:fill|stroke):color\(display-p3[^)]*\);?/g, '');
if (process.argv[4] === 'recording-frame') {
    // Apply the Figma parent frame's rounded clip at build time. The source
    // asset stays unchanged, at its original size and position inside the clip.
    svg = `<svg xmlns="http://www.w3.org/2000/svg" width="242" height="202" viewBox="0 0 242 202"><defs><clipPath id="screen"><rect x="9" y="9" width="224" height="184" rx="28"/></clipPath></defs><g clip-path="url(#screen)">${svg}</g></svg>`;
}
const image = new Resvg(svg, {fitTo: {mode: 'width', value: Number(process.argv[3])}}).render();
process.stdout.write(image.asPng());
