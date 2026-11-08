# Build the draped patient for the OR (run headless):
#   blender -b --python scripts/blender/build_patient.py -- <body.glb> <out.glb> <landmarks.json> '<ports-json>'
# ports-json: {"umbilical":[x,y,z], ...} in three.js OR coordinates (y up, metres).
#
# - bakes the CC-BY "Injured Super Human" scan pose into static meshes, lays it supine on
#   the table (head +X, face up), navel under the trocar cluster, arms out on arm boards
# - cloth simulation (Blender cloth) pass 1: forced-air warming blanket over the legs;
#   pass 2: fenestrated laparoscopy drape (window at the ports), anaesthesia screen drape
#   hung over its bar, thigh safety strap
# - prep stain + incise film over the abdominal window, cap, eye pads, tape, ET tube +
#   circuit, pulse-oximeter clip; exports one GLB + landmarks JSON for the JS side.
# Blender coords: X = three X, Y = -three Z, Z = three Y.
import bpy, bmesh, sys, json, math
from mathutils import Vector, Matrix
from mathutils.bvhtree import BVHTree

argv = sys.argv[sys.argv.index('--') + 1:]
BODY, OUT, OUT_JSON, PORTS = argv[0], argv[1], argv[2], json.loads(argv[3])
TABLE_TOP, TABLE_X, TABLE_Y, TABLE_LEN, TABLE_W = 0.725, 0.1, 0.75, 2.05, 0.55
HEIGHT = 1.78
def B(p3): return Vector((p3[0], -p3[2], p3[1]))
def T(pb): return [round(pb.x, 4), round(pb.z, 4), round(-pb.y, 4)]

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
bpy.ops.import_scene.gltf(filepath=BODY)
dg = bpy.context.evaluated_depsgraph_get()

# ---------------------------------------------------------------- bake the body
DROP = {'Hair', 'Beard', 'eyebrows', 'Thin_Hair'}
parts = []
for o in list(scene.objects):
    if o.type != 'MESH' or not o.data.materials or o.data.materials[0].name in DROP: continue
    e = o.evaluated_get(dg)
    me = bpy.data.meshes.new_from_object(e)
    ob = bpy.data.objects.new(o.name + '_b', me); ob.matrix_world = e.matrix_world.copy()
    scene.collection.objects.link(ob); parts.append(ob)
for o in list(scene.objects):
    if o not in parts: bpy.data.objects.remove(o, do_unlink=True)
for ob in parts:  # apply transforms (the rig has a mirrored transform: fix face winding afterwards)
    flip = ob.matrix_world.determinant() < 0
    ob.data.transform(ob.matrix_world); ob.matrix_world = Matrix.Identity(4)
    if flip: ob.data.flip_normals()
with bpy.context.temp_override(active_object=parts[0], selected_editable_objects=parts, object=parts[0]):
    bpy.ops.object.join()
body = parts[0]; body.name = 'PatientBody'
# source frame: head -Z, anterior +Y  ->  head +X, anterior +Z
R = Matrix(((0, 0, -1), (-1, 0, 0), (0, 1, 0))).to_4x4()
body.data.transform(R)
vs = [v.co for v in body.data.vertices]
xmin, xmax = min(v.x for v in vs), max(v.x for v in vs)
s = HEIGHT / (xmax - xmin)
body.data.transform(Matrix.Scale(s, 4))
vs = [v.co for v in body.data.vertices]
xmin = min(v.x for v in vs); ymin, ymax = min(v.y for v in vs), max(v.y for v in vs); zmin = min(v.z for v in vs)
navel_target_x = sum(p[0] for p in PORTS.values()) / len(PORTS) - 0.02
body.data.transform(Matrix.Translation((navel_target_x - (xmin + 0.585 * HEIGHT), TABLE_Y - (ymin + ymax) / 2, TABLE_TOP + 0.004 - zmin)))
body.data.update()
vs = [v.co.copy() for v in body.data.vertices]
xmin, xmax = min(v.x for v in vs), max(v.x for v in vs)

