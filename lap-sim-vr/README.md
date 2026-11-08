# lap-sim-vr

This is the VR half of a DIY laparoscopic cholecystectomy trainer. The surgeon wears a **Meta Quest 3 / 3S** and stands in a virtual operating room. The **live feed from the camera inside the box trainer** plays on the big laparoscopy monitor, just as in a real lap case. A human "director" (and later, AI agents) can trigger complications: blood flooding the view, cautery smoke, a red warning banner, crashing vitals, and OR staff talking.

It's a plain WebXR web app (three.js), so there's nothing to install on the headset. You open a URL in the Quest Browser and press **Enter VR**.

**New: virtual abdomen.** By default the OR monitor now shows a **simulated, deformable gallbladder field** (liver, gallbladder, cystic duct, CBD, cystic artery, peritoneum) instead of the box camera. Its instruments are driven by tool poses: from the box tracker later, and today from the mouse/keyboard, the Quest controllers, a scripted autopilot or `scripts/sim-tools.mjs`. The physical box becomes passive haptics. Clipping, cutting and cautery work, and errors (cutting an unclipped artery or duct, burning the CBD) automatically raise bleeding / bile / warning / vitals / voice events. See [docs/realism-options.md](docs/realism-options.md) for the engine research and the tracking hardware recommendation.

```
 box trainer                 laptop / Pi                         Quest 3
┌──────────────┐  CSI/USB  ┌────────────────────────────┐ WebRTC ┌──────────────────────┐
│ camera ──────┼──────────►│ MediaMTX  (:8889 WHEP)     │───────►│ Quest Browser        │
│ sponge liver │           │ lap-sim-vr server (:8787)  │  wss   │  OR scene + monitor  │
│ balloon GB   │           │   /ws  /api/events  /mediamtx◄──────►│  overlays, vitals    │
└──────────────┘           └───────────▲────────────────┘        └──────────────────────┘
                                       │ ws / REST JSON events
                         director panel (/control.html), future AI agents, Pi pump/servo daemons
```

| Page | What |
|---|---|
| `/` | The VR operating room. Open it on the Quest. |
| `/?view=scope` | Just the laparoscope monitor image, full screen (desktop). Mouse/keyboard drive the tools |
| `/abdomen.html` | Bare virtual abdomen (no OR, no overlays), with mouse/keyboard tool control |
| `/control.html` | Director panel: bleed, smoke, warning, vitals, voices, feed source |
| `/sender.html` | Laptop-webcam sender (publishes the webcam to MediaMTX over WebRTC/WHIP) |
| `/api/events` | `POST` one event or an array of events (for agents and scripts) |
| `/api/state`, `/api/schema`, `/api/config` | Current scenario state, JSON Schema of events, and video config |

## Quick start (laptop + USB webcam, everything on one machine)

You need Node 20+ and a laptop on the same Wi-Fi as the Quest.

```bash
npm install
npm run mediamtx      # terminal 1: downloads MediaMTX once, runs laptop/mediamtx.yml
npm run dev           # terminal 2: event server :8787 + Vite HTTPS dev server :5173
```

1. On the laptop, open `https://localhost:5173/sender.html`, pick the USB webcam (the one inside the box) and click **Start sending**.
2. On the Quest, open `https://<laptop-LAN-IP>:5173/`. Vite prints the address under "Network". Accept the certificate warning (**Advanced → Proceed**). The page is now a secure context, so WebXR works.
3. The monitor label switches from `DEMO` to `LIVE · box camera`. Press **Enter VR**.
4. On the laptop (or a phone), open `https://<laptop-LAN-IP>:5173/control.html` and play director.

If no camera is publishing, the monitor plays the demo lap-chole clip (`source: auto`). The same happens if the camera drops.

### Webcam via ffmpeg instead of the browser

Use this to run headless or to get lower latency. Publish to MediaMTX's RTSP port, path `cam`:

