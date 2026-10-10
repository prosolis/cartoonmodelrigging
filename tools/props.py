"""Simple low-poly props that go with the new animations (runs inside bpy).

Bone props (Box, Umbrella, Phone, Pen, ...) are exported with their attachment offset
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
    "Cycling_Signal_Left": ["Bicycle"],
    "Cycling_Signal_Right": ["Bicycle"],
    "Cycling_Signal_Stop": ["Bicycle"],
    "Sit_Chair_Idle-loop": ["Chair"],
    "Sit_Chair_Down": ["Chair"],
    "Sit_Chair_StandUp": ["Chair"],
    "Sit_Chair_Talk-loop": ["Chair"],
    "Cello_Carry_Idle-loop": ["Cello_Carried"],
    "Cello_Carry_Walk-loop": ["Cello_Carried"],
    "Cello_Play-loop": ["Cello_Played", "Bow", "Chair"],
    "Cello_Rest-loop": ["Cello_Played", "Bow", "Chair"],
    "Police_Radio-loop": ["Radio_Mic"],
    "Police_Ticket-loop": ["TicketBook", "Pen"],
    "Taxi_Enter": ["Taxi"],
    "Taxi_Ride-loop": ["Taxi"],
    "Taxi_Exit": ["Taxi"],
    "Surf_Paddle-loop": ["Surfboard"],
    "Surf_Sit-loop": ["Surfboard"],
    "Surf_Ride-loop": ["Surfboard"],
    "Kite_Fly-loop": ["Kite"],
    "Fish_Idle-loop": ["Rod", "Fishing_Line"],
    "Fish_Cast": ["Rod", "Fishing_Line"],
    "Canoe_Paddle-loop": ["Canoe"],
    "Canoe_Rest-loop": ["Canoe"],
    "Jump_Rope-loop": ["Jump_Rope"],
    "Float_Tube_Lounge-loop": ["Float_Tube"],
    **{name: ["Skate_L", "Skate_R"] for name in ("Skate_Idle-loop", "Skate_Stride-loop", "Skate_Glide-loop",
                                                 "Skate_Spin-loop", "Skate_Wobble-loop", "Skate_Stop", "Skate_Stumble",
                                                 "Skate_Fall_Forward", "Skate_Fall_Back", "Skate_Fall_Wobble",
                                                 "Skate_Sit_Ice-loop", "Skate_Kneel_Ice-loop", "Skate_GetUp",
                                                 "Skate_GetUp_Knees", "Skate_GetUp_Clumsy", "Skate_GetUp_Knees_Clumsy")},
}

ANIMATED_PROPS = ("Bicycle", "Taxi", "Surfboard", "Kite", "Fishing_Line", "Canoe", "Jump_Rope", "Float_Tube")  # own animation per clip

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

# Taxi for Taxi_Enter / Taxi_Exit: the PixelClock City Taxi_5 (pixelclock-city-taxis.zip),
# fitted by tools/taxi.py and placed relative to the character root (armature
# space: +Z up, the character faces -Y). The car faces -X (the character's
# right) with its right side toward the character; its right rear door is cut
# out and hinged at its front edge. These are the fitted car's measurements
# (taxi.TAXI_SCALE = 1.25) that the animations are built around.
TAXI = {
    "side_y": -0.5575,              # outer face of the right-hand side
    "width": 1.825,
    "hinge": Vector((-1.025, -0.5575, 0.0)),  # rear door hinge axis (vertical)
    "door_w": 0.78,                 # door length at window height (0.625 beside the wheel arch)
    "door_open": 85.0,              # degrees, fully open
    "sill_z": 0.48,                 # top of the step below the door opening (= cabin floor)
    "belt_z": 1.24,                 # bottom of the side windows
    "opening_top": 2.225,
    "roof_z": 2.21,                 # underside of the roof
    "floor_z": 0.48,                # cabin floor (a footwell below the sill)
    "seat_z": 0.78,                 # rear seat cushion top
    "seat_back_x": -0.26,           # front face of the rear backrest
    "seat_front_x": -0.76,          # front edge of the rear cushion
    "front_seat_x": -1.12,          # back face of the front seats
    "cabin_front_x": -1.95, "cabin_rear_x": -0.26,
}
# door-local points (x along the door from the hinge, y outward, z up)
TAXI_HANDLE_OUT = Vector((0.67, 0.02, 1.106))    # the handle on the car's texture
TAXI_GRIP_IN = Vector((0.24, -0.07, 1.12))       # inside, below the window (near the hinge)
TAXI_FRAME_IN = Vector((0.72, -0.21, 1.30))      # rear edge of the window frame, inside (the glass leans in)


# Water and beach props (see animations: Surfing, Kite, Fishing, Canoe)
SURFBOARD = {"length": 2.0, "width": 0.56, "thick": 0.075}  # deck-top centre at the origin, nose toward -Y
KITE_REEL = {  # winder: local X across (grips at the ends), line wound on the crossbars, top toward +Z
    "grip": {"L": Vector((0.13, 0, 0)), "R": Vector((-0.13, 0, 0))},
    "line_out": Vector((0, -0.02, 0.075)),
    "bridle": Vector((0, 0.10, 0.30)),  # kite-local: where the line meets the kite (in front of its face)
}
ROD = {"length": 1.9, "butt": -0.32}  # along +Y from the fist; reel below (+Z)
CANOE = {
    "length": 3.4, "width": 0.72, "centre_y": -0.30,  # hull centre (the seat is at y = 0)
    "bottom_z": -0.13, "floor_z": -0.09, "seat_z": -0.07, "gunwale_z": 0.15, "end_z": 0.30,
    "paddle": 2.2,
}
# Jump rope: two handles and the rope (JUMP_ROPE["segments"] straight pieces), all keyed per frame.
# Handle local frame: +Y along the handle, the rope comes out of the +Y end at `tip`.
JUMP_ROPE = {"segments": 24, "radius": 0.0065, "tip": 0.085, "butt": -0.075}
# Inflatable ring: torus in the XY plane, centred on the origin (the float frame), half in the water.
FLOAT_TUBE = {"R": 0.34, "r": 0.13, "z": 0.04}
# Ice skates: a strap-on blade under each shoe. Local frame: origin on the sole under the shoe centre,
# toes toward -Y, up +Z; the blade's lowest point is `lift` below the sole (animations put it on the ice).
SKATE = {"lift": 0.085, "centre_y": -0.075, "front": -0.215, "heel": 0.18, "top": -0.052, "rocker": 0.008}


def blade_bottom(y):
    """Height of the blade's running edge below the sole at y (skate local): a slight rocker."""
    return -SKATE["lift"] + SKATE["rocker"] * (y / 0.2) ** 2