def box(name, size, loc, rot=0.0, bevel=0.006):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    o = bpy.context.active_object; o.name = name; o.scale = size; o.rotation_euler.z = rot
    bpy.ops.object.transform_apply(scale=True, rotation=True)
    if bevel:
        m = o.modifiers.new('bev', 'BEVEL'); m.width = bevel; m.segments = 3
    return o
def mat(o, name):
    m = bpy.data.materials.get(name)
    if not m:  # distinct base colours so glTF optimisers don't dedupe/merge them (names are used by the viewer)
        m = bpy.data.materials.new(name); h = (sum(map(ord, name)) * 37 % 255) / 255
        m.diffuse_color = (h, (h * 7) % 1, (h * 13) % 1, 1); m.use_nodes = True
        m.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = m.diffuse_color
    o.data.materials.clear(); o.data.materials.append(m)
def collide(o, outer=0.004):
    c = o.modifiers.new('col', 'COLLISION'); o.collision.thickness_outer = outer; o.collision.cloth_friction = 8

# table top proxy (collision only) + arm boards
table = box('TableProxy', (TABLE_LEN, TABLE_W, 0.08), (TABLE_X, TABLE_Y, TABLE_TOP - 0.04), bevel=0)
collide(table)
landmarks = {}
for side, sign in (('L', -1), ('R', 1)):  # patient's left = blender -Y (towards the surgeon)
    arm = [v for v in vs if (v.y - TABLE_Y) * sign > 0.24 and v.x > xmin + 0.85]
    hand = max(arm, key=lambda v: (v.y - TABLE_Y) * sign)
    shoulder = Vector((sum(v.x for v in arm) / len(arm), TABLE_Y + sign * 0.2, 0))
    d = Vector((hand.x - shoulder.x, hand.y - shoulder.y, 0)); L = d.length; d.normalize()
    ang = math.atan2(d.y, d.x)
    bottom = min(v.z for v in arm)
    mid = shoulder + d * (L * 0.62)
    ab = box(f'ArmBoard{side}', (L * 0.85, 0.16, 0.045), (mid.x, mid.y, bottom - 0.0235), ang, 0.012); mat(ab, 'armboard_pad'); collide(ab)
    mount = box(f'ArmBoardMount{side}', (0.08, 0.06, 0.05), (shoulder.x + d.x * 0.1, TABLE_Y + sign * (TABLE_W / 2 + 0.02), TABLE_TOP - 0.06), 0, 0.004); mat(mount, 'armboard_metal')
    fore = shoulder + d * (L * 0.72)
    top = max((v.z for v in arm if (Vector((v.x, v.y, 0)) - Vector((fore.x, fore.y, 0))).length < 0.06), default=bottom + 0.08)
    st = box(f'ArmStrap{side}', (0.05, 0.19, 0.006), (fore.x, fore.y, top + 0.004), ang, 0.002); mat(st, 'strap')
    landmarks[f'fingertip{side}'] = T(hand); landmarks[f'forearm{side}'] = T(Vector((fore.x, fore.y, top)))
    landmarks[f'hand{side}'] = T(shoulder + d * (L * 0.93) + Vector((0, 0, bottom + 0.03)))
# collision proxy: the CC body is many open shells, so cloth falls through it. A voxel-remeshed
# closed copy (not exported) is a robust collider.
proxy = bpy.data.objects.new('BodyCollider', body.data.copy()); scene.collection.objects.link(proxy)
rm = proxy.modifiers.new('remesh', 'REMESH'); rm.mode = 'VOXEL'; rm.voxel_size = 0.012
pm = bpy.data.meshes.new_from_object(proxy.evaluated_get(bpy.context.evaluated_depsgraph_get()))
proxy.modifiers.clear(); proxy.data = pm
collide(proxy, 0.006); proxy.collision.thickness_inner = 0.03
print('PROXY faces', len(pm.polygons))

