"""Fit the PixelClock City taxi (pixelclock-city-taxis.zip, Taxi_5) for Taxi_Enter / Taxi_Exit.

Runs inside bpy (called from props.build("Taxi")). The car is taken from the zip
in the repo root and:
  * turned and scaled (TAXI_SCALE) into character-root space: it faces -X with
    its right side toward the character, the ground at z=0;
  * the right rear door is cut out of the body along its outline (with the
    notch over the wheel arch) and becomes "Taxi_Door", pivoting on its front
    edge (animated per clip, see animations.TAXI_TRACKS);
  * the windows are made see-through and a simple cabin (floor, rear bench,
    front seat backs, dark lining) is added so the passenger can sit inside.

The numbers the animations use (door, sill, seat, roof) are in props.TAXI.
"""
import math
import os
import tempfile
import zipfile

import bmesh
import bpy
from mathutils import Matrix, Vector

import rigkit

ZIP = os.path.join(rigkit.REPO, "pixelclock-city-taxis.zip")
MODEL = "Taxis/Cars_Full/Models/Taxi/Taxi_5.glb"
TEXTURES = ("Taxis/Cars_Full/Textures/city_atlas.png", "Taxis/Cars_Full/Textures/windows.png")
TAXI_SCALE = 1.25  # so the big-headed characters fit under the roof when seated
# car-native -> character-root: turn 90 deg (forward +Y -> -X), scale, lift wheels onto the ground,
# and slide so the rear door opening sits where the clips expect it
NATIVE_GROUND = -0.25
OFFSET = Vector((-1.40, -1.47, 0.0))

# rear door outline in car-native coordinates (y along the car, + = forward; z up)
DOOR_FRONT_Y = -0.30   # front edge (hinge), at the B pillar
DOOR_REAR_Y = -0.925   # rear edge above the wheel arch
DOOR_ARCH_Y = -0.80    # rear edge beside the wheel arch
DOOR_ARCH_Z = 0.34     # top of the wheel arch notch
DOOR_TOP_Z = 1.53      # window frame top (roof line)
SIDE_MIN_X = 0.40      # faces right of this (and facing right) belong to the right-hand side
SIDE_X = 0.73          # outer face of the side panels
DOOR_BELT_Z = 0.74     # bottom of the side windows


def to_root(v):
    """Car-native point -> character-root space."""
    k = TAXI_SCALE
    return Vector((-v.y * k, v.x * k, (v.z - NATIVE_GROUND) * k)) + OFFSET


def native_matrix():
    k = TAXI_SCALE
    m = Matrix(((0, -k, 0, 0), (k, 0, 0, 0), (0, 0, k, -NATIVE_GROUND * k), (0, 0, 0, 1)))
    return Matrix.Translation(OFFSET) @ m


def _extract():
    out = os.path.join(tempfile.gettempdir(), "pixelclock_taxis")
    if not os.path.exists(os.path.join(out, MODEL)):
        with zipfile.ZipFile(ZIP) as z:
            for name in (MODEL,) + TEXTURES:
                z.extract(name, out)
    return os.path.join(out, MODEL)


def _material(name, rgba, rough=0.8):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = rgba
    b.inputs["Roughness"].default_value = rough
    return m


def _box(name, lo, hi, mat, parent):
    lo, hi = Vector(lo), Vector(hi)
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co = Vector(((v.co.x + 0.5) * (hi.x - lo.x) + lo.x, (v.co.y + 0.5) * (hi.y - lo.y) + lo.y,
                       (v.co.z + 0.5) * (hi.z - lo.z) + lo.z))
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mat)
    o = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(o)
    o.parent = parent
    return o


def _in_door(c):
    """Is this face centre (car-native) part of the right rear door?"""
    if c.x < SIDE_MIN_X or c.y > DOOR_FRONT_Y or c.z > DOOR_TOP_Z:
        return False
    if c.z < DOOR_BELT_Z and c.x < SIDE_X - 0.10:  # inner panels below the windows are not the door skin
        return False
    rear = DOOR_ARCH_Y if c.z < DOOR_ARCH_Z else DOOR_REAR_Y
    return c.y > rear