def _blade_profile():
    """Side outline of the blade, (y, z) in skate local: the running edge, the toe curl, the top."""
    sk, low = SKATE, -SKATE["lift"]
    prof = [(sk["heel"], sk["top"])]
    prof += [(y, blade_bottom(y)) for y in (rigkit.lerp(sk["heel"], -0.19, i / 10) for i in range(11))]
    prof += [(-0.204, low + 0.008), (sk["front"], low + 0.019), (sk["front"] + 0.002, low + 0.031),
             (-0.20, sk["top"] + 0.004), (-0.185, sk["top"])]
    return prof


SKATE_PROFILE = _blade_profile()


LINES_JSON = os.path.join(os.path.dirname(__file__), "prop_lines.json")


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


def build_taxi(root):
    import taxi
    return taxi.build(root)


def animate_taxi(door, frames, name, angle):
    """Key the door into an NLA track `name`: angle(f, frames) -> degrees open."""
    door.rotation_mode = "XYZ"
    door.animation_data_create()
    act = bpy.data.actions.new(f"{name}_{door.name}")
    act.id_root = "OBJECT"
    door.animation_data.action = act
    for f in range(frames + 1):
        door.rotation_euler = (0.0, 0.0, math.radians(angle(f, frames)))
        door.keyframe_insert("rotation_euler", frame=f)
    for fc in act.fcurves:
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    track = door.animation_data.nla_tracks.new()
    track.name = name
    track.strips.new(name, 0, act)
    door.animation_data.action = None


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