# BVH of the body for ray casts (skin heights at the ports, head features)
bm = bmesh.new(); bm.from_mesh(body.data); bvh = BVHTree.FromBMesh(bm)
def skin_z(x, y):
    hit = bvh.ray_cast(Vector((x, y, 2.0)), Vector((0, 0, -1)))
    return hit[0].z if hit[0] else None
landmarks['skinAtPorts'] = {k: skin_z(B(p).x, B(p).y) for k, p in PORTS.items()}

# ---------------------------------------------------------------- cloth helpers
def grid(name, x0, x1, y0, y1, z, res=0.02):
    nx, ny = max(2, int((x1 - x0) / res)), max(2, int((y1 - y0) / res))
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=nx, y_subdivisions=ny, size=1, location=((x0 + x1) / 2, (y0 + y1) / 2, z))
    o = bpy.context.active_object; o.name = name; o.scale = (x1 - x0, y1 - y0, 1)
    bpy.ops.object.transform_apply(scale=True)
    return o
def cut(o, pred):
    bm2 = bmesh.new(); bm2.from_mesh(o.data)
    bmesh.ops.delete(bm2, geom=[f for f in bm2.faces if pred(f.calc_center_median())], context='FACES')
    bm2.to_mesh(o.data); bm2.free()
def cloth(o, mass=0.15, stiff=8, bend=0.4, pin=None):
    m = o.modifiers.new('Cloth', 'CLOTH'); st = m.settings
    st.quality = 6; st.mass = mass; st.tension_stiffness = st.compression_stiffness = st.shear_stiffness = stiff; st.bending_stiffness = bend
    st.air_damping = 2.0
    m.collision_settings.distance_min = 0.004; m.collision_settings.collision_quality = 3; m.collision_settings.use_self_collision = False
    if pin: st.vertex_group_mass = pin
    m.point_cache.frame_start = 1; m.point_cache.frame_end = 80
    return m
def simulate(objs, frames=70):
    for f in range(1, frames + 1): scene.frame_set(f)
    out = []
    dg2 = bpy.context.evaluated_depsgraph_get()
    for o in objs:
        me = bpy.data.meshes.new_from_object(o.evaluated_get(dg2))
        n = bpy.data.objects.new(o.name, me); scene.collection.objects.link(n)
        bpy.data.objects.remove(o, do_unlink=True); n.name = n.name.replace('.001', ''); out.append(n)
    scene.frame_set(1)
    return out

scene.frame_start, scene.frame_end = 1, 80
# pass 1: forced-air warming blanket over the legs
blanket = grid('WarmingBlanket', xmin - 0.12, xmin + 0.78, TABLE_Y - 0.45, TABLE_Y + 0.45, TABLE_TOP + 0.35, 0.025)
cloth(blanket, mass=0.25, stiff=12, bend=2.0)
blanket, = simulate([blanket]); mat(blanket, 'blanket_fabric'); collide(blanket, 0.003)

# pass 2: fenestrated lap drape, thigh strap, anaesthesia screen
ports_b = [B(p) for p in PORTS.values()]
wx0, wx1 = min(p.x for p in ports_b) - 0.06, max(p.x for p in ports_b) + 0.06
wy0, wy1 = min(p.y for p in ports_b) - 0.06, max(p.y for p in ports_b) + 0.06
head_edge = xmax - 0.36   # drape stops at the anaesthesia screen (upper chest)
drape = grid('LapDrape', xmin + 0.3, head_edge, TABLE_Y - 0.8, TABLE_Y + 0.8, TABLE_TOP + 0.55, 0.02)
cut(drape, lambda c: (wx0 < c.x < wx1 and wy0 < c.y < wy1) or (c.x > head_edge - 0.3 and abs(c.y - TABLE_Y) > 0.32))
dvg = drape.vertex_groups.new(name='pin')  # head edge clipped to the anaesthesia screen bar
dvg.add([v.index for v in drape.data.vertices if v.co.x > head_edge - 0.012 and abs(v.co.y - TABLE_Y) < 0.3], 1.0, 'REPLACE')
cloth(drape, mass=0.12, stiff=10, bend=0.3, pin='pin')
strap = grid('ThighStrap', xmin + 0.42, xmin + 0.48, TABLE_Y - 0.42, TABLE_Y + 0.42, TABLE_TOP + 0.4, 0.015)
cloth(strap, mass=0.2, stiff=40, bend=5)
# anaesthesia screen: posts + crossbar, drape hung from the bar (pinned middle row)
bx = head_edge + 0.01
for sgn in (-1, 1):
    bpy.ops.mesh.primitive_cylinder_add(radius=0.009, depth=0.62, location=(bx, TABLE_Y + sgn * (TABLE_W / 2 + 0.03), TABLE_TOP + 0.25)); mat(bpy.context.active_object, 'bar_metal')
