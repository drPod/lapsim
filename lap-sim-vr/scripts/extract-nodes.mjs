// Extract named nodes (and their subtrees) from a GLB into a new GLB.
//   node scripts/extract-nodes.mjs in.glb out.glb NodeName [NodeName...]
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { prune } from '@gltf-transform/functions';
const [, , input, output, ...names] = process.argv;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS);
const doc = await io.read(input);
const keep = new Set();
doc.getRoot().listNodes().forEach((n) => { if (names.includes(n.getName())) n.traverse((c) => keep.add(c)); });
// keep ancestors (for transforms) but drop their meshes and other children
const ancestors = new Set();
for (const n of keep) { let p = n.getParentNode(); while (p) { ancestors.add(p); p = p.getParentNode(); } }
for (const n of doc.getRoot().listNodes()) if (!keep.has(n) && !ancestors.has(n)) n.dispose();
await doc.transform(prune());
await io.write(output, doc);
console.log('wrote', output, 'nodes', keep.size);