```bash
# macOS
ffmpeg -f avfoundation -framerate 30 -video_size 1280x720 -i "0" \
  -c:v libx264 -preset ultrafast -tune zerolatency -g 30 -bf 0 -b:v 3M -pix_fmt yuv420p -f rtsp rtsp://localhost:8554/cam
# Linux
ffmpeg -f v4l2 -input_format mjpeg -video_size 1280x720 -framerate 30 -i /dev/video0 \
  -c:v libx264 -preset ultrafast -tune zerolatency -g 30 -bf 0 -b:v 3M -pix_fmt yuv420p -f rtsp rtsp://localhost:8554/cam
# Windows (list cameras: ffmpeg -list_devices true -f dshow -i dummy)
ffmpeg -f dshow -video_size 1280x720 -framerate 30 -i video="USB Camera" ^
  -c:v libx264 -preset ultrafast -tune zerolatency -g 30 -bf 0 -b:v 3M -pix_fmt yuv420p -f rtsp rtsp://localhost:8554/cam
```

You can also uncomment `runOnInit` in `laptop/mediamtx.yml` so MediaMTX starts ffmpeg itself.

## Raspberry Pi 5 camera setup

**Hardware:** Raspberry Pi 5 (4 GB is plenty), official 27 W USB-C PSU, a camera, and a **Pi 5 camera cable** (22-pin to 15-pin "mini" FPC; the Pi 5 uses the smaller connector, so older Pi 4 cables don't fit).

Camera choice:

- **Camera Module 3 *Wide*** (~US$35, 120° FOV, autofocus) is the best fit. A wide FOV mimics a laparoscope, and autofocus handles the 5–25 cm working distance in a bin. The standard (non-wide) Camera Module 3 also works but sees less.
- Fixed-focus modules (Camera Module 2, cheap OV5647) are often focused at infinity and will be blurry at 10 cm. You'd have to twist the lens to refocus.
- **Light:** the box is dark. Add a small LED ring or strip next to the camera (5 V USB LED strip, or a 12 V strip with a buck converter). Diffuse it with tape or paper to avoid glare on the wet balloon.
- Mount the camera at a laparoscope-like angle, looking down at about 30–45° from one end of the bin. Use `rpiCameraHFlip/VFlip` in the config if the image is mirrored.

**Software** (Raspberry Pi OS Bookworm 64-bit, Pi on the same LAN as the Quest):

```bash
sudo apt update && sudo apt install -y rpicam-apps git
rpicam-hello --list-cameras          # the camera must be listed
git clone <this repo> && cd lap-sim-vr
./pi/setup.sh                        # installs MediaMTX + systemd service with pi/mediamtx.yml
```

This runs MediaMTX's built-in `rpiCamera` source (libcamera, the same stack as `rpicam-vid`) at 1280×720/30 fps, H.264, keyframe every second. The Pi 5 has no hardware H.264 encoder, so encoding is in software. 720p30 is comfortable; 1080p works but adds CPU load and latency. Check the stream at `http://<pi-ip>:8889/cam` (MediaMTX's own WebRTC page).

Run the lap-sim-vr server either **on the laptop**, pointing at the Pi:

```bash
MEDIAMTX_URL=http://<pi-ip>:8889 npm run dev
```

**or on the Pi itself** (install Node 20+, e.g. `sudo apt install -y nodejs npm` or from NodeSource):

```bash
npm install && npm run build && npm run cert
HTTPS=1 PORT=8443 npm start          # then open https://<pi-ip>:8443/ on the Quest
```

The server only proxies the WebRTC *signalling* (`/mediamtx/*`). The video itself goes peer-to-peer from MediaMTX to the headset over the LAN (UDP 8189). If the Quest shows `camera: …` errors, open UDP 8189 / TCP 8889 on the Pi's firewall. If the Pi has several interfaces, set `webrtcAdditionalHosts: [<pi-ip>]` in `pi/mediamtx.yml`.

## Running the server

| Command | What |
|---|---|
| `npm run dev` | Event server on :8787 + Vite dev server with HTTPS (self-signed, `@vitejs/plugin-basic-ssl`) on :5173. Use this day to day. |
| `npm run build && npm start` | Production: the server serves `dist/` over **HTTP** on :8787. Use behind a TLS proxy or tunnel. |
| `npm run cert && npm run build && npm run start:https` | Production over **HTTPS** on :8443 with a self-signed cert covering this machine's LAN IPs. |
| `npm run mediamtx [-- pi/mediamtx.yml]` | Download and run MediaMTX for this OS. |

