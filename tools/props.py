"""Simple low-poly props that go with the new animations (runs inside bpy).

Bone props (Box, Umbrella, Phone) are exported with their attachment offset
baked into the root node, in the bone's local space, so in Godot you add a
BoneAttachment3D for the listed bone and instance the prop under it with an
identity transform. The Bicycle is placed relative to the character root.

Offsets are measured from reference frames of the animations (see
animations.PROP_REFS) and stored in tools/prop_offsets.json by the build.
"""
import json
import math
import os

import bpy
from mathutils import Matrix, Vector

import rigkit

OFFSETS_JSON = os.path.join(os.path.dirname(__file__), "prop_offsets.json")
PROPS_DIR = os.path.join(rigkit.PACK, "Props")

# Which props each animation uses in previews (Chair is preview-only)
ANIM_PROPS = {
    "Carry_Box_Idle-loop": ["Box"],
    "Carry_Box_Walk-loop": ["Box"],
    "Umbrella_Idle-loop": ["Umbrella"],
    "Umbrella_Walk-loop": ["Umbrella"],
    "Phone_Idle-loop": ["Phone"],
    "Phone_Walk-loop": ["Phone"],
    "Phone_Talk-loop": ["Phone"],
    "Cycling-loop": ["Bicycle"],
    "Cycling_Coast-loop": ["Bicycle"],
    "Cycling_Stop": ["Bicycle"],
    "Cycling_Rest-loop": ["Bicycle"],
    "Cycling_Start": ["Bicycle"],
    "Sit_Chair_Idle-loop": ["Chair"],
    "Sit_Chair_Down": ["Chair"],
    "Sit_Chair_StandUp": ["Chair"],
    "Sit_Chair_Talk-loop": ["Chair"],
    "Cello_Carry_Idle-loop": ["Cello_Carried"],
    "Cello_Carry_Walk-loop": ["Cello_Carried"],
    "Cello_Play-loop": ["Cello_Played", "Bow", "Chair"],
    "Cello_Rest-loop": ["Cello_Played", "Bow", "Chair"],
}

BIKE = {
    "crank": Vector((0, -0.10, 0.27)),
    "crank_r": 0.085,
    "pedal_x": 0.15,
    "seat": Vector((0, 0.13, 0.50)),
    "grips": 0.21,
    "bar": Vector((0, -0.43, 0.76)),
    "rear": Vector((0, 0.40, 0.23)),
    "front": Vector((0, -0.62, 0.23)),
    "wheel_r": 0.23,
}


# ---------------------------------------------------------------------------
# geometry helpers


def material(name, rgba, rough=0.7, metal=0.0):
    m = bpy.data.materials.get(name)
    if m:
        return m
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = rgba
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    m.use_backface_culling = False
    return m


def _finish(obj, mat, parent=None):
    obj.data.materials.append(mat)
    if parent is not None:
        obj.parent = parent
    for poly in obj.data.polygons:
        poly.use_smooth = False
    return obj


def box(name, size, loc, mat, parent=None, rot=None):
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0))
    o = bpy.context.active_object
    o.name = name
    o.scale = size
    bpy.ops.object.transform_apply(scale=True)
    o.location = loc
    if rot is not None:
        o.rotation_euler = rot
    return _finish(o, mat, parent)


def cylinder_between(name, a, b, radius, mat, parent=None, verts=8):
    a, b = Vector(a), Vector(b)
    d = b - a
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=radius, depth=d.length, location=(a + b) / 2)
    o = bpy.context.active_object
    o.name = name
    o.rotation_mode = "QUATERNION"
    o.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(d.normalized())
    return _finish(o, mat, parent)


def empty(name, parent=None):
    e = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(e)
    e.parent = parent
    return e


# ---------------------------------------------------------------------------
# props (built in their own local space)


