"""Santa hat: build the skinned hat, fit it to every character, render checks.

    python tools/santa_hat.py            # export Props/SantaHat.glb + Props/santa_hat_fits.gd
    python tools/santa_hat.py --check OUT_DIR [MODEL ...]   # contact sheet of fitted hats

The hat has its own little skeleton (Hat_Root plus a Tip_1..Tip_3 chain) so
Godot's SpringBoneSimulator3D can make the floppy tip bounce. It is modelled
in "hat space": origin at the centre of the brim, +Y up, facing +Z (Godot/glTF
conventions, the same way the characters face).

Fitting works in each model's bind pose, in character space (glTF: +Y up,
facing +Z). For every model the vertices skinned to the Head bone (head +
hair) are collected, and the hat is scaled and lowered until it sits as deep
on the head as FIT_DEPTH asks without any of the head/hair poking through the
inside of the cone. Models that already wear a hat or helmet, and the zombies
(kept for Halloween), are skipped.
"""
import argparse
import glob
import json
import math
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PACK = os.path.join(REPO, "Characters_1_Godot")
PROPS_DIR = os.path.join(PACK, "Props")
FITS_GD = os.path.join(PROPS_DIR, "santa_hat_fits.gd")

# Models with their own headgear (hard hats, police caps, Character_7's hat),
# plus the zombies (Character_Z_*), which are kept for Halloween
SKIP_PREFIXES = ("PoliceMan_", "Character_7_", "Character_B_", "Character_Z_")
SKIP_MODELS = set()

# Hat shape, unscaled (hat space, metres)
BRIM_RADIUS = 0.30   # inside of the brim
BRIM_TUBE = 0.06     # thickness of the fluffy brim
TILT = 8.0           # degrees the hat leans back
FIT_DEPTH = 0.32     # brim sits this fraction of the head height below its top
MARGIN = 0.01        # gap kept between hair and the inside of the hat
POKE = 0.004         # fraction of head points allowed through (single spikes)
# per-model tweaks of the fit parameters
OVERRIDES = {
    "Character_6": {"tilt": 16.0, "poke": 0.012},  # lean back over the quiff so the hat can sit lower
}


# ---------------------------------------------------------------------------
# glb reading (numpy only)


def read_glb(path):
    data = open(path, "rb").read()
    ln = struct.unpack("<I", data[12:16])[0]
    gltf = json.loads(data[20:20 + ln])
    return gltf, data[28 + ln:]


def accessor(gltf, binary, i):
    a = gltf["accessors"][i]
    bv = gltf["bufferViews"][a["bufferView"]]
    dt = {5126: np.float32, 5125: np.uint32, 5123: np.uint16, 5121: np.uint8}[a["componentType"]]
    n = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[a["type"]]
    off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    return np.frombuffer(binary, dt, a["count"] * n, off).reshape(a["count"], n)


def head_points(path):
    """Bind-pose vertices skinned (>= 50%) to the Head bone, in character space."""
    gltf, binary = read_glb(path)
    skin = gltf["skins"][0]
    joints = [gltf["nodes"][j]["name"] for j in skin["joints"]]
    h = joints.index("Head")
    pts = []
    for prim in gltf["meshes"][0]["primitives"]:
        attr = prim["attributes"]
        p = accessor(gltf, binary, attr["POSITION"])
        j = accessor(gltf, binary, attr["JOINTS_0"])
        w = accessor(gltf, binary, attr["WEIGHTS_0"]).astype(np.float64)
        hw = (w * (j == h)).sum(1)
        pts.append(p[hw >= 0.499])
    return np.concatenate(pts).astype(np.float64)


def model_paths():
    return sorted(glob.glob(os.path.join(PACK, "Characters", "*", "*.glb")))


def model_name(path):
    return os.path.splitext(os.path.basename(path))[0]


def skipped(name):
    return name.startswith(SKIP_PREFIXES) or name in SKIP_MODELS


# ---------------------------------------------------------------------------
# fitting


