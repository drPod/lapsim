// Procedural wet-tissue materials. MeshPhysicalMaterial + onBeforeCompile:
// 3D simplex-noise colour, serosal vessels and bump, sampled at each vertex's
// REST position (attribute `restPos`) so the texture sticks to deforming tissue.
// A smooth clearcoat over the bumpy base gives the wet laparoscopic sheen.
// Optional per-vertex `burn` (0..1) attribute chars the surface (cautery).
import * as THREE from 'three';

const NOISE = /* glsl */ `
vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 permute(vec4 x){return mod289(((x*34.0)+10.0)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1.0/6.0,1.0/3.0); const vec4 D=vec4(0.0,0.5,1.0,2.0);
  vec3 i=floor(v+dot(v,C.yyy)); vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.0-g; vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx; vec3 x2=x0-i2+C.yyy; vec3 x3=x0-D.yyy;
  i=mod289(i);
  vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
  float n_=0.142857142857; vec3 ns=n_*D.wyz-D.xzx;
  vec4 j=p-49.0*floor(p*ns.z*ns.z);
  vec4 x_=floor(j*ns.z); vec4 y_=floor(j-7.0*x_);
  vec4 x=x_*ns.x+ns.yyyy; vec4 y=y_*ns.x+ns.yyyy; vec4 h=1.0-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.0+1.0; vec4 s1=floor(b1)*2.0+1.0; vec4 sh=-step(h,vec4(0.0));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y); vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x;p1*=norm.y;p2*=norm.z;p3*=norm.w;
  vec4 m=max(0.5-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0); m=m*m;
  return 105.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}
float fbm3(vec3 p){ return 0.55*snoise(p) + 0.3*snoise(p*2.03+7.1) + 0.15*snoise(p*4.11+3.3); }
vec3 tissuePerturb(vec3 surf_pos, vec3 surf_norm, vec2 dHdxy, float faceDir){
  vec3 vSigmaX = normalize(dFdx(surf_pos)); vec3 vSigmaY = normalize(dFdy(surf_pos));
  vec3 R1 = cross(vSigmaY, surf_norm); vec3 R2 = cross(surf_norm, vSigmaX);
  float fDet = dot(vSigmaX, R1) * faceDir;
  vec3 vGrad = sign(fDet) * (dHdxy.x * R1 + dHdxy.y * R2);
  return normalize(abs(fDet) * surf_norm - vGrad);
}
`;

/**
 * @param {object} o
 * colA/colB: base colour range; scale: noise frequency (1/m); vein: vessel colour; veinAmt; veinScale;
 * bump: bump height scale; bumpScale: bump frequency; spots: amount of fine speckle (liver lobules / fat lobules)
 */
export function tissueMaterial(o = {}) {
  const u = {
    uColA: { value: new THREE.Color(o.colA ?? 0x5a1810) }, uColB: { value: new THREE.Color(o.colB ?? 0x8a2a1a) },
    uVein: { value: new THREE.Color(o.vein ?? 0x7a0d10) }, uVeinAmt: { value: o.veinAmt ?? 0 }, uVeinScale: { value: o.veinScale ?? 60 },
    uVeinWidth: { value: o.veinWidth ?? 0.06 },
    uScale: { value: o.scale ?? 40 }, uBump: { value: o.bump ?? 0.0015 }, uBumpScale: { value: o.bumpScale ?? 220 },
    uSpots: { value: o.spots ?? 0 }, uSSS: { value: o.sss ?? 0 }, uSpotScale: { value: o.spotScale ?? 600 }, uChar: { value: new THREE.Color(0x1a0f08) },
  };
  const m = new THREE.MeshPhysicalMaterial({
    color: 0xffffff, roughness: o.roughness ?? 0.55, metalness: 0,
    clearcoat: o.clearcoat ?? 1, clearcoatRoughness: o.clearcoatRoughness ?? 0.08,
    sheen: o.sheen ?? 0, sheenColor: new THREE.Color(o.sheenColor ?? 0xffffff), sheenRoughness: 0.5,
    transparent: !!o.transparent, opacity: o.opacity ?? 1, side: o.side ?? THREE.FrontSide,
    depthWrite: o.depthWrite ?? true,
    polygonOffset: !!o.polygonOffset, polygonOffsetFactor: o.polygonOffset ?? 0, polygonOffsetUnits: (o.polygonOffset ?? 0) * 4,
  });
  m.userData.uniforms = u;
  m.onBeforeCompile = (sh) => {
    Object.assign(sh.uniforms, u);
    sh.vertexShader = sh.vertexShader
      .replace('#include <common>', '#include <common>\nattribute vec3 restPos;\nattribute float burn;\nvarying vec3 vRest;\nvarying float vBurn;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>\nvRest = restPos; vBurn = burn;');
    sh.fragmentShader = sh.fragmentShader
      .replace('#include <common>', `#include <common>
uniform vec3 uColA, uColB, uVein, uChar; uniform float uSSS; uniform float uVeinAmt, uVeinScale, uVeinWidth, uScale, uBump, uBumpScale, uSpots, uSpotScale;
varying vec3 vRest; varying float vBurn;
${NOISE}`)
      .replace('#include <map_fragment>', `
float tN = fbm3(vRest * uScale);
vec3 tc = mix(uColA, uColB, smoothstep(-0.7, 0.7, tN));
float spk = snoise(vRest * uSpotScale);
tc *= 1.0 - uSpots * smoothstep(0.2, 0.9, spk);
float vn = snoise(vRest * uVeinScale + vec3(3.1, 1.7, 5.2)) + 0.35 * snoise(vRest * uVeinScale * 3.0);
float vmask = smoothstep(-0.2, 0.4, snoise(vRest * uVeinScale * 0.35 + 9.0));
float ridge = (1.0 - smoothstep(0.0, uVeinWidth, abs(vn))) * vmask;
tc = mix(tc, uVein, ridge * uVeinAmt);
tc = mix(tc, uChar, clamp(vBurn, 0.0, 1.0) * 0.85);
diffuseColor.rgb *= tc;
totalEmissiveRadiance += tc * uSSS;`)
      .replace('#include <roughnessmap_fragment>', `#include <roughnessmap_fragment>
roughnessFactor = clamp(roughnessFactor + 0.25 * snoise(vRest * uScale * 0.5) + 0.4 * vBurn, 0.05, 1.0);`)
      .replace('#include <normal_fragment_maps>', `#include <normal_fragment_maps>
{
  float hgt = fbm3(vRest * uBumpScale) * uBump + uSpots * 0.4 * uBump * spk - ridge * uBump * 0.8 * uVeinAmt;
  normal = tissuePerturb(-vViewPosition, normal, clamp(vec2(dFdx(hgt), dFdy(hgt)) * 600.0, -0.6, 0.6), faceDirection);
}`);
  };
  m.customProgramCacheKey = () => 'tissue-v1';
  return m;
}