def build_box(root):
    card = material("Cardboard", (0.62, 0.43, 0.24, 1))
    tape = material("Tape", (0.78, 0.66, 0.46, 1))
    box("Box_Body", (0.36, 0.28, 0.30), (0, 0, 0), card, root)
    box("Box_Tape", (0.37, 0.07, 0.305), (0, 0, 0.001), tape, root)


def build_umbrella(root):
    # local frame: shaft along +Y through the origin (the fist), canopy at +Y
    cloth = material("Umbrella_Cloth", (0.80, 0.12, 0.14, 1))
    dark = material("Umbrella_Handle", (0.12, 0.08, 0.06, 1))
    metal = material("Umbrella_Metal", (0.6, 0.6, 0.62, 1), 0.3, 1.0)
    cylinder_between("Umbrella_Shaft", (0, -0.06, 0), (0, 1.0, 0), 0.012, metal, root, 6)
    cylinder_between("Umbrella_Grip", (0, -0.10, 0), (0, 0.06, 0), 0.025, dark, root, 8)
    depth = 0.24
    bpy.ops.mesh.primitive_cone_add(vertices=8, radius1=0.70, radius2=0.0, depth=depth,
                                    end_fill_type="NOTHING", location=(0, 1.0 - depth / 2, 0),
                                    rotation=(-math.pi / 2, 0, 0))
    c = bpy.context.active_object
    c.name = "Umbrella_Canopy"
    _finish(c, cloth, root)


def build_phone(root):
    # local frame: long axis +Y, screen faces +Z, origin at the phone centre
    body = material("Phone_Body", (0.08, 0.08, 0.1, 1), 0.4)
    screen = material("Phone_Screen", (0.25, 0.55, 0.85, 1), 0.2)
    box("Phone_Body", (0.09, 0.17, 0.016), (0, 0, 0), body, root)
    box("Phone_Screen", (0.078, 0.15, 0.002), (0, 0, 0.0085), screen, root)


def build_chair(root):
    wood = material("Chair_Wood", (0.45, 0.30, 0.18, 1))
    seat_z = 0.27
    box("Chair_Seat", (0.42, 0.40, 0.05), (0, 0.17, seat_z - 0.025), wood, root)
    for x in (-0.18, 0.18):
        for y in (0.0, 0.34):
            box("Chair_Leg", (0.04, 0.04, seat_z - 0.05), (x, y, (seat_z - 0.05) / 2), wood, root)
    box("Chair_Back", (0.42, 0.04, 0.42), (0, 0.36, seat_z + 0.23), wood, root)


