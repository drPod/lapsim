// Writes client/public/assets/credits.json (shown at /credits.html) from assets-src/or-models.json
// plus the non-Sketchfab assets.  node scripts/write-credits.mjs
import fs from 'node:fs';
const { models } = JSON.parse(fs.readFileSync('assets-src/or-models.json', 'utf8'));
const seen = new Set();
const credits = models.filter((m) => !seen.has(m.uid + m.title) && seen.add(m.uid + m.title)).map((m) => ({
  what: m.title, author: m.author, license: m.license, link: `https://sketchfab.com/3d-models/${m.uid}`, use: m.name, note: m.extractedFrom ? 'parts extracted from the full room model' : undefined,
}));
credits.push(
  { what: 'Liver + gallbladder meshes (virtual abdomen)', author: 'Paul Maria Scheikl et al. (LapGym / sofa_env)', license: 'MIT', link: 'https://github.com/ScheiklP/sofa_env', use: 'abdomen' },
  { what: '"Surgery" HDRI (reflections / lighting)', author: 'Oliksiy Yakovlyev, Poly Haven', license: 'CC0', link: 'https://polyhaven.com/a/surgery', use: 'environment' },
  { what: 'Demo laparoscope video', author: 'Boer J, Boerma D, de Vries Reilingh T (J Med Case Reports 2011)', license: 'CC BY 2.0', link: 'https://commons.wikimedia.org/wiki/File:A-gallbladder-torsion-presenting-as-acute-cholecystitis-in-an-elderly-woman-A-case-report-1752-1947-5-588-S1.ogv', use: 'demo feed' },
  { what: 'three.js', author: 'three.js authors', license: 'MIT', link: 'https://threejs.org', use: 'engine' },
  { what: 'IWER / @iwer/devui', author: 'Meta', license: 'MIT', link: 'https://github.com/meta-quest/immersive-web-emulation-runtime', use: 'testing' },
  { what: 'MediaMTX reader.js / publisher.js', author: 'bluenviron', license: 'MIT', link: 'https://github.com/bluenviron/mediamtx', use: 'video' },
);
fs.writeFileSync('client/public/assets/credits.json', JSON.stringify(credits, null, 1) + '\n');
console.log(credits.length, 'credits');
