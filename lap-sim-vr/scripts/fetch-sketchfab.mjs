// Download the Sketchfab models listed in assets-src/or-models.json (GLB) into a cache dir.
//   SKETCHFAB_API_TOKEN=... node scripts/fetch-sketchfab.mjs [outDir=assets-src/sketchfab]
// The token is read from the environment and never written anywhere.
import fs from 'node:fs';
const token = process.env.SKETCHFAB_API_TOKEN;
if (!token) { console.error('SKETCHFAB_API_TOKEN not set'); process.exit(1); }
const out = process.argv[2] || 'assets-src/sketchfab';
fs.mkdirSync(out, { recursive: true });
const { models } = JSON.parse(fs.readFileSync('assets-src/or-models.json', 'utf8'));
for (const m of models) {
  const file = `${out}/${m.name}.glb`;
  if (fs.existsSync(file)) { console.log('cached', file); continue; }
  const r = await fetch(`https://api.sketchfab.com/v3/models/${m.uid}/download`, { headers: { Authorization: `Token ${token}` } }).then((x) => x.json());
  const url = r.glb?.url || r.gltf?.url;
  if (!url) { console.error(m.name, 'no download', JSON.stringify(r).slice(0, 200)); continue; }
  fs.writeFileSync(file, Buffer.from(await (await fetch(url)).arrayBuffer()));
  console.log('downloaded', file);
}
