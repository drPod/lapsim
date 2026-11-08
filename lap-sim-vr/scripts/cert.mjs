// Generate a self-signed cert for `HTTPS=1 npm start` covering localhost and this
// machine's LAN IPs. Prefer mkcert if you can install its CA (see README).
import fs from 'node:fs';
import os from 'node:os';
import selfsigned from 'selfsigned';

const ips = Object.values(os.networkInterfaces()).flat().filter((i) => i && i.family === 'IPv4').map((i) => i.address);
const altNames = [{ type: 2, value: 'localhost' }, { type: 2, value: os.hostname() }, { type: 2, value: `${os.hostname()}.local` },
  ...ips.map((ip) => ({ type: 7, ip }))];
const pems = await selfsigned.generate([{ name: 'commonName', value: 'lap-sim-vr' }], {
  days: 825, keySize: 2048, algorithm: 'sha256', extensions: [{ name: 'subjectAltName', altNames }],
});
fs.mkdirSync('.certs', { recursive: true });
fs.writeFileSync('.certs/selfsigned.crt', pems.cert);
fs.writeFileSync('.certs/selfsigned.key', pems.private);
console.log('wrote .certs/selfsigned.{crt,key} for', ['localhost', os.hostname(), ...ips].join(', '));