def rot_x(deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def hat_rotation(tilt):
    # positive tilt leans the top of the hat back (towards -Z, behind the character)
    return rot_x(-tilt)


def inner_profile():
    """Inside radius of the cone against height above the brim (hat space), up to where it bends over."""
    ts = np.linspace(0, 0.5, 40)
    h = np.array([spine_xyz(t)[2] for t in ts])
    r = np.array([cone_radius(t) for t in ts]) - 0.01
    return h, r


PROFILE = None


def collides(pts, center, scale, tilt, poke=POKE):
    global PROFILE
    if PROFILE is None:
        PROFILE = inner_profile()
    q = (pts - center) @ hat_rotation(tilt)  # into hat space (rows: R^T p)
    h = q[:, 1] / scale
    # hair may puff out from under the brim; only the cone above the brim has to clear it
    above = h > BRIM_TUBE
    if not above.any():
        return False
    r_in = np.interp(h[above], PROFILE[0], PROFILE[1], right=0.0) - MARGIN / scale
    radial = np.hypot(q[above, 0], q[above, 2]) / scale
    return (radial > r_in).sum() > poke * len(pts)


def lowest_seat(pts, cx, cz, scale, tilt, poke=POKE):
    """Lowest brim height at which the hat does not cut into the head."""
    lo, hi = pts[:, 1].min(), pts[:, 1].max() + 0.01
    if collides(pts, np.array([cx, lo, cz]), scale, tilt, poke):
        for _ in range(30):
            mid = (lo + hi) / 2
            if collides(pts, np.array([cx, mid, cz]), scale, tilt, poke):
                lo = mid
            else:
                hi = mid
        return hi
    return lo


def fit(pts, depth=FIT_DEPTH, tilt=TILT, poke=POKE):
    top, bottom = pts[:, 1].max(), pts[:, 1].min()
    height = top - bottom
    band = pts[pts[:, 1] > top - 0.35 * height]
    cx = (band[:, 0].min() + band[:, 0].max()) / 2
    cz = (band[:, 2].min() + band[:, 2].max()) / 2
    want = top - depth * height
    best = None
    for scale in np.arange(0.6, 2.2, 0.01):
        y = lowest_seat(pts, cx, cz, scale, tilt, poke)
        err = abs(y - want)
        if best is None or err < best[0]:
            best = (err, scale, y)
    _, scale, y = best
    # sphere roughly filling the head, used to keep the bouncing tip out of it
    mid = (pts.max(0) + pts.min(0)) / 2
    radius = 0.42 * float(np.min(pts.max(0) - pts.min(0)))
    return {
        "scale": round(float(scale), 3),
        "position": [round(float(cx), 4), round(float(y), 4), round(float(cz), 4)],
        "tilt": tilt,
        "head_center": [round(float(v), 4) for v in mid],
        "head_radius": round(radius, 4),
    }


def compute_fits(paths=None):
    fits, skips, cache = {}, [], {}
    for path in paths or model_paths():
        name = model_name(path)
        if skipped(name):
            skips.append(name)
            continue
        pts = head_points(path)
        over = next((v for k, v in OVERRIDES.items() if name == k or name.startswith(k + "_")), {})
        key = (len(pts), round(float(pts.sum()), 3), tuple(sorted(over.items())))  # identical heads share a fit
        if key not in cache:
            cache[key] = fit(pts, **over)
        fits[name] = cache[key]
    return fits, skips


def write_fits_gd(fits, skips):
    lines = [
        "# Generated by tools/santa_hat.py - Santa hat fit per character model.",
        "# Values are in character space at the bind pose (+Y up, facing +Z):",
        "# [scale, brim centre x, y, z, tilt back (deg), head centre x, y, z, head radius]",
        "# Tweak by hand if you like; rerunning the tool overwrites this file.",
        "",
        "const FITS := {",
    ]
    for name in sorted(fits, key=lambda n: [(0, int(t), "") if t.isdigit() else (1, 0, t) for t in n.split("_")]):
        f = fits[name]
        vals = [f["scale"], *f["position"], f["tilt"], *f["head_center"], f["head_radius"]]
        lines.append(f'\t"{name}": [{", ".join(f"{v:g}" for v in vals)}],')
    lines += ["}", "", "# Models that already wear a hat or helmet, and the zombies", "const SKIP := ["]
    lines += [f'\t"{n}",' for n in skips]
    lines += ["]", ""]
    with open(FITS_GD, "w") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# hat geometry (bpy). Built in Blender space (+Z up, facing -Y, right = -X);
# the glTF export turns that into hat space (+Y up, facing +Z).

BONES = [  # name, spine parameter range
    ("Hat_Root", 0.0, 0.30),
    ("Tip_1", 0.30, 0.55),
    ("Tip_2", 0.55, 0.78),
    ("Tip_3", 0.78, 1.0),
]
SPINE = [(0, 0, 0.0), (0, 0.02, 0.40), (-0.05, 0.20, 0.55), (-0.20, 0.33, 0.36)]  # Bezier, droops back-right
POM_RADIUS = 0.065


def spine_xyz(t):
    u = 1 - t
    k = (u ** 3, 3 * u * u * t, 3 * u * t * t, t ** 3)
    return tuple(sum(k[i] * SPINE[i][a] for i in range(4)) for a in range(3))


def spine_point(t):
    from mathutils import Vector
    return Vector(spine_xyz(t))


def cone_radius(t):
    # full near the brim so it hugs the crown, then tapering to the tip
    return 0.022 + (BRIM_RADIUS - 0.022) * (1 - t ** 1.2) ** 1.6


def bone_weights(t):
    mids = [(a + b) / 2 for _, a, b in BONES]
    if t <= mids[0]:
        return {BONES[0][0]: 1.0}
    for i in range(len(mids) - 1):
        if t <= mids[i + 1]:
            k = (t - mids[i]) / (mids[i + 1] - mids[i])
            return {BONES[i][0]: 1 - k, BONES[i + 1][0]: k}
    return {BONES[-1][0]: 1.0}


def build_hat():
    """Create the hat armature + skinned mesh in the current Blender scene."""
    import bpy
    from mathutils import Vector
    import props

    red = props.material("SantaHat_Red", (0.72, 0.04, 0.05, 1), 0.85)
    white = props.material("SantaHat_White", (0.95, 0.94, 0.92, 1), 0.95)

    arm_data = bpy.data.armatures.new("SantaHat")
    arm = bpy.data.objects.new("SantaHat", arm_data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    parent = None
    for name, t0, t1 in BONES:
        b = arm_data.edit_bones.new(name)
        b.head = spine_point(t0)
        b.tail = spine_point(t1)
        b.roll = 0
        if parent is not None:
            b.parent = parent
            b.use_connect = True
        parent = b
    bpy.ops.object.mode_set(mode="OBJECT")

    verts, faces, mats, weights = [], [], [], []

    def add(vs, fs, mat, w):
        base = len(verts)
        verts.extend(vs)
        faces.extend([tuple(base + i for i in f) for f in fs])
        mats.extend([mat] * len(fs))
        weights.extend(w if isinstance(w, list) else [w] * len(vs))

    # cone: rings along the spine, parallel-transported frames
    rings, seg = 16, 12
    ts = [i / (rings - 1) for i in range(rings)]
    normal = Vector((1, 0, 0))
    ring_vs, ring_ws = [], []
    prev_tan = None
    for t in ts:
        c = spine_point(t)
        tan = (spine_point(min(1, t + 0.01)) - spine_point(max(0, t - 0.01))).normalized()
        if prev_tan is not None:
            normal = prev_tan.rotation_difference(tan) @ normal
        prev_tan = tan
        binorm = tan.cross(normal).normalized()
        nrm = binorm.cross(tan).normalized()
        r = cone_radius(t)
        base_z = 0.02 if t == 0 else 0.0
        for k in range(seg):
            a = 2 * math.pi * k / seg
            ring_vs.append(c + (nrm * math.cos(a) + binorm * math.sin(a)) * r + Vector((0, 0, base_z)))
            ring_ws.append(bone_weights(t))
    cone_faces = []
    for i in range(rings - 1):
        for k in range(seg):
            a, b = i * seg + k, i * seg + (k + 1) % seg
            cone_faces.append((a, b, b + seg, a + seg))
    add(ring_vs, cone_faces, 0, ring_ws)

    # brim: a lumpy torus
    maj, tube_seg, tube_ring = BRIM_RADIUS + BRIM_TUBE * 0.35, 20, 6
    bvs = []
    for i in range(tube_seg):
        a = 2 * math.pi * i / tube_seg
        lump = 1 + 0.08 * math.sin(5 * a) + 0.05 * math.sin(11 * a + 1)
        for j in range(tube_ring):
            b = 2 * math.pi * j / tube_ring
            rr = maj + BRIM_TUBE * lump * math.cos(b)
            bvs.append(Vector((rr * math.cos(a), rr * math.sin(a), 0.01 + BRIM_TUBE * lump * math.sin(b))))
    bfs = []
    for i in range(tube_seg):
        for j in range(tube_ring):
            a = i * tube_ring + j
            b = i * tube_ring + (j + 1) % tube_ring
            c = ((i + 1) % tube_seg) * tube_ring + (j + 1) % tube_ring
            d = ((i + 1) % tube_seg) * tube_ring + j
            bfs.append((a, d, c, b))
    add(bvs, bfs, 1, {"Hat_Root": 1.0})

    # pom-pom
    end = spine_point(1.0)
    tan = (spine_point(1.0) - spine_point(0.97)).normalized()
    centre = end + tan * POM_RADIUS * 0.6
    pvs, pfs = [], []
    lat, lon = 5, 8
    pvs.append(centre + Vector((0, 0, POM_RADIUS)))
    for i in range(1, lat):
        th = math.pi * i / lat
        for k in range(lon):
            ph = 2 * math.pi * k / lon
            lump = 1 + 0.07 * ((i + k) % 2)
            pvs.append(centre + Vector((math.sin(th) * math.cos(ph), math.sin(th) * math.sin(ph), math.cos(th)))
                       * POM_RADIUS * lump)
    pvs.append(centre + Vector((0, 0, -POM_RADIUS)))
    for k in range(lon):
        pfs.append((0, 1 + k, 1 + (k + 1) % lon))
    for i in range(lat - 2):
        for k in range(lon):
            a = 1 + i * lon + k
            b = 1 + i * lon + (k + 1) % lon
            pfs.append((a, a + lon, b + lon, b))
    last = len(pvs) - 1
    for k in range(lon):
        a = 1 + (lat - 2) * lon + k
        b = 1 + (lat - 2) * lon + (k + 1) % lon
        pfs.append((a, last, b))
    add(pvs, pfs, 1, {"Tip_3": 1.0})

    mesh = bpy.data.meshes.new("SantaHat_Mesh")
    mesh.from_pydata([tuple(v) for v in verts], [], faces)
    mesh.materials.append(red)
    mesh.materials.append(white)
    for poly, m in zip(mesh.polygons, mats):
        poly.material_index = m
        poly.use_smooth = False
    mesh.update()
    obj = bpy.data.objects.new("SantaHat_Mesh", mesh)
    bpy.context.scene.collection.objects.link(obj)
    for name, _, _ in BONES:
        obj.vertex_groups.new(name=name)
    for i, w in enumerate(weights):
        for name, v in w.items():
            if v > 0:
                obj.vertex_groups[name].add([i], v, "REPLACE")
    obj.parent = arm
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = arm
    return arm, obj


def export_hat():
    import bpy
    import rigkit
    rigkit.reset_scene()
    arm, obj = build_hat()
    bpy.ops.object.select_all(action="DESELECT")
    arm.select_set(True)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.export_scene.gltf(
        filepath=os.path.join(PROPS_DIR, "SantaHat.glb"), export_format="GLB", use_selection=True,
        export_yup=True, export_apply=False, export_skins=True, export_animations=False,
    )
    print("exported Props/SantaHat.glb")


# ---------------------------------------------------------------------------
# preview: hats on characters


def gltf_to_blender():
    from mathutils import Matrix
    return Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))