def build_bicycle(root, frames=None):
    b = BIKE
    paint = material("Bike_Paint", (0.10, 0.45, 0.75, 1), 0.4)
    black = material("Bike_Rubber", (0.05, 0.05, 0.05, 1), 0.9)
    metal = material("Bike_Metal", (0.65, 0.65, 0.68, 1), 0.3, 1.0)
    seat_m = material("Bike_Seat", (0.15, 0.1, 0.08, 1), 0.8)
    # everything hangs off Bike_Lean, which pivots about the ground line (roll about Y) when stopped
    lean = empty("Bike_Lean", root)
    frame = empty("Bike_Frame", lean)
    crank, seat, bar = b["crank"], b["seat"], b["bar"]
    head_low = b["front"].lerp(bar, 0.45)
    t = 0.022
    cylinder_between("SeatTube", crank, seat - Vector((0, 0, 0.03)), t, paint, frame)
    cylinder_between("DownTube", crank, head_low, t, paint, frame)
    cylinder_between("TopTube", seat.lerp(crank, 0.25), bar.lerp(b["front"], 0.25), t, paint, frame)
    for x in (-0.04, 0.04):
        off = Vector((x, 0, 0))
        cylinder_between("ChainStay", crank + off, b["rear"] + off, 0.012, paint, frame)
        cylinder_between("SeatStay", seat.lerp(crank, 0.2) + off, b["rear"] + off, 0.012, paint, frame)
        cylinder_between("Fork", b["front"] + off, head_low + off * 0.5, 0.013, paint, frame)
    cylinder_between("HeadTube", head_low, bar, t, paint, frame)
    cylinder_between("Bar", Vector((-b["grips"], bar.y, bar.z)), Vector((b["grips"], bar.y, bar.z)), 0.014, metal, frame)
    for x in (-b["grips"], b["grips"]):
        cylinder_between("Grip", Vector((x - 0.04, bar.y, bar.z)), Vector((x + 0.04, bar.y, bar.z)), 0.02, black, frame)
    box("Saddle", (0.1, 0.2, 0.04), seat, seat_m, frame)
    wheels = []
    for name, c in (("Bike_WheelRear", b["rear"]), ("Bike_WheelFront", b["front"])):
        w = empty(name, lean)
        w.location = c
        bpy.ops.mesh.primitive_torus_add(major_radius=b["wheel_r"] - 0.02, minor_radius=0.022,
                                         major_segments=16, minor_segments=6, location=(0, 0, 0),
                                         rotation=(0, math.pi / 2, 0))
        tire = bpy.context.active_object
        tire.name = name + "_Tire"
        _finish(tire, black, w)
        for k in range(4):
            a = k * math.pi / 4
            d = Vector((0, math.cos(a), math.sin(a))) * (b["wheel_r"] - 0.03)
            cylinder_between("Spoke", -d, d, 0.005, metal, w, 4)
        cylinder_between("Hub", Vector((-0.04, 0, 0)), Vector((0.04, 0, 0)), 0.02, metal, w)
        wheels.append(w)
    cr = empty("Bike_Crank", lean)
    cr.location = crank
    r = b["crank_r"]
    bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=0.06, depth=0.01, location=(0.06, 0, 0),
                                        rotation=(0, math.pi / 2, 0))
    ring = bpy.context.active_object
    ring.name = "Chainring"
    _finish(ring, metal, cr)
    cylinder_between("Axle", Vector((-b["pedal_x"] + 0.04, 0, 0)), Vector((b["pedal_x"] - 0.04, 0, 0)), 0.012, metal, cr)
    # left arm points down at angle 0 (see animations.cycling: left pedal at theta, right at theta+pi)
    for sx, sign in ((1, 1), (-1, -1)):
        x = sx * (b["pedal_x"] - 0.03)
        end = Vector((x, 0, -r * sign))
        cylinder_between("CrankArm", Vector((x, 0, 0)), end, 0.01, metal, cr)
        p = empty("Pedal_" + ("L" if sx > 0 else "R"), cr)
        p.location = end + Vector((sx * 0.04, 0, 0))
        box("Pedal", (0.07, 0.08, 0.015), (0, 0, 0), black, p)
    return lean, wheels, cr


def animate_bicycle(lean, wheels, crank, frames, name, state):
    """Key the bike into NLA tracks named `name`, one key per frame.

    state(f, frames) -> (lean degrees, crank angle, wheel angle), shared with the rider
    animation (animations.BIKE_TRACKS) so the two always match.
    """
    pedals = [c for c in crank.children if c.name.startswith("Pedal_")]
    # rotating about +X: a positive angle moves the top forward (-Y) -> riding forward;
    # the pedals counter-rotate to stay level; the lean rolls about +Y (+ = to the rider's left)
    parts = [(lean, 1, lambda st: math.radians(st[0])), (crank, 0, lambda st: st[1]),
             (wheels[0], 0, lambda st: st[2]), (wheels[1], 0, lambda st: st[2])]
    parts += [(p, 0, lambda st: -st[1]) for p in pedals]
    states = [state(f, frames) for f in range(frames + 1)]
    for obj, axis, value in parts:
        obj.rotation_mode = "XYZ"
        obj.animation_data_create()
        act = bpy.data.actions.new(f"{name}_{obj.name}")
        act.id_root = "OBJECT"
        obj.animation_data.action = act
        for f, st in enumerate(states):
            rot = [0.0, 0.0, 0.0]
            rot[axis] = value(st)
            obj.rotation_euler = rot
            obj.keyframe_insert("rotation_euler", frame=f)
        for fc in act.fcurves:
            for kp in fc.keyframe_points:
                kp.interpolation = "LINEAR"
        track = obj.animation_data.nla_tracks.new()
        track.name = name
        track.strips.new(name, 0, act)
        obj.animation_data.action = None