def build_radio_mic(root):
    # local frame: armature axes (grille facing -Y, up +Z), origin at the mic centre
    body = material("Radio_Body", (0.06, 0.06, 0.07, 1), 0.6)
    grille = material("Radio_Grille", (0.22, 0.22, 0.24, 1), 0.5)
    key = material("Radio_Key", (0.85, 0.35, 0.08, 1), 0.5)
    box("Radio_Body", (0.06, 0.03, 0.08), (0, 0, 0), body, root)
    for k in range(4):
        box("Radio_Slot", (0.04, 0.004, 0.005), (0, -0.0155, 0.022 - k * 0.011), grille, root)
    box("Radio_Key", (0.008, 0.016, 0.024), (0.032, 0, 0.008), key, root)
    box("Radio_Clip", (0.03, 0.006, 0.05), (0, 0.017, 0.005), body, root)
    # coiled cord running up over the shoulder
    cylinder_between("Radio_Cord", (0, 0.004, 0.04), (0.02, 0.03, 0.09), 0.006, body, root, 6)
    cylinder_between("Radio_Cord", (0.02, 0.03, 0.09), (0.03, 0.07, 0.11), 0.006, body, root, 6)


def build_ticket_book(root):
    # local frame: long axis +Y, open page facing +Z, origin at the centre (animations.TICKET_BOOK_SIZE)
    cover = material("Ticket_Cover", (0.10, 0.16, 0.32, 1), 0.6)
    paper = material("Ticket_Paper", (0.96, 0.95, 0.88, 1), 0.8)
    ink = material("Ticket_Ink", (0.35, 0.40, 0.55, 1), 0.8)
    box("Ticket_Cover", (0.16, 0.24, 0.013), (0, 0, -0.0065), cover, root)
    box("Ticket_Pages", (0.15, 0.23, 0.013), (0, -0.002, 0.0065), paper, root)
    box("Ticket_Header", (0.12, 0.025, 0.0015), (0, 0.09, 0.0135), cover, root)
    for k in range(4):
        box("Ticket_Line", (0.11, 0.003, 0.0012), (0, 0.055 - k * 0.04, 0.0135), ink, root)
    cylinder_between("Ticket_Spine", (-0.08, 0.12, 0), (0.08, 0.12, 0), 0.009, cover, root, 8)


def build_pen(root):
    # local frame: +Y toward the tip, origin in the fist, tip at y = animations.PEN_TIP
    barrel = material("Pen_Barrel", (0.05, 0.08, 0.25, 1), 0.4)
    metal = material("Pen_Metal", (0.75, 0.75, 0.78, 1), 0.3, 1.0)
    cylinder_between("Pen_Barrel", (0, -0.06, 0), (0, 0.10, 0), 0.007, barrel, root, 8)
    cylinder_between("Pen_Tip", (0, 0.10, 0), (0, 0.115, 0), 0.0035, metal, root, 6)
    cylinder_between("Pen_Clip", (0.008, -0.055, 0), (0.008, -0.02, 0), 0.002, metal, root, 4)


# ---------------------------------------------------------------------------
# water and beach props


def loft(name, sections, mat, parent=None, closed=True, cap=True, mats=None):
    """Mesh through rings of points (each a list of Vectors, same count). closed: rings wrap around;
    cap: fan the first and last ring shut. mats(face_centre) -> material index (optional)."""
    import bmesh
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    rings = [[bm.verts.new(v) for v in ring] for ring in sections]
    n = len(sections[0])
    for a, b in zip(rings, rings[1:]):
        for i in range(n if closed else n - 1):
            j = (i + 1) % n
            bm.faces.new((a[i], a[j], b[j], b[i]))
    if cap:
        for ring, rev in ((rings[0], False), (rings[-1], True)):
            c = bm.verts.new(sum((v.co for v in ring), Vector()) / len(ring))
            for i in range(n if closed else n - 1):
                j = (i + 1) % n
                vs = (c, ring[j], ring[i]) if rev else (c, ring[i], ring[j])
                bm.faces.new(vs)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    if mats:
        for f in bm.faces:
            f.material_index = mats(f.calc_center_median())
    bm.to_mesh(me)
    bm.free()
    o = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(o)
    o.parent = parent
    for m in mat if isinstance(mat, (list, tuple)) else [mat]:
        me.materials.append(m)
    return o


