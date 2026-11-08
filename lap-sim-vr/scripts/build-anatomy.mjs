// Convert LapGym's (sofa_env, MIT) SOFA meshes to web-friendly files:
//   *_visual.stl       -> copied as-is (binary STL, mm)
//   *_volumetric.vtk   -> *_tets.json { points: [x,y,z,...] (mm), tets: [a,b,c,d,...] }
// Usage: node scripts/build-anatomy.mjs <dir-with-lapgym-meshes>
// Source: https://github.com/ScheiklP/sofa_env/tree/main/sofa_env/scenes/grasp_lift_touch/meshes
import fs from 'node:fs';
import path from 'node:path';

const src = process.argv[2];
const out = 'client/public/assets/anatomy';
fs.mkdirSync(out, { recursive: true });

function parseVtk(buf) { // legacy VTK, BINARY (big-endian), UNSTRUCTURED_GRID
  const find = (s, from = 0) => buf.indexOf(Buffer.from(s), from);
  const lineEnd = (i) => buf.indexOf(0x0a, i);
  let i = find('POINTS'); const n = +buf.subarray(i, lineEnd(i)).toString().split(/\s+/)[1];
  let p = lineEnd(i) + 1;
  const points = []; for (let k = 0; k < 3 * n; k++) points.push(+buf.readFloatBE(p + 4 * k).toFixed(3));
  p += 12 * n;
  i = find('CELLS', p); const [, ncells] = buf.subarray(i, lineEnd(i)).toString().split(/\s+/).map(Number);
  p = lineEnd(i) + 1;
  const tets = [];
  for (let c = 0; c < ncells; c++) {
    const k = buf.readInt32BE(p); p += 4;
    const ids = []; for (let j = 0; j < k; j++) { ids.push(buf.readInt32BE(p)); p += 4; }
    if (k === 4) tets.push(...ids);
  }
  return { points, tets };
}

for (const organ of ['liver', 'gallbladder']) {
  fs.copyFileSync(path.join(src, `${organ}_visual.stl`), path.join(out, `${organ}_visual.stl`));
  const v = parseVtk(fs.readFileSync(path.join(src, `${organ}_volumetric.vtk`)));
  fs.writeFileSync(path.join(out, `${organ}_tets.json`), JSON.stringify(v));
  console.log(organ, 'points', v.points.length / 3, 'tets', v.tets.length / 4);
}
