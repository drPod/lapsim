# Realism options: virtual abdomen + instrument tracking

**Goal:** everything the surgeon sees in the headset should look as real as possible. The physical box stays, but only for touch and resistance (passive haptics). The headset shows a realistic **virtual** abdomen whose virtual instruments are driven by the **real** instruments tracked in the box.

Research was done in November 2026. Repository activity and licences were checked directly against GitHub, GitLab and the vendor pages, and links are given inline. Anything I couldn't verify is marked *unverified*.

## TL;DR recommendation

1. **Build now (done): a WebXR-native virtual abdomen.** It uses three.js and our own XPBD soft-body solver, with LapGym's MIT-licensed liver and gallbladder meshes plus procedural Calot's-triangle structures. It renders through a virtual laparoscope onto the existing OR monitor. It is the only option that runs **standalone on the Quest 3/3S** (no PC, no cable), plugs straight into the OR, vitals, overlays and event server we already have, and can be tested in this headless environment. See `client/src/abdomen/`.
2. **Upgrade path for more physical realism: SOFA (FEM) on a PC streamed to the Quest.** This means SofaUnity + Unity HDRP, or SOFA → our WebXR monitor as a video/WebRTC stream. Use it if the tissue mechanics (cutting through volume, real FEM stiffness, haptic devices) become the bottleneck. It costs a Windows PC with an RTX GPU and more engineering.
3. **Do not build on iMSTK.** It had the best laparoscopic feature set, but Kitware **discontinued it on 2 May 2025** (README sunset notice; the GitHub mirror is archived).
4. **AI restyling is an optional layer, not the core.** Use it on the *sim render* (not the box camera) via a GPU server, later. It adds latency and can hallucinate anatomy, which is dangerous in a trainer.
5. **Instrument tracking: instrumented trocars (fulcrum sensors).** Use two magnetic angle encoders plus one optical mouse sensor per port, a jaw sensor on each handle, and an ESP32 sending UDP poses at 100 Hz. This gives no occlusion problems inside a wet, cluttered box and costs roughly US$25–40 of electronics per port. Camera/marker tracking with the existing Pi camera is the zero-hardware fallback. The parts list is below.

## Simulation / rendering engines

| Candidate | Licence | Activity (checked Nov 2026) | VR frame rate on Quest | WebXR / Unity / OpenXR | Lap-chole content | Cut / clip / cautery / bleed | Feeding external tool poses | Verdict |
|---|---|---|---|---|---|---|---|---|
| **iMSTK** (Kitware) | Apache-2.0 | **Sunset 2 May 2025**; last release v7.0.0 (Sep 2023); GitHub mirror archived | PC only (C++/VTK); Unity asset existed | Unity asset + OpenXR via Unity | Yes: lap examples; a lap-chole simulator was built on it (Unity + iMSTK + Pulse) | PBD tissue, cutting, suturing, haptics | C++ device API, VRPN, Haply/Phantom | ❌ discontinued; don't start new work on it |
| **SOFA** + SofaPython3 | LGPL-2.1 | Very active: v26.06.00 (Jul 2026), commits this week | PC real-time for moderate FEM meshes; not on Quest | No browser runtime. **SofaUnity** (InfinyTech3D): licence "Other", Windows-only tested, consulting model → Unity → OpenXR / Quest Link | Liver demos; LapGym scenes (below) | FEM, topological cutting (tetra), haptics; no built-in bleeding visuals | Python API: trivial (set MechanicalObject poses from UDP) | ✅ best physics; upgrade path on PC |
| **LapGym / sofa_env** | MIT | lap_gym last push Dec 2023; sofa_env Oct 2024 | Built for RL (headless), not VR | via SOFA | **grasp_lift_touch** ships liver + gallbladder visual STL and tetrahedral VTK meshes; tissue_dissection, rope_cutting, ligating_loop, precision_cutting | Via SOFA | Gym API | ✅ **meshes reused** (MIT) |
| **NVIDIA Isaac for Healthcare** (i4h-workflows) | Apache-2.0 | Active (Nov 2026); asset catalog repo archived | RTX GPU **16 GB min, 48 GB recommended**; streamed only | Omniverse / Isaac Lab; XR via streaming | Laparoscopic *robotics* workflows (dVRK PSM / STAR reach, lift needle on organ bed); no cholecystectomy | Rigid/robot focus; photoreal RTX rendering | Python / ROS | ❌ too heavy, robot-centric |
| **ORBIT-Surgical** | Custom ("NOASSERTION") | Last push Dec 2024 (Isaac Sim 4.1 / Isaac Lab 1.0) | Same as above | Omniverse | dVRK tasks, photoreal | Limited deformables | Python | ❌ |
| **SurRoL** | MIT | Last push Oct 2025 | PyBullet desktop | — | dVRK rigid tasks | No tissue | Python | ❌ |
| **SurgicAI** | MIT | Last push Mar 2025 | AMBF desktop | — | Suturing (robotic) | — | ROS / Gym | ❌ |
| **Unity + Obi Softbody / Fluid** | Commercial (Asset Store; Obi Rope US$37, Physics Suite US$179 listed; Softbody price not shown) | Obi 7.x maintained (Rope 7.1.1 Apr 2026) | Native Quest app possible; Obi 7 uses GPU compute, so on-device budget is tight *(unverified for Quest)* | Unity → OpenXR (native APK); Unity WebGL has no WebXR without a plugin | None; build it yourself | Particle softbodies, tearing, fluid (good for blood/bile) | C# from UDP | ⚠️ viable native route; paid; we'd rebuild the OR |
| **Unreal Chaos Flesh** | UE EULA | Plugin lives under *Experimental* in UE 5.3–5.5 docs | PC VR only in practice | OpenXR, Quest Link | None | FEM flesh; no cutting tooling | C++/Blueprint | ❌ experimental and heavy |
| **three.js + custom XPBD (this repo)** | MIT (ours) + MIT meshes | — | **Standalone Quest** (WebGL2); sim is about 1k particles / about 5k constraints | **WebXR native**; renders into the existing OR | LapGym liver/GB + procedural cystic duct, CBD, cystic artery, peritoneal fat | Grasp/retract, clip, cut, hook cautery (burn, tear fat, release GB bed), complications → events | `tools` messages over WebSocket / UDP / REST (fulcrum params) | ✅ **picked for the prototype** |

