"""Small procedural posing toolkit for the Characters_1 rig (runs inside Blender / bpy).

All positions and directions are in armature space, which for this pack is
Blender space: +Z up, the character faces -Y, its left side is +X.

A pose is built per frame with forward kinematics on a copy of the rest
skeleton, plus a two-bone IK helper for arms and legs. Finished poses are
written as keyframes (one key per frame on every bone) so the glTF exporter
produces the same 63-channel layout as the original animations.
"""
import math
import os

import bpy
from mathutils import Matrix, Quaternion, Vector

FPS = 30
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PACK = os.path.join(REPO, "Characters_1_Godot")
LIBRARY = os.path.join(PACK, "Animations", "Characters_1_Animations.glb")

# Handy directions in armature space
UP = Vector((0, 0, 1))
DOWN = Vector((0, 0, -1))
FWD = Vector((0, -1, 0))
BACK = Vector((0, 1, 0))
LEFT = Vector((1, 0, 0))
RIGHT = Vector((-1, 0, 0))


def V(x, y, z):
    return Vector((x, y, z))


def lerp(a, b, t):
    return a + (b - a) * t


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def smooth(t):
    t = clamp(t)
    return t * t * (3 - 2 * t)


def ease_in_out(t):
    t = clamp(t)
    return 0.5 - 0.5 * math.cos(math.pi * t)


def ramp(t, t0, t1):
    """0 before t0, 1 after t1, smooth in between."""
    if t1 <= t0:
        return 1.0 if t >= t1 else 0.0
    return smooth((t - t0) / (t1 - t0))


def wave(phase, offset=0.0):
    """sin over one loop: phase in [0,1)."""
    return math.sin(2 * math.pi * (phase + offset))


def rot(axis, deg):
    return Quaternion(Vector(axis).normalized(), math.radians(deg))


# ---------------------------------------------------------------------------
# Scene setup


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.render.fps = FPS


