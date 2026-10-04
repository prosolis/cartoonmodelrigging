"""Measure how close long props (umbrella shaft, cello neck) get to the head mesh.

    python tools/check_clearance.py [CHARACTER.glb ...]

Head vertices (weighted mostly to the Head bone) are moved rigidly with the
Head bone; each prop part is a capsule (segment + radius) in the prop's space. Prints the minimum
distance per animation; negative means the shaft is inside the head hull.
"""
import glob
import os
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(__file__))
import animations  # noqa: E402
import props  # noqa: E402
import rigkit  # noqa: E402

SHAFT = (Vector((0, -0.10, 0)), Vector((0, 1.0, 0)))
SHAFT_RADIUS = 0.025
# prop, capsule start, end, radius, animations
CHECKS = [
    ("Umbrella", SHAFT[0], SHAFT[1], SHAFT_RADIUS, ("Umbrella_Idle-loop", "Umbrella_Walk-loop")),
    ("Cello_Carried", Vector((0, 0.55, 0.02)), Vector((0, 1.0, 0.0)), 0.045,
     ("Cello_Carry_Idle-loop", "Cello_Carry_Walk-loop")),
    ("Cello_Played", Vector((0, 0.55, 0.02)), Vector((0, 1.0, 0.0)), 0.045, ("Cello_Play-loop", "Cello_Rest-loop")),
]


def head_points(path):
    arm, objs = rigkit.import_glb(path)
    pts = []
    for o in objs:
        if o.type != "MESH":
            continue
        gi = {g.name: g.index for g in o.vertex_groups}
        if "Head" not in gi:
            continue
        for v in o.data.vertices:
            w = {g.group: g.weight for g in v.groups}
            if w.get(gi["Head"], 0) > 0.5:
                pts.append(o.matrix_world @ v.co)
    for o in objs:
        bpy.data.objects.remove(o, do_unlink=True)
    return pts


def seg_dist(p, a, b):
    ab = b - a
    t = max(0.0, min(1.0, (p - a).dot(ab) / ab.length_squared))
    return (p - (a + ab * t)).length


def main(paths):
    rigkit.reset_scene()
    arm, objs = rigkit.import_glb(rigkit.LIBRARY)
    rig = rigkit.Rig(arm)
    src = {a.name[:-9] if a.name.endswith("_Armature") else a.name: a for a in bpy.data.actions}
    ctx = animations.Ctx(rig, src)
    for o in objs:
        bpy.data.objects.remove(o, do_unlink=True)
    offsets = props.load_offsets()
    head_rest_inv = rig.rest["Head"].inverted()
    worst_all = 1e9
    for path in paths:
        pts = [head_rest_inv @ p for p in head_points(path)]
        for prop, c0, c1, radius, anims in CHECKS:
            bone, off = offsets[prop]
            for name in anims:
                fn, frames, _ = animations.ANIMATIONS[name]
                pose_fn = fn(ctx, frames)
                worst = (1e9, None)
                for fr in range(0, frames + 1, 2):
                    p = pose_fn(fr)
                    m = off if bone is None else p.world(bone) @ off
                    a, b = m @ c0, m @ c1
                    hw = p.world("Head")
                    d = min(seg_dist(hw @ q, a, b) for q in pts) - radius
                    worst = min(worst, (d, fr))
                worst_all = min(worst_all, worst[0])
                print(f"{os.path.basename(path):28s} {name:22s} min clearance {worst[0]:+.3f} at frame {worst[1]}")
    return worst_all


if __name__ == "__main__":
    args = sys.argv[1:] or sorted(glob.glob(os.path.join(rigkit.PACK, "Characters", "*", "*_1_1.glb")))
    main(args)