def build_surfboard(root):
    b = SURFBOARD
    deck = material("Surf_Deck", (0.95, 0.93, 0.86, 1), 0.4)
    stripe = material("Surf_Stripe", (0.10, 0.55, 0.75, 1), 0.4)
    fl = empty("Surf_Float", root)
    L, W, T = b["length"], b["width"], b["thick"]
    secs = []
    n = 24
    for i in range(n + 1):
        u = i / n  # 0 = nose (-Y) .. 1 = tail
        y = -L / 2 + L * u
        w = W / 2 * max(0.02, math.sin(math.pi * min(1.0, u * 1.08)) ** 0.55) * (1 - 0.25 * max(0.0, u - 0.8) / 0.2)
        if u > 0.97:
            w = max(w, W * 0.12)  # squash tail
        t = T * (0.35 + 0.65 * math.sin(math.pi * u) ** 0.4)
        rocker = 0.10 * (1 - u / 0.3) ** 2 if u < 0.3 else 0.02 * ((u - 0.75) / 0.25) ** 2 if u > 0.75 else 0.0
        ring = []
        for k in range(16):
            a = 2 * math.pi * k / 16
            c, s = math.cos(a), math.sin(a)
            x = w * math.copysign(abs(c) ** 0.45, c)
            z = (t / 2) * math.copysign(abs(s) ** 0.8, s)
            ring.append(Vector((x, y, z - t / 2 + rocker)))
        secs.append(ring)
    loft("Surf_Board", secs, [deck, stripe], fl,
         mats=lambda c: 1 if (abs(c.x) < 0.035 and c.z > -0.02 and abs(c.y) < L * 0.42) else 0)
    # fin under the tail
    box("Surf_Fin", (0.012, 0.12, 0.12), (0, L / 2 - 0.16, -T - 0.05), stripe, fl)
    return {"Surf_Float": fl}


def build_kite(root):
    r = KITE_REEL
    wood = material("Kite_Wood", (0.55, 0.38, 0.22, 1), 0.6)
    string = material("Kite_String", (0.95, 0.95, 0.92, 1), 0.8)
    sail_a = material("Kite_Sail_A", (0.90, 0.20, 0.15, 1), 0.6)
    sail_b = material("Kite_Sail_B", (0.98, 0.80, 0.15, 1), 0.6)
    tail = material("Kite_Tail", (0.15, 0.45, 0.85, 1), 0.6)
    reel = empty("Kite_Reel", root)
    for s in "LR":
        g = r["grip"][s]
        cylinder_between("Reel_Grip", g + Vector((0, 0, -0.09)), g + Vector((0, 0, 0.09)), 0.018, wood, reel)
    for z in (-0.06, 0.06):
        box("Reel_Bar", (0.25, 0.03, 0.025), (0, 0, z), wood, reel)
    box("Reel_Line", (0.17, 0.04, 0.14), (0, 0, 0), string, reel)
    body = empty("Kite_Body", root)
    # diamond in its local XY plane (nose +Y), face toward +Z
    nose, tail_pt, lw, rw = Vector((0, 0.55, 0)), Vector((0, -0.80, 0)), Vector((0.50, 0.12, 0)), Vector((-0.50, 0.12, 0))
    for a, b, c, m in ((nose, lw, tail_pt, sail_a), (nose, tail_pt, rw, sail_b)):
        me = bpy.data.meshes.new("Kite_Sail")
        me.from_pydata([a, b, c], [], [(0, 1, 2)])
        o = bpy.data.objects.new("Kite_Sail", me)
        bpy.context.scene.collection.objects.link(o)
        _finish(o, m, body)
    cylinder_between("Kite_Spine", nose, tail_pt, 0.008, wood, body, 6)
    cylinder_between("Kite_Spar", lw, rw, 0.008, wood, body, 6)
    for k in (lw, rw, nose):
        cylinder_between("Kite_Bridle", k * 0.6 + Vector((0, -0.05, 0)), r["bridle"], 0.003, string, body, 4)
    prev = tail_pt
    for i in range(1, 7):
        pt = tail_pt + Vector((0.12 * math.sin(i * 1.3), -0.28 * i, -0.04 * i))
        cylinder_between("Kite_TailString", prev, pt, 0.003, string, body, 4)
        box("Kite_Bow", (0.12, 0.03, 0.004), pt, tail, body)
        prev = pt
    line = empty("Kite_Line", root)
    cylinder_between("Kite_LineMesh", (0, 0, 0), (0, 1, 0), 0.0035, string, line, 4)
    return {"Kite_Reel": reel, "Kite_Body": body, "Kite_Line": line}


