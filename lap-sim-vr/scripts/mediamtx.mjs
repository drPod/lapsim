// Download (once) and run MediaMTX for the current OS with laptop/mediamtx.yml.
//   npm run mediamtx                 -> laptop config
//   npm run mediamtx -- pi/mediamtx.yml
import { spawn, execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const VER = process.env.MEDIAMTX_VERSION || 'v1.21.3';
const plat = { darwin: 'darwin', linux: 'linux', win32: 'windows' }[process.platform];
const arch = { x64: 'amd64', arm64: 'arm64', arm: 'armv7' }[process.arch];
if (!plat || !arch) throw new Error(`unsupported platform ${process.platform}/${process.arch}`);
const ext = plat === 'windows' ? 'zip' : 'tar.gz';
const dir = path.resolve('bin', `mediamtx-${VER}`);
const exe = path.join(dir, plat === 'windows' ? 'mediamtx.exe' : 'mediamtx');

if (!fs.existsSync(exe)) {
  fs.mkdirSync(dir, { recursive: true });
  const url = `https://github.com/bluenviron/mediamtx/releases/download/${VER}/mediamtx_${VER}_${plat}_${arch}.${ext}`;
  console.log('downloading', url);
  const res = await fetch(url);
  if (!res.ok) throw new Error(`download failed: ${res.status}`);
  const archive = path.join(os.tmpdir(), `mediamtx.${ext}`);
  fs.writeFileSync(archive, Buffer.from(await res.arrayBuffer()));
  if (ext === 'zip') execFileSync('powershell', ['-Command', `Expand-Archive -Force '${archive}' '${dir}'`], { stdio: 'inherit' });
  else execFileSync('tar', ['xzf', archive, '-C', dir], { stdio: 'inherit' });
}
const conf = path.resolve(process.argv[2] || 'laptop/mediamtx.yml');
console.log(`running ${exe} ${conf}`);
const p = spawn(exe, [conf], { stdio: 'inherit' });
p.on('exit', (code) => process.exit(code ?? 0));
for (const s of ['SIGINT', 'SIGTERM']) process.on(s, () => p.kill(s));
