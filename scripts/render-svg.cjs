// Build-time only: the firmware embeds the resulting alpha masks.
const {Resvg} = require('../.tools/font-converter/node_modules/@resvg/resvg-js');
const fs = require('fs');
const svg = fs.readFileSync(process.argv[2], 'utf8');
const image = new Resvg(svg, {fitTo: {mode: 'width', value: Number(process.argv[3])}}).render();
process.stdout.write(image.asPng());