Environment variables: `PORT`, `HOST`, `HTTPS=1`, `CERT_FILE`/`KEY_FILE` (e.g. from mkcert), `MEDIAMTX_URL` (default `http://127.0.0.1:8889`), `CAMERA_PATH` (default `cam`), `DEFAULT_SOURCE` (`auto` | `live` | `demo`).

### HTTPS on the LAN (WebXR needs a secure context)

- **Easiest:** use the self-signed certs (`npm run dev`, or `npm run cert` + `start:https`). The Quest Browser shows a warning once; tap **Advanced → Proceed** and WebXR works.
- **No warning:** use [mkcert](https://github.com/FiloSottile/mkcert). Run `mkcert -install && mkcert <laptop-ip> localhost`, then `CERT_FILE=./<ip>+1.pem KEY_FILE=./<ip>+1-key.pem HTTPS=1 PORT=8443 npm start`. To get no warning on the Quest you must also install mkcert's `rootCA.pem` on the headset (Settings → Security → Credentials). That isn't always available, so clicking through is usually simpler.
- **Remote / no LAN:** any HTTPS tunnel (Cloudflare Tunnel, ngrok, Replicas preview) in front of `npm start` works for the scene, director panel and demo video. Live WebRTC video still needs the headset to reach MediaMTX directly (same LAN), or a TURN server.

## The operating room (art pipeline and performance)

```bash
. ~/.replicas/runtime-env.sh                     # provides SKETCHFAB_API_TOKEN (never commit it)
node scripts/fetch-sketchfab.mjs                 # downloads the models in assets-src/or-models.json -> assets-src/sketchfab/ (gitignored)
node scripts/extract-nodes.mjs assets-src/sketchfab/room.glb assets-src/sketchfab/orlamps.glb Obj3d66-594498-90-834_29   # light boom
node scripts/extract-nodes.mjs assets-src/sketchfab/room.glb assets-src/sketchfab/pendant.glb Group-594498-941_64        # pendant
node scripts/build-or-models.mjs                 # simplify + KTX2 + meshopt -> client/public/assets/or/*.glb (needs `toktx` from KTX-Software)
node scripts/write-credits.mjs                   # -> client/public/assets/credits.json (/credits.html)
```

Measured budget (Chrome, Quest 3 profile via IWER): the scene has **about 314k triangles in 98 meshes**. A desktop frame is **128 draw calls / about 535k rendered triangles**, including the shadow pass. Stereo XR is **about 210 draw calls / about 790k triangles** for both eyes. The abdomen renders separately into a 1280×720 texture. Things that keep it Quest-friendly:
- one shadow-casting light (1024² map) plus soft contact-shadow decals instead of baked AO
- the static room merged per material
- glass `KHR_materials_transmission` replaced with plain transparency, since transmission forces a second full-scene pass
- KTX2 GPU-compressed textures

three.js multiview (one draw for both eyes) is only available in its WebGPU renderer today.

## Opening it on the Quest

1. Put the Quest on the same Wi-Fi as the laptop or Pi.
2. Open the Quest Browser and go to the URL (tip: open the director panel on your phone and use **VR view ↗** to send the link, or type it once and bookmark it).
3. Accept the certificate warning if shown.
4. Press **Enter VR**. You're standing on the patient's left at the OR table. The laparoscopy monitor is across the table, and the anaesthesia monitor is at the head end.
5. **Recenter:** squeeze either grip button. This puts the surgeon spot where you're standing, facing where you're looking. You can also hold the Meta button to recenter the headset.
6. Audio (pulse-ox beep, alarms, staff voices) starts once you've entered VR or tapped the page.

Your hands will be on the real instruments, so you can put the controllers down. Hand tracking may show nothing, and that's expected.

Useful URL flags: `?source=demo|live|auto`, `?whep=<url>` (WHEP override), `?monitorWidth=1.8`, `?controllers=0`, `?emulate=1[&devui=1]` (IWER Quest-3 emulation on a desktop browser), `?clean=1` (hide the 2D status box).

## Virtual abdomen (simulated laparoscopic field)

`client/src/abdomen/`: three.js + a small XPBD soft-body solver (`xpbd.js`), running in the Quest browser.

- **Anatomy** (`anatomy.js`): LapGym's MIT liver and gallbladder meshes (tetrahedral volumes are simulated; high-res surfaces are skinned to them). The cystic duct, CBD/CHD and pulsing cystic artery are XPBD rods. A tearable peritoneal veil covers Calot's triangle, and the scene also includes the duodenum, omentum and the peritoneal cavity. Wet-tissue look comes from procedural PBR (`materials.js`) lit by a coaxial scope light.
- **Instruments** (`tools.js`): grasper, Maryland, L-hook (cautery), scissors and clip applier. A grasper grabs whatever tissue is between the jaws. The clip applier leaves titanium clips on the duct or artery. Scissors divide rods and the veil. The hook with energy burns (chars), dissects the veil, frees the gallbladder from its liver bed and emits smoke.
- **Complications** (`index.js`): cutting the artery proximal to any clip produces a pulsatile bleed + warning + falling BP + attending voice. Cutting the duct without stump clips gives a bile leak. Cutting or burning the CBD gives a critical warning. They are sent as ordinary events, so the director panel, overlays and future agents see them.
- **Rendering:** an HDR render target, shown on the OR monitor through the existing overlay shader (ACES tonemapping there). `?scopeAngle=30` gives a 30° scope; `?fat=0` removes the veil.

### Box tracker → instruments in the room

When `tools` poses arrive from role `hardware` (WebSocket) or over UDP:
- The OR shows each instrument as a long laparoscopic instrument entering the patient through a trocar on the drape, pivoting at the trocar exactly as the pose says (`client/src/outside.js`). The lap monitor shows the same instruments inside.
- The tracker overrides the controller-as-instrument mode, which comes back about 1 s after the stream stops.
- Tools are matched by `port`, so a tracker can use the port names (`epigastric`, `rsc`, `raa`) as ids.
- The server broadcasts `{type:"tracker", live, hz, tools}` once a second. It is shown as **TRACKER LIVE · N Hz / NO TRACKER** in the HUD, as a pill in the director panel, and as a badge on the lap monitor.
- `node tests/tracker.mjs` runs the box tracker's protocol (hardware client, 30 Hz, sweeping left/right then in/out) and captures the OR and scope views.

**Frame mapping.** The drape trocars are the abdomen's `PORTS` mapped rigidly (no mirror) onto the belly, so the spacing matches exactly (epigastric↔umbilical 17.5 cm). From the surgeon's standing position, abdomen +x maps to the surgeon's right, +z (anterior) to up, and +y ("head" on the monitor) to away from the surgeon. Left/right therefore agrees between the room and the monitor.

Known anatomical issue, to fix in one go later: the abdomen frame is mirrored left/right relative to real anatomy (rsc/raa sit at +x), and its head axis points across the table rather than along the placeholder patient. Fixing both means changing `PORTS` plus the anatomy transform in `abdomen/anatomy.js` and the matrix in `outside.js` together, and re-calibrating the box.

### Tool-pose interface (for the box tracker)

Every pose source produces the same per-instrument **fulcrum** pose: the instrument pivots through its trocar.

```json
{"type":"tools","t":1730000000000,"tools":[
  {"id":"right","port":"epigastric","tool":"maryland","yaw":0.05,"pitch":-0.10,"roll":0.0,"depth":0.11,"jaw":0.8,"energy":false},
  {"id":"left","port":"rsc","tool":"grasper","yaw":0.12,"pitch":0.02,"roll":0.3,"depth":0.09,"jaw":0.0},
  {"id":"scope","port":"umbilical","tool":"scope","yaw":0,"pitch":0,"depth":0.065}
]}
```

| field | meaning |
|---|---|
| `id` | `left`, `right`, `assist`, `scope` (any new id creates a new instrument) |
| `port` | `umbilical`, `epigastric`, `rsc` (right subcostal), `raa` (right anterior axillary). Positions and nominal axes are in `PORTS` in `client/src/abdomen/tools.js`; match them to your lid |
| `yaw`, `pitch` | rad, shaft rotation about the port's nominal axis (right/left, up/down) |
| `roll` | rad, spin about the shaft |
| `depth` | m, trocar pivot → jaw hinge |
| `jaw` | 0 closed … 1 open |
| `tool` | `grasper` \| `maryland` \| `hook` \| `scissors` \| `clipper` (switching = instrument exchange) |
| `energy` | cautery pedal |

There are three ways to send poses, with no state or logging involved, at 30–120 Hz:
- **WebSocket** `/ws` (say `{"type":"hello","role":"hardware"}` first).
- **UDP** datagrams to port **9870** (`TOOLS_UDP_PORT`). See `hardware/tracker_udp_example.py`.
- `POST /api/events`.

Simulated sources for testing:
- `node scripts/sim-tools.mjs`: streams the scripted procedure like a tracker would (`--mode sweep`, `--udp host:9870`, `--url wss://…`, `--loop`).
- **Mouse/keyboard** on `/?view=scope` or `/abdomen.html`: drag = pivot, wheel = depth, Q/E = roll, Space/right mouse = close jaws, F = energy, T = change instrument, 1/2/3/0 = select left/right/assistant/scope.
- **Quest controllers** in VR, when no tracker is streaming: each controller is an instrument handle pivoting through a trocar on the virtual patient. Trigger closes the jaws, A/X = energy, B/Y = next instrument.
- **Autopilot:** `?autopilot=1` (or the director panel ▶ button), which retracts, dissects, clips, divides the duct, then cuts the artery unclipped to show the complication chain. `?autopilot=bile` skips the duct clips instead.

Recommended tracker hardware (instrumented trocars, about US$80–150 for three ports) is in [docs/realism-options.md](docs/realism-options.md#instrument-tracking-in-the-box).

## Event schema (for the director panel, AI agents and hardware)

Connect a WebSocket to `wss://<host>/ws` and send `{"type":"hello","role":"agent","name":"anesthesia-bot"}`. Then send events as JSON. Every client receives every event, with `id`, `ts` (ms) and `from` (the sender's role) added. A new client first receives `{"type":"state", ...}` with the current scenario. Without a socket, `POST /api/events` with one event or an array:

```bash
curl -k https://localhost:5173/api/events -H 'content-type: application/json' \
  -d '[{"type":"bleed","intensity":0.8},{"type":"say","speaker":"Anesthesia","text":"Pressure is dropping"}]'
```

The full JSON Schema is at `schema/events.schema.json` (also `GET /api/schema`). Invalid events are rejected with an error.

| type | fields | effect |
|---|---|---|
| `bleed` | `intensity` 0–1 (0 = stop/suction), `origin` `{x,y}` 0–1 screen coords (default `{0.62,0.38}`), `pulsatile` bool (default true) | Blood pools from the origin and runs down. Arterial bleeds spurt in time with the heart rate. High intensity darkens and reddens the whole view. |
| `smoke` | `intensity` 0–1 (default 0.7), `durationMs` (default 8000, 0 = until cleared) | Cautery smoke / lens fog with heat shimmer and blur |
| `warning` | `text`, `level` `critical`\|`warning`\|`info`, `durationMs` (0 = until cleared) | Banner across the top of the lap monitor. Critical blinks. |
| `clear` | `target` `all`\|`bleed`\|`bile`\|`smoke`\|`warning`\|`captions` | Remove overlays |
| `vitals` | any of `hr`, `bp` (`"120/80"`), `spo2`, `rr`, `etco2`, `temp`, `transitionMs` (default 3000) | Anaesthesia monitor values glide to the new targets. Alarms flash and sound when out of range (SpO2 <90, HR >120/<50, SBP <90/>180, EtCO2 >50). The pulse-ox beep pitch drops with SpO2. |
| `say` | `speaker`, `text`, optional `audioUrl`, `voice` | Caption under the lap monitor, spoken with the browser's speech synthesis or by playing `audioUrl` (for agent-generated TTS) |
| `source` | `mode` `sim`\|`live`\|`demo`\|`auto`, optional `whep` | Switch the laparoscope feed (`sim` = virtual abdomen, default) |
| `bile` | `intensity` 0–1, `origin` `{x,y}` | Bile flooding the view (green-yellow) |
| `tools` | `tools: [{id, port, tool, yaw, pitch, roll, depth, jaw, energy}]` | Instrument poses (see above). High rate, not logged |
| `sim` | `action` `reset`\|`autopilot`\|`stop` (control), `clip`\|`cut` (sent by the sim, informational) | Virtual-abdomen control and feedback |
| `actuator` | `device` (e.g. `pump.bleed`, `servo.liver`), `value`, `durationMs` | **Hook only.** Forwarded to `role: "hardware"` clients (future Pi pump/servo daemons). See `hardware/actuator_client.py`. |
| `reset` | – | Clear everything and return vitals to baseline |

Server → client only: `state`, `presence` (connected roles), `history` (to directors), `error`.

## Testing without a headset

`tests/` uses [Playwright](https://playwright.dev) + Meta's [IWER](https://github.com/meta-quest/immersive-web-emulation-runtime) to emulate a Quest 3 in headless Chrome. The tests enter a real `immersive-vr` session, send events through the REST API and screenshot the in-headset view:

```bash
npm run build && npm start &            # server on :8787
npm run test:screens                    # artifacts/01..08 *.png
npm run mediamtx & ffmpeg -re -stream_loop -1 -i client/public/media/demo-lap-chole.mp4 \
  -c:v libx264 -preset ultrafast -tune zerolatency -f rtsp rtsp://localhost:8554/cam &
node tests/live.mjs                     # verifies MediaMTX -> WHEP proxy -> VR monitor shows LIVE
node tests/record-demo.mjs              # director panel + VR view side by side -> artifacts/demo.webm
node tests/abdomen.mjs                  # virtual abdomen: VR monitor + scope views through the scripted procedure
node tests/record-abdomen.mjs 15 4 25   # frame-by-frame capture of the procedure -> artifacts/abd-frames/
```

For interactive testing on a desktop, open `/?emulate=1&devui=1` to get IWER's DevUI (move the emulated headset and controllers).

## Project layout

```
client/src/abdomen/  virtual abdomen: xpbd.js (solver), anatomy.js, materials.js, tools.js (fulcrum kinematics + instruments),
                     index.js (interaction, complications, render), autopilot.js, keyboard.js, standalone.js
client/            Vite root
  index.html       VR view          src/main.js      scene setup, events, XR
  control.html     director panel   src/or.js        OR set dressing (HDRI room, table, drapes, lights, tower, props)
  sender.html      webcam sender    src/monitor.js   lap monitor + overlay shader
  public/vendor/   MediaMTX reader.js / publisher.js (MIT)
  public/assets/   HDRI + glTF props        src/vitals.js   anaesthesia monitor
  public/media/    demo clip                src/audio.js    beeps, alarms, TTS, captions
server/index.js    event hub (ws + REST), MediaMTX signalling proxy, static hosting
schema/            JSON Schema for events
pi/                MediaMTX config + setup script for Raspberry Pi 5
laptop/            MediaMTX config for a laptop webcam
hardware/          stub hardware client for future pumps/servos
docs/prior-art.md  what we looked at and reused
```

## Credits and licenses

Code: MIT. Third-party assets (all free to reuse):

- **OR equipment and staff:** 3D models from [Sketchfab](https://sketchfab.com), all **CC BY 4.0** (no NC models used; the popular "Charité University Hospital – Operating Room" was skipped because it is CC BY-NC). They were downloaded with the Sketchfab Download API and optimised for Quest (meshoptimizer simplification, KTX2/Basis textures ≤1024 px, meshopt compression). The surgical light boom and equipment pendant were extracted from Chenchanchong's full room model. The in-app list is at `/credits.html`.

| file | model | author | licence |
|---|---|---|---|
| anesthesia | [Anesthesia Machine on a Medical Cart](https://sketchfab.com/3d-models/6b28fafc23244ef9a47e84dbde09b5a4) | kkso199 | CC BY 4.0 |
| endocart | [Endoscope Camera Cart](https://sketchfab.com/3d-models/bd483063aa0d4ab9b43e3cf79240c61b) | krio302 | CC BY 4.0 |
| roofmon | [Operating Room Roof Monitors](https://sketchfab.com/3d-models/359aea63962d467cb81b42d942870644) | kareem.elgayyar | CC BY 4.0 |
| ivstand | [Iv drip stand](https://sketchfab.com/3d-models/b503f5749e5841139f3bdd0bec20a7a6) | saba.nazir | CC BY 4.0 |
| suction | [Suction-canister](https://sketchfab.com/3d-models/34f41e2382fb4b66a9732cbebc8e872d) | Dwayne.Barnett | CC BY 4.0 |
| trolley | [Medical Props (instrument trolley)](https://sketchfab.com/3d-models/4b2e1a4c3f13432b9a0bc15355b27632) | coa white | CC BY 4.0 |
| cabinet | [Hospital medical Treatment Cabinet](https://sketchfab.com/3d-models/9a9edfe3879e4889877bf1dff97c6105) | Chenchanchong | CC BY 4.0 |
| surgeon | [Male surgical doctor working 22](https://sketchfab.com/3d-models/79e23af07d1243c789b3372c6ec72c27) | deep3dstudio | CC BY 4.0 |
| nurse | [Nurse in light blue scrubs](https://sketchfab.com/3d-models/fc7dad7d88f24687bc718a32d6ad4e8d) | restore50 | CC BY 4.0 |
| bed | [Hospital Operating Room/Examination Bed](https://sketchfab.com/3d-models/e3d748837ab14ef3aab1348faa20991d) | Chenchanchong | CC BY 4.0 |
| orlamps | [Hospital operating room (surgical light boom, extracted)](https://sketchfab.com/3d-models/a27400a73f4a43fbb1dda30031cb91c2) | Chenchanchong | CC BY 4.0 |
| pendant | [Hospital operating room (equipment pendant, extracted)](https://sketchfab.com/3d-models/a27400a73f4a43fbb1dda30031cb91c2) | Chenchanchong | CC BY 4.0 |

- **Reflections / lighting:** ["Surgery" HDRI](https://polyhaven.com/a/surgery) by Oliksiy Yakovlyev, [Poly Haven](https://polyhaven.com), **CC0** (environment map only; the room itself is now a 3D shell in `client/src/room.js`).
- **Liver and gallbladder meshes (virtual abdomen):** from [LapGym / sofa_env](https://github.com/ScheiklP/sofa_env) (`grasp_lift_touch` scene) by Paul Maria Scheikl et al., **MIT**. Converted with `scripts/build-anatomy.mjs`. The ducts, artery, peritoneum, bowel and omentum are procedural.
- **Demo laparoscope video:** Boer J, Boerma D, de Vries Reilingh T. *A gallbladder torsion presenting as acute cholecystitis in an elderly woman: a case report.* J Med Case Reports 2011;5:588. [Video via Wikimedia Commons](https://commons.wikimedia.org/wiki/File:A-gallbladder-torsion-presenting-as-acute-cholecystitis-in-an-elderly-woman-A-case-report-1752-1947-5-588-S1.ogv), **CC BY 2.0**. Cropped and transcoded to H.264.
- **Libraries:** [three.js](https://threejs.org) (MIT), [IWER / @iwer/devui](https://github.com/meta-quest/immersive-web-emulation-runtime) (MIT, Meta), [MediaMTX](https://github.com/bluenviron/mediamtx) incl. vendored `reader.js`/`publisher.js` (MIT), [ws](https://github.com/websockets/ws), [Express](https://expressjs.com), [Ajv](https://ajv.js.org), [Vite](https://vite.dev).
- The room shell (floor, walls, laminar-flow ceiling, door, PACS display, gas panel) and the draped patient are procedural (`client/src/room.js`, `client/src/or.js`).

This is a training aid for a physical box trainer, not a medical device.