def build_rod(root):
    # local frame: +Y along the rod from the fist (origin), reel below on +Z
    cork = material("Rod_Cork", (0.72, 0.55, 0.36, 1), 0.8)
    blank = material("Rod_Blank", (0.10, 0.18, 0.12, 1), 0.35)
    metal = material("Rod_Metal", (0.70, 0.70, 0.74, 1), 0.3, 1.0)
    dark = material("Rod_Reel", (0.12, 0.12, 0.14, 1), 0.5)
    L, butt = ROD["length"], ROD["butt"]
    cylinder_between("Rod_Grip", (0, butt, 0), (0, 0.12, 0), 0.017, cork, root, 8)
    cylinder_between("Rod_Cap", (0, butt - 0.02, 0), (0, butt, 0), 0.02, dark, root, 8)
    bpy.ops.mesh.primitive_cone_add(vertices=8, radius1=0.010, radius2=0.0025, depth=L - 0.12,
                                    location=(0, 0.12 + (L - 0.12) / 2, 0), rotation=(-math.pi / 2, 0, 0))
    c = bpy.context.active_object
    c.name = "Rod_Blank"
    _finish(c, blank, root)
    for y in (0.55, 0.95, 1.3, 1.6, 1.86):
        cylinder_between("Rod_Guide", (0, y, 0), (0, y, 0.025), 0.003, metal, root, 4)
    cylinder_between("Reel_Stem", (0, 0.08, 0), (0, 0.08, 0.06), 0.006, metal, root, 6)
    cylinder_between("Reel_Body", (0, 0.04, 0.08), (0, 0.11, 0.08), 0.035, dark, root, 12)
    cylinder_between("Reel_Spool", (0, 0.11, 0.08), (0, 0.14, 0.08), 0.03, metal, root, 12)
    cylinder_between("Reel_Crank", (0.035, 0.075, 0.08), (0.075, 0.075, 0.08), 0.004, metal, root, 4)
    cylinder_between("Reel_Knob", (0.075, 0.075, 0.08), (0.075, 0.075, 0.11), 0.008, dark, root, 6)


def build_skate(root):
    steel = material("Skate_Blade", (0.82, 0.84, 0.88, 1), 0.2, 1.0)
    metal = material("Skate_Metal", (0.55, 0.56, 0.60, 1), 0.35, 1.0)
    plate = material("Skate_Plate", (0.72, 0.12, 0.13, 1), 0.5)
    top = SKATE["top"]
    for name, y, w, l in (("Toe", -0.115, 0.11, 0.13), ("Heel", 0.115, 0.09, 0.09)):
        box(f"Skate_{name}Plate", (w, l, 0.012), (0, y, -0.006), plate, root)
        box(f"Skate_{name}Post", (0.024, 0.055, -top - 0.008), (0, y, (top - 0.016) / 2), metal, root)
    # blade: the side profile extruded across x
    prof, t = SKATE_PROFILE, 0.0045
    m = len(prof)
    verts = [(x, y, z) for x in (-t, t) for y, z in prof]
    faces = [tuple(range(m))[::-1], tuple(range(m, 2 * m))]
    faces += [(i, (i + 1) % m, m + (i + 1) % m, m + i) for i in range(m)]
    me = bpy.data.meshes.new("Skate_Blade")
    me.from_pydata(verts, [], faces)
    me.update()
    o = bpy.data.objects.new("Skate_Blade", me)
    bpy.context.scene.collection.objects.link(o)
    _finish(o, steel, root)


