import bpy, sys
from mathutils import Vector
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=sys.argv[-1])
dg = bpy.context.evaluated_depsgraph_get()
for o in bpy.context.scene.objects:
    if o.type != 'MESH': continue
    e = o.evaluated_get(dg); m = e.to_mesh()
    vs = [e.matrix_world @ v.co for v in m.vertices]
    if not vs: continue
    mn = [min(v[i] for v in vs) for i in range(3)]; mx = [max(v[i] for v in vs) for i in range(3)]
    c = sum(vs, Vector()) / len(vs)
    print('EVAL', o.name, o.data.materials[0].name if o.data.materials else '-', len(m.polygons), [round(x, 3) for x in mn], [round(x, 3) for x in mx], 'c', [round(x, 3) for x in c])
    e.to_mesh_clear()