/** Give a geometry the attributes tissueMaterial needs. */
export function prepTissueGeometry(geo, rest = null) {
  const pos = geo.attributes.position;
  geo.setAttribute('restPos', new THREE.BufferAttribute(rest ? rest.slice() : new Float32Array(pos.array), 3));
  if (!geo.attributes.burn) geo.setAttribute('burn', new THREE.BufferAttribute(new Float32Array(pos.count), 1));
  return geo;
}

export const TISSUE = {
  liver: () => tissueMaterial({ polygonOffset: 2, sss: 0.02, sheen: 0.5, sheenColor: 0xff6a50, colA: 0x7a2618, colB: 0xa8412a, scale: 35, spots: 0.18, spotScale: 900, bump: 0.0012, bumpScale: 260, roughness: 0.5, clearcoat: 1, clearcoatRoughness: 0.06, vein: 0x3a0806, veinAmt: 0.15, veinScale: 30 }),
  gallbladder: () => tissueMaterial({ sss: 0.02, colA: 0x4e6e62, colB: 0x8aa088, scale: 30, vein: 0x9a1a1a, veinAmt: 0.75, veinScale: 70, veinWidth: 0.05, bump: 0.0008, bumpScale: 300, roughness: 0.45, clearcoat: 1, clearcoatRoughness: 0.05, sheen: 0.25, sheenColor: 0xd8e8e0 }),
  fat: (o = {}) => tissueMaterial({ colA: 0xd09a38, colB: 0xf0d070, scale: 120, spots: 0.12, spotScale: 500, bump: 0.0008, bumpScale: 260, roughness: 0.4, clearcoat: 1, clearcoatRoughness: 0.1, vein: 0x8a1a12, veinAmt: 0.35, veinScale: 90, ...o }),
  duct: () => tissueMaterial({ colA: 0x9c9670, colB: 0xcfc69c, scale: 150, vein: 0xa02020, veinAmt: 0.35, veinScale: 300, veinWidth: 0.08, bump: 0.0004, bumpScale: 600, roughness: 0.4, clearcoat: 1, clearcoatRoughness: 0.06, sheen: 0.4 }),
  artery: () => tissueMaterial({ colA: 0x9a2a28, colB: 0xc85a50, scale: 160, vein: 0x701010, veinAmt: 0.3, veinScale: 300, bump: 0.0003, bumpScale: 700, roughness: 0.35, clearcoat: 1, clearcoatRoughness: 0.05 }),
  peritoneum: () => tissueMaterial({ colA: 0xa04a3a, colB: 0xd08870, scale: 25, vein: 0x8a1010, veinAmt: 0.5, veinScale: 40, veinWidth: 0.05, bump: 0.002, bumpScale: 120, roughness: 0.5, clearcoat: 1, clearcoatRoughness: 0.12, side: THREE.BackSide }),
  bowel: () => tissueMaterial({ colA: 0xb06a5a, colB: 0xd8988a, scale: 60, vein: 0x9a2020, veinAmt: 0.55, veinScale: 80, bump: 0.0012, bumpScale: 200, roughness: 0.45, clearcoat: 1, clearcoatRoughness: 0.08 }),
};