def ellipsoid(name, radii, loc, mat, parent=None):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, radius=1, location=(0, 0, 0))
    o = bpy.context.active_object
    o.name = name
    o.scale = radii
    bpy.ops.object.transform_apply(scale=True)
    o.location = loc
    return _finish(o, mat, parent)


# The cartoon heads are huge, so a cello scaled to body height looks like a viola;
# everything below is modelled at "unit" size and scaled by this factor.
CELLO_SCALE = 1.35
CELLO_ENDPIN = 0.07  # endpin length when played (unit size), short so the body sits at chest height


def build_cello(root, endpin=True):
    # local frame: +Y up the instrument, origin where the endpin leaves the body,
    # strings on the +Z side, width along X (modelled at unit size, scaled by CELLO_SCALE)
    first = len(root.children)
    wood = material("Cello_Wood", (0.55, 0.22, 0.07, 1), 0.45)
    dark = material("Cello_Ebony", (0.04, 0.03, 0.03, 1), 0.5)
    light = material("Cello_Bridge", (0.85, 0.7, 0.5, 1), 0.6)
    metal = material("Cello_Metal", (0.75, 0.75, 0.78, 1), 0.3, 1.0)
    ellipsoid("Cello_LowerBout", (0.205, 0.20, 0.07), (0, 0.19, 0), wood, root)
    ellipsoid("Cello_UpperBout", (0.17, 0.155, 0.065), (0, 0.45, 0), wood, root)
    box("Cello_Neck", (0.045, 0.30, 0.04), (0, 0.72, 0.015), wood, root)
    box("Cello_Fingerboard", (0.055, 0.44, 0.014), (0, 0.66, 0.062), dark, root)
    box("Cello_Pegbox", (0.045, 0.09, 0.045), (0, 0.905, 0.01), wood, root)
    ellipsoid("Cello_Scroll", (0.03, 0.035, 0.035), (0, 0.97, 0.0), wood, root)
    box("Cello_Pegs", (0.11, 0.012, 0.012), (0, 0.90, 0.01), dark, root)
    box("Cello_Bridge", (0.085, 0.055, 0.01), (0, 0.30, 0.075), light, root)
    box("Cello_Tailpiece", (0.055, 0.15, 0.012), (0, 0.15, 0.066), dark, root)
    for x in (-0.018, -0.006, 0.006, 0.018):
        cylinder_between("Cello_String", (x, 0.08, 0.077), (x, 0.88, 0.072), 0.0018, metal, root, 4)
    if endpin:  # retracted when carrying
        cylinder_between("Cello_Endpin", (0, 0.0, 0), (0, -CELLO_ENDPIN, 0), 0.006, metal, root, 6)
    for o in list(root.children)[first:]:
        o.matrix_basis = Matrix.Scale(CELLO_SCALE, 4) @ o.matrix_basis


def build_bow(root):
    # local frame: origin at the frog (in the fist), stick along +Y, hair on the -Z side
    stick = material("Bow_Stick", (0.35, 0.15, 0.06, 1), 0.4)
    hair = material("Bow_Hair", (0.92, 0.9, 0.82, 1), 0.8)
    dark = material("Cello_Ebony", (0.04, 0.03, 0.03, 1), 0.5)
    cylinder_between("Bow_Stick", (0, -0.03, 0), (0, 0.66, 0.0), 0.006, stick, root, 6)
    box("Bow_Frog", (0.012, 0.045, 0.028), (0, 0.0, -0.014), dark, root)
    box("Bow_Hair", (0.008, 0.64, 0.002), (0, 0.33, -0.024), hair, root)
    box("Bow_Tip", (0.01, 0.015, 0.026), (0, 0.655, -0.012), stick, root)


