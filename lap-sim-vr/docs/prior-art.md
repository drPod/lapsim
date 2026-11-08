# Prior art and what we reused

> Update (Nov 2026): the headset now shows a **virtual abdomen** instead of the box camera. The engine, anatomy, AI-restyling, splatting and instrument-tracking research for that is in [realism-options.md](realism-options.md). In short: iMSTK is discontinued, and the prototype uses three.js + XPBD with LapGym's MIT liver/gallbladder meshes, with SOFA on a PC as the upgrade path.

This was a short survey, done before building, of what already exists for a WebXR lap-chole trainer that streams a physical box camera into a headset. The short version: no open-source project already does "physical box trainer + live laparoscope feed inside a VR OR". The pieces do exist, and we reused them.

## Reused directly

| Piece | Project | License | Why |
|---|---|---|---|
| 3D engine, WebXR session, VR button, controller models, glTF/HDR loaders, `GroundedSkybox` | [three.js](https://threejs.org) r187 | MIT | Most mature WebXR stack in the browser, runs well in the Quest Browser, and `GroundedSkybox` turns one 360° photo into a convincing room with a floor. A-Frame would have been faster for the scene, but its abstraction gets in the way of the custom shader we need for the overlays. |
| Headset emulation for tests | Meta's [IWER](https://github.com/meta-quest/immersive-web-emulation-runtime) (`iwer`, `@iwer/devui`) | MIT | Emulates a Quest 3 WebXR runtime in any browser. `?emulate=1` installs it, which is how `tests/screenshots.mjs` enters a real `immersive-vr` session in headless Chrome. `&devui=1` adds Meta's DevUI overlay for manual testing. |
| Camera streaming server | [MediaMTX](https://github.com/bluenviron/mediamtx) | MIT | Built-in Raspberry Pi camera source (libcamera, so no separate `rpicam-vid` pipe is needed), WebRTC with WHEP/WHIP, and roughly 100–250 ms glass-to-glass latency. It is the standard answer to "Pi camera → browser with low latency". |
| WHEP player and WHIP publisher | MediaMTX's own `reader.js` / `publisher.js` (vendored in `client/public/vendor/`) | MIT | Battle-tested, with codec negotiation, trickle ICE and automatic retry. We didn't write our own WHEP client. |
| The OR room | Poly Haven ["Surgery" HDRI](https://polyhaven.com/a/surgery) by Oliksiy Yakovlyev | CC0 | A real operating room photographed in 360°. It is used both as the visible room and as image-based lighting. |
| Props | Poly Haven [Metal Stool 01](https://polyhaven.com/a/metal_stool_01) (Ulan Cabanilla), [Metal Trash Can](https://polyhaven.com/a/metal_trash_can) (GurJas Studios), [Industrial Storage Cart](https://polyhaven.com/a/industrial_storage_cart) (Jule Bielitz); Kenney [Furniture Kit](https://kenney.nl/assets/furniture-kit) side table and keyboard | CC0 | Free, glTF, small. Poly Haven models were re-packed to `.glb` with WebP textures using [glTF-Transform](https://gltf-transform.dev). |
| Demo laparoscope clip | Boer J, Boerma D, de Vries Reilingh T (2011), *"A gallbladder torsion presenting as acute cholecystitis in an elderly woman: a case report"*, J Med Case Reports 5:588, [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:A-gallbladder-torsion-presenting-as-acute-cholecystitis-in-an-elderly-woman-A-case-report-1752-1947-5-588-S1.ogv) | CC BY 2.0 | A real laparoscopic cholecystectomy recording under a reuse license. We cropped the letterbox and transcoded it to H.264 MP4. |

## Looked at, not used (and why)

- **[LapGym / sofa_env](https://github.com/ScheiklP/sofa_env)** (ScheiklP, JMLR 2023). Gym environments for robot-assisted laparoscopy, built on the SOFA FEM engine. Its MIT-licensed liver and gallbladder meshes (visual + tetrahedral) are now **reused** for the virtual abdomen.
- **[SOFA Framework](https://www.sofa-framework.org)**. A native C++/Python physics engine with no browser runtime. Same reason as above.
- **SurRoL** (CUHK, dVRK-compatible RL platform on PyBullet). Robot-focused (da Vinci) and desktop Python, not relevant to a manual-instrument box trainer.
- **Commercial lap trainers** (LapSim, LAP Mentor, VirtaMed). Closed and expensive, but useful as a reference for what complications to model: bleeding, smoke, vitals, and staff callouts.
- **Unity / Unreal medical VR trainers** (e.g. Osso VR, FundamentalVR). Closed source. Unity WebGL builds also don't give us WebXR on the Quest without extra plugins. Shipping a URL instead of an APK is a big win for a DIY rig.
- **A-Frame**. It would have been fine. We stayed on raw three.js because the monitor overlay is a single custom `ShaderMaterial` and the rest of the scene is small.
- **Immersive Web Emulator (Chrome extension)**. This is the extension form of IWER. We use the npm package so tests are scriptable.
- **picamera2 + aiortc / custom GStreamer `webrtcbin`**. Both are viable for Pi → WebRTC, but they mean more code and more tuning than MediaMTX's `rpiCamera` source.
- **Sketchfab / Poly Pizza OR models**. Sketchfab requires an account and an API token to download, and Poly Pizza's API requires a key. CC0 OR-specific models (operating table, surgical lights) are rare. The table, draped patient, surgical lights, laparoscopy tower and IV pole are therefore built procedurally in `client/src/or.js`, over a real OR photo that carries most of the realism.

## Design choices that follow from this

- **The headset never touches the camera directly.** MediaMTX runs next to the camera (Pi or laptop) and the headset pulls WebRTC from it. The Node server proxies only the WHEP/WHIP *signalling* (`/mediamtx/*`), so an HTTPS page can talk to a plain-HTTP MediaMTX without mixed-content errors. Media flows peer-to-peer over the LAN.
- **Overlays are a shader, not video compositing.** Blood, smoke, the banner and the HUD are composited on the GPU at display time, so complications cost no extra latency and need nothing on the Pi.
- **One event bus for humans, agents and hardware.** The director panel, future AI agents (`role: "agent"`) and future pump/servo daemons on the Pi (`role: "hardware"`) all speak the same JSON over `/ws` or `POST /api/events`.