def build_fishing_line(root):
    line_m = material("Fish_LineMat", (0.92, 0.92, 0.88, 1), 0.6)
    red = material("Fish_FloatRed", (0.90, 0.10, 0.08, 1), 0.5)
    white = material("Fish_FloatWhite", (0.97, 0.97, 0.95, 1), 0.5)
    line = empty("Fish_Line", root)
    cylinder_between("Fish_LineMesh", (0, 0, 0), (0, 1, 0), 0.0025, line_m, line, 4)
    fl = empty("Fish_Float", root)
    ellipsoid("Fish_FloatTop", (0.028, 0.028, 0.03), (0, 0, 0.02), red, fl)
    ellipsoid("Fish_FloatBottom", (0.026, 0.026, 0.028), (0, 0, -0.012), white, fl)
    cylinder_between("Fish_FloatStick", (0, 0, 0.04), (0, 0, 0.09), 0.004, red, fl, 4)
    return {"Fish_Line": line, "Fish_Float": fl}


def build_canoe(root):
    c = CANOE
    hull_m = material("Canoe_Hull", (0.85, 0.30, 0.10, 1), 0.5)
    inside = material("Canoe_Inside", (0.22, 0.18, 0.15, 1), 0.8)
    wood = material("Canoe_Wood", (0.55, 0.38, 0.22, 1), 0.6)
    blade_m = material("Paddle_Blade", (0.95, 0.80, 0.15, 1), 0.5)
    shaft_m = material("Paddle_Shaft", (0.15, 0.15, 0.17, 1), 0.4)
    fl = empty("Canoe_Float", root)
    L, W = c["length"], c["width"]
    secs = []
    n = 28
    for i in range(n + 1):
        u = i / n
        y = c["centre_y"] - L / 2 + L * u
        w = W / 2 * max(0.015, math.sin(math.pi * u) ** 0.6)
        top = c["gunwale_z"] + (c["end_z"] - c["gunwale_z"]) * abs(2 * u - 1) ** 3
        bottom = c["bottom_z"] + (top - c["bottom_z"]) * 0.9 * abs(2 * u - 1) ** 6
        ring = []
        for k in range(13):  # open U from the left gunwale under to the right
            a = math.pi * k / 12
            x = w * math.cos(a)
            depth = math.sin(a) ** 0.6
            ring.append(Vector((x, y, top - (top - bottom) * depth)))
        secs.append(ring)
    hull = loft("Canoe_Hull", secs, [hull_m, inside], fl, closed=False, cap=False)
    sol = hull.modifiers.new("thickness", "SOLIDIFY")
    sol.thickness = 0.018
    sol.material_offset = 1
    hull.data.polygons.foreach_set("use_smooth", [True] * len(hull.data.polygons))
    # rim along the gunwales
    for k in (0, 12):
        pts = [secs[i][k] for i in range(n + 1)]
        for a, b in zip(pts, pts[1:]):
            cylinder_between("Canoe_Rim", a, b, 0.014, wood, fl, 6)
    box("Canoe_Seat", (0.40, 0.42, 0.04), (0, 0.02, c["seat_z"] - 0.02), inside, fl)
    box("Canoe_Back", (0.36, 0.04, 0.22), (0, 0.27, c["seat_z"] + 0.10), inside, fl, rot=(math.radians(-12), 0, 0))
    box("Canoe_Floor", (0.30, 1.30, 0.02), (0, -0.25, c["floor_z"] - 0.01), inside, fl)
    box("Canoe_Footrest", (0.42, 0.04, 0.06), (0, -0.56, c["floor_z"] + 0.04), wood, fl)
    paddle = empty("Canoe_Paddle", root)
    half = c["paddle"] / 2
    cylinder_between("Paddle_Shaft", (0, -half + 0.3, 0), (0, half - 0.3, 0), 0.016, shaft_m, paddle, 8)
    for sgn in (-1, 1):
        box("Paddle_Blade", (0.012, 0.44, 0.16), (0, sgn * (half - 0.22), 0), blade_m, paddle)
    return {"Canoe_Float": fl, "Canoe_Paddle": paddle}