def import_glb(path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    arm = next(o for o in new if o.type == "ARMATURE")
    return arm, new


# ---------------------------------------------------------------------------
# Skeleton


class Rig:
    """Rest data of an armature plus helpers to build and key poses."""

    def __init__(self, arm_obj):
        self.obj = arm_obj
        self.names = [b.name for b in arm_obj.data.bones]
        self.parent = {b.name: (b.parent.name if b.parent else None) for b in arm_obj.data.bones}
        self.rest = {b.name: b.matrix_local.copy() for b in arm_obj.data.bones}
        self.length = {b.name: b.length for b in arm_obj.data.bones}
        # parent-relative rest offset
        self.offset = {}
        for n in self.names:
            p = self.parent[n]
            self.offset[n] = self.rest[n] if p is None else self.rest[p].inverted() @ self.rest[n]

    def pose(self, basis=None):
        return Pose(self, basis)

    # -- sampling existing actions ---------------------------------------
    def sample_action(self, action, frame):
        """Return {bone: basis Matrix} for an action at a (fractional) frame."""
        out = {}
        for n in self.names:
            loc = Vector((0, 0, 0))
            q = Quaternion((1, 0, 0, 0))
            sc = Vector((1, 1, 1))
            for fc in action.fcurves:
                if not fc.data_path.startswith(f'pose.bones["{n}"].'):
                    continue
                prop = fc.data_path.split(".")[-1]
                val = fc.evaluate(frame)
                if prop == "location":
                    loc[fc.array_index] = val
                elif prop == "rotation_quaternion":
                    q[fc.array_index] = val
                elif prop == "scale":
                    sc[fc.array_index] = val
            q.normalize()
            out[n] = Matrix.LocRotScale(loc, q, sc)
        return out

    # -- writing keys ---------------------------------------------------------
    def bake(self, name, pose_fn, frames, loop=False):
        """Create an action `name` by calling pose_fn(frame) -> Pose for frames 0..frames.

        For loops pose_fn should be periodic so frame `frames` equals frame 0.
        """
        act = bpy.data.actions.new(name)
        act.use_fake_user = True
        act.id_root = "OBJECT"
        curves = {}
        for n in self.names:
            base = f'pose.bones["{n}"]'
            curves[n] = (
                [act.fcurves.new(base + ".location", index=i, action_group=n) for i in range(3)],
                [act.fcurves.new(base + ".rotation_quaternion", index=i, action_group=n) for i in range(4)],
                [act.fcurves.new(base + ".scale", index=i, action_group=n) for i in range(3)],
            )
            for group in curves[n]:
                for fc in group:
                    fc.keyframe_points.add(frames + 1)
        prev_q = {}
        for f in range(frames + 1):
            p = pose_fn(f)
            if loop and f == frames:
                p = pose_fn(0)
            for n in self.names:
                loc, q, sc = p.basis[n].decompose()
                if n in prev_q and prev_q[n].dot(q) < 0:
                    q = -q
                prev_q[n] = q
                lc, qc, scc = curves[n]
                for i in range(3):
                    lc[i].keyframe_points[f].co = (f, loc[i])
                    scc[i].keyframe_points[f].co = (f, sc[i])
                for i in range(4):
                    qc[i].keyframe_points[f].co = (f, q[i])
        for group in curves.values():
            for fcs in group:
                for fc in fcs:
                    for kp in fc.keyframe_points:
                        kp.interpolation = "LINEAR"
                    fc.update()
        return act


class Pose:
    """Mutable pose: per-bone basis matrices with armature-space helpers.

    Edit parents before children: helpers read the current world matrix of
    the parent chain.
    """

    def __init__(self, rig, basis=None):
        self.rig = rig
        self.basis = {n: Matrix.Identity(4) for n in rig.names}
        if basis:
            for n, m in basis.items():
                self.basis[n] = m.copy()

    def copy(self):
        return Pose(self.rig, self.basis)

    # -- queries ------------------------------------------------------------------
    def chain(self, n):
        """World matrix of bone n if its basis were identity."""
        p = self.rig.parent[n]
        if p is None:
            return self.rig.rest[n].copy()
        return self.world(p) @ self.rig.offset[n]

    def world(self, n):
        return self.chain(n) @ self.basis[n]

    def head(self, n):
        return self.world(n).translation.copy()

    def tail(self, n):
        w = self.world(n)
        return w.translation + w.to_3x3().col[1].normalized() * self.rig.length[n]

    def axis(self, n, i):
        return self.world(n).to_3x3().col[i].normalized()

    # -- edits -----------------------------------------------------------------------
    def set_world(self, n, w):
        self.basis[n] = self.chain(n).inverted() @ w

    def rotate(self, n, axis, deg, pivot=None):
        """Rotate bone n (and its children) about an armature-space axis through its head."""
        w = self.world(n)
        piv = w.translation.copy() if pivot is None else Vector(pivot)
        r = rot(axis, deg).to_matrix().to_4x4()
        t = Matrix.Translation(piv) @ r @ Matrix.Translation(-piv)
        self.set_world(n, t @ w)

    def rotate_q(self, n, q):
        w = self.world(n)
        piv = w.translation.copy()
        t = Matrix.Translation(piv) @ q.to_matrix().to_4x4() @ Matrix.Translation(-piv)
        self.set_world(n, t @ w)

    def translate(self, n, delta):
        w = self.world(n)
        self.set_world(n, Matrix.Translation(Vector(delta)) @ w)

    def orient(self, n, y, z, head=None):
        """Set absolute orientation: bone Y axis along y, Z axis as close as possible to z."""
        y = Vector(y).normalized()
        x = y.cross(Vector(z))
        if x.length < 1e-6:
            x = y.orthogonal()
        x.normalize()
        z = x.cross(y).normalized()
        m = Matrix((x, y, z)).transposed().to_4x4()
        m.translation = self.head(n) if head is None else Vector(head)
        self.set_world(n, m)

    def aim(self, n, direction, twist=0.0):
        """Rotate bone n minimally so its Y axis points along `direction`."""
        cur = self.axis(n, 1)
        q = cur.rotation_difference(Vector(direction).normalized())
        if twist:
            q = rot(direction, twist) @ q
        self.rotate_q(n, q)

    def ik2(self, upper, lower, target, bend_dir, z_sign=1.0, end=None):
        """Two-bone IK: place the end of `lower` (head of `end`) at target.

        bend_dir: armature-space direction the joint (elbow / knee) should point to.
        z_sign: +1 for arms (bone Z points toward the elbow tip), -1 for legs.
        """
        a = self.head(upper)
        l1 = (self.rig.rest[lower].translation - self.rig.rest[upper].translation).length
        if end is None:
            l2 = self.rig.length[lower]
        else:
            l2 = (self.rig.rest[end].translation - self.rig.rest[lower].translation).length
        t = Vector(target)
        d = t - a
        dist = clamp(d.length, abs(l1 - l2) + 1e-4, l1 + l2 - 1e-4)
        dn = d.normalized()
        # angle at the root between root->target and root->joint
        cos_a = (l1 * l1 + dist * dist - l2 * l2) / (2 * l1 * dist)
        ang = math.acos(clamp(cos_a, -1, 1))
        bend = Vector(bend_dir) - dn * Vector(bend_dir).dot(dn)
        if bend.length < 1e-6:
            bend = dn.orthogonal()
        bend.normalize()
        joint = a + dn * (l1 * math.cos(ang)) + bend * (l1 * math.sin(ang))
        tgt = a + dn * dist
        self.orient(upper, joint - a, bend * z_sign)
        # Z of lower bone: joint tip direction, perpendicular to the lower bone
        ld = (tgt - joint).normalized()
        jdir = (joint - (a + tgt) * 0.5)
        jdir = jdir - ld * jdir.dot(ld)
        if jdir.length < 1e-6:
            jdir = bend
        self.orient(lower, ld, jdir.normalized() * z_sign)
        return joint

    # -- convenience for this rig ---------------------------------------------
    def arm_ik(self, side, wrist, elbow_dir):
        s = side.upper()
        return self.ik2(f"Arm.{s}", f"ForeArm.{s}", wrist, elbow_dir, 1.0, end=f"Hand.{s}")

    def leg_ik(self, side, ankle, knee_dir):
        s = side.upper()
        return self.ik2(f"UpperLeg.{s}", f"Leg.{s}", ankle, knee_dir, -1.0, end=f"Foot.{s}")

    def hand(self, side, fingers, palm):
        """Orient a hand: fingers = direction the fingers point, palm = palm normal."""
        s = side.upper()
        palm = Vector(palm).normalized()
        x = -palm if s == "L" else palm
        y = Vector(fingers).normalized()
        y = (y - x * y.dot(x)).normalized()
        z = x.cross(y)
        m = Matrix((x, y, z)).transposed().to_4x4()
        m.translation = self.head(f"Hand.{s}")
        self.set_world(f"Hand.{s}", m)

    def rest_orient(self, n, q=None):
        """Give bone n its rest orientation (armature space), optionally rotated by q."""
        r = self.rig.rest[n].to_3x3()
        if q is not None:
            r = q.to_matrix() @ r
        m = r.to_4x4()
        m.translation = self.head(n)
        self.set_world(n, m)

    def foot_flat(self, side, forward=FWD, pitch=0.0):
        """Foot flat on the ground pointing along `forward` (toes follow)."""
        s = side.upper()
        f = Vector(forward)
        f.z = 0
        q = FWD.rotation_difference(f.normalized())
        if pitch:
            q = q @ rot(LEFT, pitch)
        self.rest_orient(f"Foot.{s}", q)
        self.basis[f"Toes.{s}"] = Matrix.Identity(4)

    def hand_grip_offset(self, side):
        """Distance from wrist (hand head) to the palm centre along the hand."""
        s = side.upper()
        return (self.rig.rest[f"IteamSlot.{s}"].translation - self.rig.rest[f"Hand.{s}"].translation).length


def wrist_for_palm(palm_center, fingers, offset):
    return Vector(palm_center) - Vector(fingers).normalized() * offset


def blend(a, b, t):
    """Per-bone blend of two poses (basis space)."""
    out = a.copy()
    for n in a.rig.names:
        la, qa, sa = a.basis[n].decompose()
        lb, qb, sb = b.basis[n].decompose()
        if qa.dot(qb) < 0:
            qb = -qb
        out.basis[n] = Matrix.LocRotScale(la.lerp(lb, t), qa.slerp(qb, t), sa.lerp(sb, t))
    return out


def blend_bones(dst, src, t, bones):
    """Blend selected bones of src into dst in place (basis space)."""
    for n in bones:
        la, qa, sa = dst.basis[n].decompose()
        lb, qb, sb = src.basis[n].decompose()
        if qa.dot(qb) < 0:
            qb = -qb
        dst.basis[n] = Matrix.LocRotScale(la.lerp(lb, t), qa.slerp(qb, t), sa.lerp(sb, t))