def build(root, door_track=None):
    """Import, fit and cut the taxi under `root`; returns the door object."""
    from props import TAXI
    path = _extract()
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    body = next(o for o in new if o.type == "MESH" and o.parent is None and o.name.startswith("Taxi"))
    wheels = [o for o in new if o is not body]
    # bake the importer's transform into the mesh (car-native coordinates)
    for o in [body] + wheels:
        o.data = o.data.copy()
    body.data.transform(body.matrix_basis)
    body.matrix_basis = Matrix.Identity(4)

    # cut along the door outline, then split the door faces off
    bm = bmesh.new()
    bm.from_mesh(body.data)
    geom = lambda: bm.verts[:] + bm.edges[:] + bm.faces[:]  # noqa: E731
    for co, no in ((Vector((0, DOOR_FRONT_Y, 0)), Vector((0, 1, 0))), (Vector((0, DOOR_REAR_Y, 0)), Vector((0, 1, 0))),
                   (Vector((0, DOOR_ARCH_Y, 0)), Vector((0, 1, 0))), (Vector((0, 0, DOOR_ARCH_Z)), Vector((0, 0, 1))),
                   (Vector((0, 0, DOOR_TOP_Z)), Vector((0, 0, 1)))):
        bmesh.ops.bisect_plane(bm, geom=geom(), plane_co=co, plane_no=no)
    door_faces = [f for f in bm.faces if f.normal.x > 0.6 and _in_door(f.calc_center_median())]  # side and glass, not the roof edge
    door_bm = bmesh.new()
    vmap = {}
    for f in door_faces:
        vs = []
        for v in f.verts:
            if v not in vmap:
                vmap[v] = door_bm.verts.new(v.co)
            vs.append(vmap[v])
        nf = door_bm.faces.new(vs)
        nf.material_index = f.material_index
        nf.smooth = f.smooth
    # UVs
    uv = bm.loops.layers.uv.active
    duv = door_bm.loops.layers.uv.new(uv.name) if uv else None
    if uv:
        for f, nf in zip(door_faces, door_bm.faces):
            for l, nl in zip(f.loops, nf.loops):
                nl[duv].uv = l[uv].uv
    bmesh.ops.delete(bm, geom=door_faces, context="FACES_ONLY")
    bm.to_mesh(body.data)
    bm.free()
    door_me = bpy.data.meshes.new("Taxi_Door")
    door_bm.to_mesh(door_me)
    door_bm.free()
    for m in body.data.materials:
        door_me.materials.append(m)

    # into character-root space
    nm = native_matrix()
    body.data.transform(nm)
    for w in wheels:
        w.parent = None
        w.matrix_basis = nm @ w.matrix_basis
    door_me.transform(nm)
    hinge = Vector(TAXI["hinge"])
    door_me.transform(Matrix.Translation(-hinge))
    door = bpy.data.objects.new("Taxi_Door", door_me)
    bpy.context.scene.collection.objects.link(door)
    door.parent = root
    door.location = hinge
    sol = door.modifiers.new("thickness", "SOLIDIFY")
    sol.thickness = 0.035
    sol.offset = -1.0
    body.name = "Taxi_Body"
    body.parent = root
    for w in wheels:
        w.parent = root

    # see-through windows (the passenger shows); paint stays as it is
    for m in body.data.materials:
        if m.name.startswith("Windows"):
            b = m.node_tree.nodes["Principled BSDF"]
            b.inputs["Alpha"].default_value = 0.35
            m.blend_method = "BLEND"

    # simple cabin
    lining = _material("Taxi_Lining", (0.10, 0.09, 0.09, 1))
    seat = _material("Taxi_Seat", (0.28, 0.20, 0.15, 1))
    t = TAXI
    sy, far = t["side_y"], t["side_y"] - t["width"]
    floor = t["floor_z"]
    _box("Taxi_CabinFloor", (t["cabin_front_x"], far + 0.08, floor - 0.04), (t["cabin_rear_x"], sy - 0.06, floor), lining, root)
    _box("Taxi_RearSeat", (t["seat_front_x"], far + 0.10, floor), (t["seat_back_x"], sy - 0.10, t["seat_z"]), seat, root)
    _box("Taxi_RearBack", (t["seat_back_x"], far + 0.10, t["seat_z"]),
         (t["seat_back_x"] + 0.14, sy - 0.10, t["seat_z"] + 0.62), seat, root)
    _box("Taxi_FrontBack", (t["front_seat_x"] - 0.12, far + 0.12, floor + 0.10),
         (t["front_seat_x"], sy - 0.12, floor + 0.85), seat, root)
    # step below the door opening, up to the cabin floor (the body shell is open underneath)
    hx = t["hinge"].x
    _box("Taxi_Sill", (hx + 0.02, sy - 0.10, 0.30), (hx + t["door_w"] - 0.02, sy - 0.03, floor), lining, root)
    _box("Taxi_FarLining", (t["cabin_front_x"], far + 0.06, floor), (t["cabin_rear_x"], far + 0.09, t["belt_z"]), lining, root)
    return door