def hat_matrix(f):
    """Hat space -> character space (glTF), as a mathutils Matrix."""
    from mathutils import Matrix
    s = f["scale"]
    r = hat_rotation(f["tilt"])
    m = Matrix.Identity(4)
    for i in range(3):
        for j in range(3):
            m[i][j] = r[i][j] * s
        m[i][3] = f["position"][i]
    return m


def check(out, names, anim=None, frames=None):
    import bpy
    from mathutils import Vector
    from PIL import Image, ImageDraw
    import preview
    import rigkit

    fits, _ = compute_fits([p for p in model_paths() if model_name(p) in names])
    os.makedirs(out, exist_ok=True)
    tiles = []
    for name in names:
        if name not in fits:
            print("skipped", name)
            continue
        path = next(p for p in model_paths() if model_name(p) == name)
        for yaw in (-30, 60, 180):
            rigkit.reset_scene()
            char, _ = rigkit.import_glb(path)
            arm, _ = build_hat()
            g2b = gltf_to_blender()
            arm.matrix_world = char.matrix_world @ g2b @ hat_matrix(fits[name]) @ g2b.inverted()
            cam = preview.setup_render(260)
            head = char.matrix_world @ char.data.bones["Head"].head_local
            target = head + Vector((0, 0, 0.3))
            preview.place_camera(cam, yaw, target, 2.4)
            cam.location.z = target.z + 0.35
            cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
            p = os.path.join(out, "_t.png")
            bpy.context.scene.render.filepath = p
            bpy.ops.render.render(write_still=True)
            im = Image.open(p).copy()
            ImageDraw.Draw(im).text((4, 4), name, fill=(0, 0, 0))
            tiles.append(im)
    cols = 6
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (260 * cols, 260 * rows), (40, 40, 40))
    for i, im in enumerate(tiles):
        sheet.paste(im, ((i % cols) * 260, (i // cols) * 260))
    sheet.save(os.path.join(out, "santa_hats.png"))
    print("wrote", os.path.join(out, "santa_hats.png"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", metavar="OUT_DIR")
    ap.add_argument("models", nargs="*")
    a = ap.parse_args()
    if a.check:
        check(a.check, a.models)
        return
    fits, skips = compute_fits()
    write_fits_gd(fits, skips)
    print(f"fitted {len(fits)} models, skipped {len(skips)} -> {os.path.relpath(FITS_GD, REPO)}")
    export_hat()


if __name__ == "__main__":
    main()