bpy.ops.mesh.primitive_cylinder_add(radius=0.009, depth=TABLE_W + 0.06, location=(bx, TABLE_Y, TABLE_TOP + 0.56), rotation=(math.pi / 2, 0, 0)); bar = bpy.context.active_object; mat(bar, 'bar_metal'); collide(bar, 0.004)
screen = grid('ScreenDrape', bx - 0.35, bx + 0.35, TABLE_Y - 0.48, TABLE_Y + 0.48, TABLE_TOP + 0.575, 0.025)
vg = screen.vertex_groups.new(name='pin'); vg.add([v.index for v in screen.data.vertices if abs(v.co.x - bx) < 0.015], 1.0, 'REPLACE')
cloth(screen, mass=0.12, stiff=10, bend=0.3, pin='pin')
drape, strap, screen = simulate([drape, strap, screen])
mat(drape, 'drape_fabric'); mat(strap, 'strap'); mat(screen, 'drape_fabric')
for o in (drape, strap, screen):
    so = o.modifiers.new('thick', 'SOLIDIFY'); so.thickness = 0.002 if o is not strap else 0.003; so.offset = 1

# ---------------------------------------------------------------- prep stain + incise film (copies of the skin in the window)
def skin_patch(name, margin, offset):
    bm3 = bmesh.new(); bm3.from_mesh(body.data); bm3.normal_update()
    keep = set(f for f in bm3.faces if (wx0 - margin < f.calc_center_median().x < wx1 + margin and wy0 - margin < f.calc_center_median().y < wy1 + margin and f.normal.z > 0.35))
    bmesh.ops.delete(bm3, geom=[f for f in bm3.faces if f not in keep], context='FACES')
    for v in bm3.verts: v.co += v.normal * offset
    uv = bm3.loops.layers.uv.verify()
    for f in bm3.faces:
        for l in f.loops:
            l[uv].uv = ((l.vert.co.x - (wx0 - margin)) / (wx1 - wx0 + 2 * margin), (l.vert.co.y - (wy0 - margin)) / (wy1 - wy0 + 2 * margin))
    me = bpy.data.meshes.new(name); bm3.to_mesh(me); bm3.free()
    o = bpy.data.objects.new(name, me); scene.collection.objects.link(o)
    me.materials.clear()
    return o
prep = skin_patch('PrepStain', 0.07, 0.0008); mat(prep, 'prep_stain')
film = skin_patch('InciseFilm', 0.035, 0.0018); mat(film, 'incise_film')

# ---------------------------------------------------------------- head: cap, eye pads, tape, ET tube, circuit
head = [v for v in vs if v.x > xmax - 0.24 and abs(v.y - TABLE_Y) < 0.12]
hc = Vector((sum(v.x for v in head) / len(head), sum(v.y for v in head) / len(head), sum(v.z for v in head) / len(head)))
face_z = max(v.z for v in head)
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.105, location=(hc.x + 0.03, hc.y, hc.z + 0.005)); cap = bpy.context.active_object; cap.name = 'Cap'
cap.scale = (1.05, 0.95, 0.85); mat(cap, 'cap_fabric')
eyes = [v for v in head if v.x > xmax - 0.13 and v.x < xmax - 0.07]
for sgn in (-1, 1):
    ey = Vector((xmax - 0.105, TABLE_Y + sgn * 0.033, 0)); ey.z = (skin_z(ey.x, ey.y) or face_z) + 0.003
    e = box('EyePad', (0.035, 0.03, 0.004), ey, 0, 0.003); mat(e, 'eyepad')
