// Optional WebXR emulation with Meta's IWER (Immersive Web Emulation Runtime) so
// the app can be driven without a headset: add ?emulate=1 (and &devui=1 for the
// IWER DevUI overlay). Used by tests/screenshots.mjs in headless Chrome.
export async function maybeEmulate(params) {
  if (!params.has('emulate')) return null;
  const { XRDevice, metaQuest3 } = await import('iwer');
  const device = new XRDevice(metaQuest3);
  device.installRuntime({ forceInstall: true });
  device.stereoEnabled = params.get('stereo') === '1';
  if (params.has('devui')) {
    const { DevUI } = await import('@iwer/devui');
    device.installDevUI(DevUI);
  }
  return device;
}