Notes:
- **Quest Browser WebGPU-in-WebXR** is still *experimental, behind flags* (Meta release notes: Chromium 146 Apr 2026, 150.1 Aug 2026, 152.1 Oct 2026). WebGL2 is the stable path today, so the prototype uses WebGL2. A WebGPU compute port of the solver is a later optimisation.
- No complete open-source lap-chole VR simulator was found. [SurgSim_Lite](https://github.com/razterizer/SurgSim_Lite) (MIT, Aug 2026, "A Laparoscopic Surgery Simulator") exists but wasn't evaluated in depth. Academic GPU-PBD lap-chole simulators (e.g. IEEE 2020 "Parallel Position-based Dynamics in GPU") have no public code.

## Anatomy and textures

| Source | Licence | What | Use |
|---|---|---|---|
| **LapGym `grasp_lift_touch` meshes** | MIT | Liver (28.7k-tri visual + 98-node/273-tet volume), gallbladder (14.7k-tri visual + 367-node/1063-tet volume), in one consistent frame | ✅ used (`scripts/build-anatomy.mjs`) |
| BodyParts3D | CC BY-SA 2.1 JP | Atlas OBJ meshes (liver, GB, biliary tree, arteries) | Possible; share-alike; atlas look, not surgical |
| [Z-Anatomy](https://github.com/Z-Anatomy/Models-of-human-anatomy) | CC BY-SA 4.0 (derives BodyParts3D) | 87 MB Blender atlas; repo active (Nov 2026) | Possible source for a full biliary tree / hepatic artery; share-alike would apply to the meshes |
| CGTrader / RenderHub liver-GB models | Royalty-free commercial, not CC | Good-looking organs | Only if bought |
| Cholec80 / CholecSeg8k (real lap-chole frames) | Research / non-commercial licences *(unverified exact terms)* | Real colour references, training data for restyling | Reference only unless licence allows |

Tissue appearance in the prototype is **procedural**: 3D simplex noise anchored to each vertex's rest position, so it sticks to deforming tissue. It has serosal vessels, lobule speckle, a bump map, and a clearcoat "wet" layer, lit only by a coaxial scope light with inverse-square falloff (which is most of what makes laparoscopic images look laparoscopic). Baked photographic textures would be the next realism step.

## AI restyling of video (fallback / enhancement)

- **[StreamDiffusion](https://github.com/cumulo-autumn/StreamDiffusion)** (Apache-2.0; up to 91 fps img2img on one RTX 4090 per the paper; last push Dec 2024). It's fast, but with no temporal consistency guarantees and no anatomy guarantees.
- **Surgical sim-to-real translation:** Rivoir et al., *Long-Term Temporally Consistent Unpaired Video Translation from Simulated Surgical 3D Data* (ICCV 2021). Also [CIIGAN](https://github.com/LongChenCV/CIIGAN) (Medical Image Analysis 2025, code public, licence unclear), and Colan et al. 2024 (CUT for laparoscopic sim-to-real).
- **Verdict:** feasible as a GPU-server post-process on the *virtual* render, where geometry is known and temporal consistency can be enforced. It is not feasible on the Quest. Restyling the **sponge/balloon camera feed** is the weakest option: the model would have to invent anatomy that isn't there, and tools would flicker. It adds about 50–150 ms latency *(estimate)*. Treat it as phase 3 at most.

## Gaussian splatting / NeRF

- [EndoNeRF](https://github.com/med-air/EndoNeRF) (no licence file), [EndoGaussian](https://github.com/CUHK-AIM-Group/EndoGaussian) (MIT; 195 fps render in the paper), [Deform3DGS](https://github.com/jinlab-imvr/Deform3DGS) (licence unclear). These reconstruct **recorded** endoscopic video into deformable splats. They replay the recorded deformation and are not interactive: no tool-tissue physics, cutting or clipping. Useful for a photoreal *static* backdrop or for teaching playback, not for the interactive surgical field.
- **Photoreal OR:** we already use a real 360° capture (Poly Haven "Surgery", CC0). A Gaussian-splat OR in WebXR (three.js splat viewers exist) would add parallax. That's nice to have, but the surgeon looks at the monitor, not the room.

## Why the prototype is WebXR + XPBD (trade-offs)

| | WebXR + XPBD (picked) | SOFA/Unity on PC + Quest Link |
|---|---|---|
| Runs on | Quest standalone, any browser | Windows PC with an RTX GPU + Link cable / Air Link |
| Physics | XPBD: tet soft bodies (edge + volume), rods, tearable sheet. Plausible, not FEM-accurate | FEM, topological cutting, haptic devices |
| Visuals | Mobile GPU, procedural PBR, 1280×720 monitor texture | HDRP/RTX; subsurface, true fluids |
| Integration | Same page as OR, vitals, overlays, events | New app, so the OR must be rebuilt in Unity |
| Testability here | Yes (headless Chrome + IWER) | No (no GPU/Windows) |
| Iteration | Edit, reload URL | Build, deploy |

Known limits of the prototype: the gallbladder and liver can't be *cut through* (only ducts, artery, fat and the GB bed); blood and bile are screen-space fluids anchored at the injury point, not 3D fluid; the liver tet mesh is coarse (98 nodes); and there is no inter-organ collision (the GB is held on its bed by attachments, not contact).

## Instrument tracking in the box

Each instrument passes through a port in the lid, so it has 4 DOF + jaw: **yaw and pitch about the port (fulcrum), insertion depth, roll, jaw opening**. Everything downstream (`tools` messages) already uses exactly these parameters.

| Approach | Precedent | Pros | Cons |
|---|---|---|---|
| **A. Instrumented trocar (recommended)**: gimbal at each port with 2 magnetic angle encoders, an optical mouse sensor reading the shaft (depth + roll), and a jaw sensor on the handle | **TrEndo**: gimbal + 3 optical mouse sensors, 0.06 mm translation / 1.27° roll resolution; patents on trocar reflective/optical depth sensors | No occlusion (sponge, balloon, water don't matter); 100+ Hz; mm-level; cheap; works in the dark | Needs a small 3D-printed gimbal per port; mouse sensors are relative, so depth needs a homing switch |
| B. Camera + markers (existing Pi camera) | Single fisheye camera + coloured tape → 3D tip at about 25 fps (Frontiers in Robotics and AI 2021); orthogonal webcams + colour markers; ArUco for tool following | No hardware on tools; reuses the Pi camera | Occlusion by tissue and other tools, wet glare, low rate; jaw state and roll are hard; needs calibration |
| C. IMU on each handle (e.g. BNO085) | IMU motion-skill tracking studies (2023) | Gives full shaft orientation (fulcrum) cheaply | Magnetometer is disturbed by metal and magnets; no depth (combine with A's depth sensor) |
| D. Leap Motion / hand tracking | Leap tracked box-trainer instruments to 2–3 mm | — | Line-of-sight; discontinued hardware lineage |

**Fulcrum trick:** with the port position fixed, shaft orientation comes from two gimbal angles. Depth plus orientation then gives the tip in 3D with no camera at all. For option B, the fulcrum constraint also turns a mono camera into a full solve: the shaft line must pass through the known port, so two coloured bands at a known spacing resolve the remaining DOF.

### Parts list (option A, 3 working ports; prices are typical retail and *unverified*)

| Qty | Part | Purpose | ~US$ |
|---|---|---|---|
| 6 | AS5600 magnetic angle encoder breakout + 6×2.5 mm diametric magnet | yaw + pitch per port | 2–4 each |
| 3 | PMW3360 (or PAW3395 / ADNS-9800) optical sensor module with lens | insertion depth + roll from shaft surface (TrEndo principle) | 15–25 each |
| 3 | AS5600 (or SS49E linear Hall + magnet) on the handle pivot | jaw opening | 1–4 each |
| 3 | A3144 Hall switch + small magnet ring on shaft | depth homing (absolute reference) | <1 each |
| 1 | TCA9548A I²C multiplexer | AS5600s all share address 0x36 | 3 |
| 1 | ESP32-S3 dev board (Wi-Fi UDP) or RP2040 Pico W | reads sensors, sends `tools` JSON to `udp/9870` at 100 Hz | 6–12 |
| 1 | USB momentary foot switch | energy (cautery) pedal | 10–15 |
| — | 3D-printed gimbals (PETG), 608/623 bearings, 10–12 mm ID sleeves (or reusable trocars), JST wiring | mechanics | 15–25 |
| 3–4 | Training laparoscopic instruments 5 mm × 33 cm (grasper, Maryland, scissors; dummy clip applier) | the real tools | 15–40 each |

Electronics total is roughly **US$80–150** for three ports, plus instruments. Firmware sends the exact message `scripts/sim-tools.mjs` sends today, so the VR side needs no changes.

The camera-only fallback (option B) needs coloured tape (two bands per shaft at 20 mm spacing) or printed ArUco markers. A second Pi camera module for stereo is optional.

## Sources

- iMSTK sunset notice: https://gitlab.kitware.com/iMSTK/iMSTK (README), mirror https://github.com/Kitware/iMSTK (archived)
- SOFA releases: https://github.com/sofa-framework/sofa/releases · SofaUnity: https://github.com/InfinyTech3D/SofaUnity
- LapGym: https://github.com/ScheiklP/lap_gym · sofa_env meshes: https://github.com/ScheiklP/sofa_env/tree/main/sofa_env/scenes/grasp_lift_touch/meshes
- Isaac for Healthcare: https://github.com/isaac-for-healthcare/i4h-workflows · ORBIT-Surgical: https://github.com/orbit-surgical/orbit-surgical
- SurRoL: https://github.com/med-air/SurRoL · SurgicAI: https://github.com/surgical-robotics-ai/SurgicAI
- Obi: https://assetstore.unity.com/packages/tools/physics/obi-softbody-130029 · Chaos Flesh API (Experimental): https://dev.epicgames.com/documentation/unreal-engine/API/Plugins/ChaosFleshEngine
- Quest Browser release notes: https://developers.meta.com/horizon/release-notes/web/
- Rivoir et al. 2021: https://arxiv.org/abs/2103.17204 · CIIGAN: https://github.com/LongChenCV/CIIGAN · Colan et al.: https://arxiv.org/abs/2409.10204 · StreamDiffusion: https://arxiv.org/abs/2312.12491
- EndoGaussian: https://arxiv.org/abs/2401.12561 · EndoNeRF: https://github.com/med-air/EndoNeRF · Deform3DGS: https://github.com/jinlab-imvr/Deform3DGS
- Tracking: Frontiers 2021 single-camera 3D tracking https://www.frontiersin.org/journals/robotics-and-ai/articles/10.3389/frobt.2021.751741/full · orthogonal cameras https://www.researchgate.net/publication/344969071 · Leap Motion https://www.sciencedirect.com/science/article/abs/pii/S0263224115006041 · LAPKaans https://www.researchgate.net/publication/346628807 · TrEndo https://data.zhenzhi.com/details/1/865873174 · IMU https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10322950/
- Z-Anatomy / BodyParts3D licensing: https://github.com/Z-Anatomy/Models-of-human-anatomy