nose_z = face_z
mouth = Vector((xmax - 0.165, TABLE_Y, 0)); mouth.z = (skin_z(mouth.x, mouth.y) or face_z - 0.02) + 0.005
t = box('Tape', (0.018, 0.12, 0.003), (mouth.x, TABLE_Y, mouth.z + 0.002), 0, 0.001); mat(t, 'tape')
def tube(name, pts, r, m):
    cu = bpy.data.curves.new(name, 'CURVE'); cu.dimensions = '3D'; cu.bevel_depth = r; cu.bevel_resolution = 3
    sp = cu.splines.new('BEZIER'); sp.bezier_points.add(len(pts) - 1)
    for bp, p in zip(sp.bezier_points, pts): bp.co = p; bp.handle_left_type = bp.handle_right_type = 'AUTO'
    o = bpy.data.objects.new(name, cu); scene.collection.objects.link(o)
    me = bpy.data.meshes.new_from_object(o.evaluated_get(bpy.context.evaluated_depsgraph_get()))
    mo = bpy.data.objects.new(name, me); scene.collection.objects.link(mo); bpy.data.objects.remove(o, do_unlink=True); mat(mo, m); return mo
et_end = Vector((mouth.x + 0.07, TABLE_Y + 0.05, mouth.z + 0.09))
tube('ETTube', [mouth + Vector((0, 0, -0.01)), mouth + Vector((0.01, 0, 0.05)), et_end], 0.0045, 'ettube')
machine = B([1.95, 1.0, -0.75])
tube('Circuit', [et_end, et_end + Vector((0.12, 0.03, 0.02)), Vector((machine.x - 0.35, machine.y + 0.08, TABLE_TOP + 0.25)), Vector((machine.x - 0.32, machine.y + 0.1, machine.z - 0.05))], 0.011, 'circuit')
landmarks['etEnd'] = T(et_end)
# pulse oximeter clip on the left index finger
fl = B(landmarks['fingertipL'])
po = box('PulseOx', (0.035, 0.022, 0.024), (fl.x, fl.y + 0.012, fl.z + 0.004), 0, 0.006); mat(po, 'pulseox')
landmarks['pulseOx'] = T(Vector((fl.x, fl.y + 0.012, fl.z + 0.004)))
landmarks['chestLeads'] = [T(Vector((head_edge + 0.02, TABLE_Y + dy, TABLE_TOP + 0.24))) for dy in (-0.11, 0.0, 0.1)]
landmarks['bodyBounds'] = {'xmin': xmin, 'xmax': xmax}
landmarks['window'] = {'x0': wx0, 'x1': wx1, 'z0': -wy1, 'z1': -wy0}

# ---------------------------------------------------------------- export
for o in scene.objects:  # smooth shading everywhere (new objects default to flat)
    if o.type == 'MESH':
        for pg in o.data.polygons: pg.use_smooth = True
bpy.data.objects.remove(table, do_unlink=True); bpy.data.objects.remove(proxy, do_unlink=True)
for o in scene.objects:
    for m in list(o.modifiers):
        if m.type == 'COLLISION': o.modifiers.remove(m)
for o in scene.objects: o.select_set(o.type == 'MESH')
bpy.ops.export_scene.gltf(filepath=OUT, export_format='GLB', use_selection=True, export_apply=True, export_yup=True)
json.dump(landmarks, open(OUT_JSON, 'w'), indent=1, default=lambda v: None)
print('DONE', OUT, {o.name: len(o.data.polygons) for o in scene.objects if o.type == 'MESH'})