BUILDERS = {
    "Box": build_box,
    "Umbrella": build_umbrella,
    "Phone": build_phone,
    "Chair": build_chair,
    "Cello_Carried": lambda root: build_cello(root, endpin=False),
    "Cello_Played": build_cello,
    "Bow": build_bow,
}


def load_offsets():
    if not os.path.exists(OFFSETS_JSON):
        return {}
    with open(OFFSETS_JSON) as f:
        data = json.load(f)
    return {k: (v["bone"], Matrix(v["matrix"])) for k, v in data.items()}


def save_offsets(offsets):
    data = {k: {"bone": bone, "matrix": [list(r) for r in m]} for k, (bone, m) in offsets.items()}
    with open(OFFSETS_JSON, "w") as f:
        json.dump(data, f, indent=1)


def build(name, animate=False):
    root = empty(name)
    if name == "Bicycle":
        lean, wheels, crank = build_bicycle(root)
        if animate:
            import animations
            for track, (anim, state) in animations.BIKE_TRACKS.items():
                animate_bicycle(lean, wheels, crank, animations.ANIMATIONS[anim][1], track, state)
    else:
        BUILDERS[name](root)
    return root


def attach_for_animation(anim, char_arm):
    """Preview helper: build the props an animation uses and attach them to char_arm."""
    import animations
    offsets = load_offsets()
    made = []
    for name in ANIM_PROPS.get(anim, []):
        root = build(name, animate=name == "Bicycle")
        if name == "Bicycle":
            # follow the scene timeline in the preview
            want = animations.bike_track_for(anim)
            for o in [root] + list(root.children_recursive):
                if o.animation_data and o.animation_data.nla_tracks:
                    tracks = list(o.animation_data.nla_tracks)
                    tr = next(t for t in tracks if t.name == want)
                    o.animation_data.action = tr.strips[0].action
                    for t in tracks:
                        o.animation_data.nla_tracks.remove(t)
        if name in offsets and offsets[name][0] is None:
            root.parent = char_arm
            root.matrix_basis = offsets[name][1]
        elif name in offsets:
            bone, m = offsets[name]
            root.parent = char_arm
            root.parent_type = "BONE"
            root.parent_bone = bone
            length = char_arm.data.bones[bone].length
            root.matrix_parent_inverse = Matrix.Identity(4)
            root.matrix_basis = Matrix.Translation((0, -length, 0)) @ m
        else:
            root.parent = char_arm
        made.extend([root] + list(root.children_recursive))
    return made


def export_all():
    """Export every prop glb into Characters_1_Godot/Props/."""
    os.makedirs(PROPS_DIR, exist_ok=True)
    offsets = load_offsets()
    for name in ["Box", "Umbrella", "Phone", "Bicycle", "Chair", "Cello_Carried", "Cello_Played", "Bow"]:
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        root = build(name, animate=name == "Bicycle")
        bone_prop = name in offsets and offsets[name][0] is not None
        if name in offsets:
            root.matrix_basis = offsets[name][1]
        bpy.ops.object.select_all(action="DESELECT")
        for o in [root] + list(root.children_recursive):
            o.select_set(True)
        bpy.context.view_layer.objects.active = root
        bpy.ops.export_scene.gltf(
            filepath=os.path.join(PROPS_DIR, name + ".glb"), export_format="GLB", use_selection=True,
            export_yup=not bone_prop, export_apply=True,
            export_animations=name == "Bicycle", export_animation_mode="NLA_TRACKS",
            export_force_sampling=True, export_optimize_animation_size=False,
            export_optimize_animation_keep_anim_object=True,
        )
        print("exported prop", name, "->", offsets[name][0] if bone_prop else "character root")
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
