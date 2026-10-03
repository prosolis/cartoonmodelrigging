"""Measure how close the umbrella shaft gets to the head mesh in the umbrella animations.

    python tools/check_clearance.py [CHARACTER.glb ...]

Head vertices (weighted mostly to the Head bone) are moved rigidly with the
Head bone, the shaft is a segment in IteamSlot.R space. Prints the minimum
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
    bone, off = props.load_offsets()["Umbrella"]
    head_rest_inv = rig.rest["Head"].inverted()
    worst_all = 1e9
    for path in paths:
        pts = [head_rest_inv @ p for p in head_points(path)]
        for name in ("Umbrella_Idle-loop", "Umbrella_Walk-loop"):
            fn, frames, _ = animations.ANIMATIONS[name]
            pose_fn = fn(ctx, frames)
            worst = (1e9, None)
            for fr in range(0, frames + 1, 2):
                p = pose_fn(fr)
                m = p.world(bone) @ off
                a, b = m @ SHAFT[0], m @ SHAFT[1]
                hw = p.world("Head")
                d = min(seg_dist(hw @ q, a, b) for q in pts) - SHAFT_RADIUS
                worst = min(worst, (d, fr))
            worst_all = min(worst_all, worst[0])
            print(f"{os.path.basename(path):28s} {name:20s} min clearance {worst[0]:+.3f} at frame {worst[1]}")
    return worst_all


if __name__ == "__main__":
    args = sys.argv[1:] or sorted(glob.glob(os.path.join(rigkit.PACK, "Characters", "*", "*_1_1.glb")))
    main(args)
