// Optimise the Sketchfab OR models for Quest: simplify (meshoptimizer), join meshes by
// material, KTX2/Basis textures <= 1024 px, meshopt geometry compression.
//   node scripts/build-or-models.mjs [srcDir=assets-src/sketchfab]
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { weld, simplify, dedup } from '@gltf-transform/functions';
import { MeshoptSimplifier } from 'meshoptimizer';

// Some models have per-face normals that prevent welding (and so simplification).
// Drop normals, weld, simplify here; the viewer recomputes smooth normals on load.
async function dropNormalsSimplify(input, output, ratio, error) {
  const io = new NodeIO().registerExtensions(ALL_EXTENSIONS);
  const doc = await io.read(input);
  for (const p of doc.getRoot().listMeshes().flatMap((m) => m.listPrimitives())) p.setAttribute('NORMAL', null);
  await doc.transform(dedup(), weld({ tolerance: 0.0005 }), simplify({ simplifier: MeshoptSimplifier, ratio, error, lockBorder: false }));
  await io.write(output, doc);
}
const src = process.argv[2] || 'assets-src/sketchfab';
const out = 'client/public/assets/or';
fs.mkdirSync(out, { recursive: true });
const { models } = JSON.parse(fs.readFileSync('assets-src/or-models.json', 'utf8'));
const only = process.argv.slice(3);
for (const m of models) {
  if (only.length && !only.includes(m.name)) continue;
  let input = `${src}/${m.name}.glb`, ratio = m.ratio;
  if (m.dropNormals) { const tmp = `${os.tmpdir()}/${m.name}-dn.glb`; await dropNormalsSimplify(input, tmp, m.ratio, m.error ?? 0.02); input = tmp; ratio = 1; }
  const args = ['gltf-transform', 'optimize', input, `${out}/${m.name}.glb`,
    '--compress', 'meshopt', '--texture-compress', 'ktx2', '--texture-size', String(m.textureSize || 1024),
    '--simplify-ratio', String(ratio), '--simplify-error', String(m.error ?? 0.002), '--join', 'true', '--flatten', 'true', '--simplify-lock-border', String(!!m.lockBorder)];
  execFileSync('npx', args, { stdio: ['ignore', 'ignore', 'inherit'] });
  console.log(m.name, (fs.statSync(`${out}/${m.name}.glb`).size / 1024).toFixed(0), 'KB');
}