def build_jump_rope(root):
    j = JUMP_ROPE
    grip = material("Rope_Handle", (0.95, 0.45, 0.10, 1), 0.5)
    cap = material("Rope_Cap", (0.15, 0.15, 0.17, 1), 0.4)
    cord = material("Rope_Cord", (0.20, 0.55, 0.90, 1), 0.6)
    objs = {}
    for s in "LR":
        h = empty(f"Rope_Handle_{s}", root)
        cylinder_between("Rope_Grip", (0, j["butt"], 0), (0, j["tip"] - 0.02, 0), 0.018, grip, h, 10)
        cylinder_between("Rope_Cap", (0, j["tip"] - 0.02, 0), (0, j["tip"], 0), 0.013, cap, h, 10)
        objs[h.name] = h
    for i in range(j["segments"]):
        seg = empty(f"Rope_Seg{i:02d}", root)
        # a little longer than the unit length so the joints between pieces don't show gaps
        cylinder_between("Rope_Piece", (0, -0.03, 0), (0, 1.03, 0), j["radius"], cord, seg, 6)
        objs[seg.name] = seg
    return objs


def build_float_tube(root):
    t = FLOAT_TUBE
    mats = [material("Tube_Pink", (0.98, 0.42, 0.62, 1), 0.35), material("Tube_White", (0.97, 0.95, 0.92, 1), 0.35)]
    fl = empty("Tube_Float", root)
    nu, nv = 48, 16
    verts = []
    for i in range(nu):
        a = 2 * math.pi * i / nu
        for k in range(nv):
            b = 2 * math.pi * k / nv
            rr = t["R"] + t["r"] * math.cos(b)
            verts.append((rr * math.cos(a), rr * math.sin(a), t["r"] * math.sin(b)))
    faces = [(i * nv + k, i * nv + (k + 1) % nv, ((i + 1) % nu) * nv + (k + 1) % nv, ((i + 1) % nu) * nv + k)
             for i in range(nu) for k in range(nv)]
    me = bpy.data.meshes.new("Tube_Ring")
    me.from_pydata(verts, [], faces)
    me.update()
    o = bpy.data.objects.new("Tube_Ring", me)
    bpy.context.scene.collection.objects.link(o)
    for m in mats:
        o.data.materials.append(m)
    for poly in o.data.polygons:
        poly.material_index = (poly.index // nv // 6) % 2  # 8 stripes round the ring
        poly.use_smooth = True
    o.parent = fl
    # the valve
    cylinder_between("Tube_Valve", (0, t["R"], t["r"] - 0.01), (0, t["R"], t["r"] + 0.025), 0.012, mats[1], fl, 8)
    return {"Tube_Float": fl}


def load_lines():
    if not os.path.exists(LINES_JSON):
        return {}
    with open(LINES_JSON) as f:
        return json.load(f)


def save_lines(data):
    with open(LINES_JSON, "w") as f:
        json.dump({k: [[[round(c, 5) for c in a], [round(c, 5) for c in b]] for a, b in v] for k, v in data.items()},
                  f, separators=(",", ":"))


def fishing_line_state(track):
    data = load_lines()[track]

    def state(f, frames):
        import animations
        tip, fl = (Vector(v) for v in data[min(f, len(data) - 1)])
        m = Matrix.Translation(fl)
        return {"Fish_Line": animations._line_matrix(tip, fl), "Fish_Float": m}
    return state


def prop_tracks(name):
    """Per-clip tracks of the generic animated props: {track: (character anim, state(f, frames) -> {object: matrix})}."""
    import animations
    if name == "Surfboard":
        return {t: (a, (lambda fn: lambda f, n: {"Surf_Float": fn(f, n)})(fn)) for t, (a, fn) in animations.SURF_TRACKS.items()}
    if name == "Canoe":
        return animations.CANOE_TRACKS
    if name == "Kite":
        return animations.KITE_TRACKS
    if name == "Jump_Rope":
        return animations.JUMP_ROPE_TRACKS
    if name == "Float_Tube":
        return {t: (a, (lambda fn: lambda f, n: {"Tube_Float": fn(f, n)})(fn))
                for t, (a, fn) in animations.FLOAT_TUBE_TRACKS.items()}
    if name == "Fishing_Line":
        return {"Idle-loop": ("Fish_Idle-loop", fishing_line_state("Idle-loop")),
                "Cast": ("Fish_Cast", fishing_line_state("Cast"))}
    return {}


def animate_objects(objs, frames, name, state):
    """Key each object's full transform into an NLA track `name`: state(f, frames) -> {object name: matrix}."""
    states = [state(f, frames) for f in range(frames + 1)]
    for key, obj in objs.items():
        obj.rotation_mode = "QUATERNION"
        obj.animation_data_create()
        act = bpy.data.actions.new(f"{name}_{key}")
        act.id_root = "OBJECT"
        obj.animation_data.action = act
        prev = None
        for f, st in enumerate(states):
            loc, q, sc = st[key].decompose()
            if prev is not None and q.dot(prev) < 0:
                q = -q
            prev = q
            obj.location, obj.rotation_quaternion, obj.scale = loc, q, sc
            for path in ("location", "rotation_quaternion", "scale"):
                obj.keyframe_insert(path, frame=f)
        for fc in act.fcurves:
            for kp in fc.keyframe_points:
                kp.interpolation = "LINEAR"
        track = obj.animation_data.nla_tracks.new()
        track.name = name
        track.strips.new(name, 0, act)
        obj.animation_data.action = None


BUILDERS = {
    "Box": build_box,
    "Umbrella": build_umbrella,
    "Phone": build_phone,
    "Chair": build_chair,
    "Cello_Carried": lambda root: build_cello(root, endpin=False),
    "Cello_Played": build_cello,
    "Bow": build_bow,
    "Radio_Mic": build_radio_mic,
    "TicketBook": build_ticket_book,
    "Pen": build_pen,
    "Rod": build_rod,
    "Skate_L": build_skate,
    "Skate_R": build_skate,
}
TRACKED_BUILDERS = {"Surfboard": build_surfboard, "Kite": build_kite, "Fishing_Line": build_fishing_line,
                    "Canoe": build_canoe, "Jump_Rope": build_jump_rope, "Float_Tube": build_float_tube}


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
    elif name == "Taxi":
        door = build_taxi(root)
        if animate:
            import animations
            for track, (anim, angle) in animations.TAXI_TRACKS.items():
                animate_taxi(door, animations.ANIMATIONS[anim][1], track, angle)
    elif name in TRACKED_BUILDERS:
        objs = TRACKED_BUILDERS[name](root)
        if animate:
            import animations
            for track, (anim, state) in prop_tracks(name).items():
                animate_objects(objs, animations.ANIMATIONS[anim][1], track, state)
    else:
        BUILDERS[name](root)
    return root


def attach_for_animation(anim, char_arm):
    """Preview helper: build the props an animation uses and attach them to char_arm."""
    import animations
    offsets = load_offsets()
    made = []
    for name in ANIM_PROPS.get(anim, []):
        root = build(name, animate=name in ANIMATED_PROPS)
        if name in ANIMATED_PROPS:
            # follow the scene timeline in the preview
            if name == "Bicycle":
                want = animations.bike_track_for(anim)
            elif name == "Taxi":
                want = animations.taxi_track_for(anim)
            else:
                want = next(t for t, (a, _) in prop_tracks(name).items() if a == anim)
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
    for name in ["Box", "Umbrella", "Phone", "Bicycle", "Chair", "Cello_Carried", "Cello_Played", "Bow",
                 "Radio_Mic", "TicketBook", "Pen", "Taxi", "Surfboard", "Kite", "Rod", "Fishing_Line", "Canoe",
                 "Skate_L", "Skate_R", "Jump_Rope", "Float_Tube"]:
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        root = build(name, animate=name in ANIMATED_PROPS)
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
            export_animations=name in ANIMATED_PROPS, export_animation_mode="NLA_TRACKS",
            export_force_sampling=True, export_optimize_animation_size=False,
            export_optimize_animation_keep_anim_object=True,
        )
        print("exported prop", name, "->", offsets[name][0] if bone_prop else "character root")
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
