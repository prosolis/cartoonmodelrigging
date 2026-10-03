"""Render contact sheets of animations from the library onto a character.

Usage (from the repo root):
    python tools/preview.py OUT_DIR ANIM [ANIM ...] [--char PATH] [--frames N] [--props]

Each animation gets one PNG: a row of frames from a front 3/4 view and a row
from the side. Needs `bpy` (Blender as a Python module) and Pillow.
"""
import argparse
import math
import os
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(__file__))
import rigkit  # noqa: E402

try:
    import props as props_mod  # noqa: E402
except ImportError:  # pragma: no cover
    props_mod = None


def find_action(name):
    for a in bpy.data.actions:
        if a.name in (name, name + "_Armature"):
            return a
    raise KeyError(name)


def setup_render(res):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.samples = 12
    sc.cycles.device = "CPU"
    sc.cycles.use_denoising = False
    sc.render.resolution_x = res
    sc.render.resolution_y = res
    sc.render.film_transparent = False
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.55, 0.6, 0.68, 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.8
    sc.world = world
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 3.5
    sun.rotation_euler = (math.radians(50), math.radians(10), math.radians(-35))
    sc.collection.objects.link(sun)
    # floor with a grid so contact with the ground is visible
    bpy.ops.mesh.primitive_plane_add(size=8, location=(0, 0, 0))
    floor = bpy.context.active_object
    mat = bpy.data.materials.new("floor")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    checker = nt.nodes.new("ShaderNodeTexChecker")
    checker.inputs["Scale"].default_value = 16
    checker.inputs["Color1"].default_value = (0.42, 0.44, 0.47, 1)
    checker.inputs["Color2"].default_value = (0.36, 0.38, 0.41, 1)
    nt.links.new(checker.outputs["Color"], bsdf.inputs["Base Color"])
    floor.data.materials.append(mat)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.data.lens = 50
    sc.collection.objects.link(cam)
    sc.camera = cam
    return cam


def place_camera(cam, yaw_deg, target, dist):
    yaw = math.radians(yaw_deg)
    # yaw 0 = straight in front (character faces -Y)
    pos = Vector((math.sin(yaw) * dist, -math.cos(yaw) * dist, target.z + 0.35))
    cam.location = pos
    d = target - pos
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("anims", nargs="+")
    ap.add_argument("--char", default="Characters/Character_1/Character_1_1_1.glb")
    ap.add_argument("--frames", type=int, default=6)
    ap.add_argument("--res", type=int, default=260)
    ap.add_argument("--props", action="store_true", help="attach the matching prop if one exists")
    ap.add_argument("--views", default="-35,90")
    ap.add_argument("--dist", type=float, default=3.4)
    ap.add_argument("--gallery", help="instead of sheets, write one labelled tile per animation into this PNG")
    ap.add_argument("--cols", type=int, default=6)
    args = ap.parse_args([a for a in sys.argv[1:]])

    from PIL import Image, ImageDraw

    os.makedirs(args.out, exist_ok=True)
    rigkit.reset_scene()
    lib, lib_objs = rigkit.import_glb(rigkit.LIBRARY)
    for o in lib_objs:
        o.hide_render = True
    char_arm, _ = rigkit.import_glb(os.path.join(rigkit.PACK, args.char))
    char_arm.animation_data_create()
    cam = setup_render(args.res)
    sc = bpy.context.scene
    views = [float(v) for v in args.views.split(",")]

    if args.gallery:
        tiles = []
        for name in args.anims:
            act = find_action(name)
            char_arm.animation_data.action = act
            f0, f1 = act.frame_range
            attached = props_mod.attach_for_animation(name, char_arm) if (args.props and props_mod) else []
            fr = f0 + (f1 - f0) * (0.3 if name.endswith("-loop") else 0.5)
            sc.frame_set(int(fr))
            hips = char_arm.pose.bones["Hips"].head
            place_camera(cam, views[0], char_arm.matrix_world @ Vector((hips.x, hips.y, 0.75)), args.dist)
            path = os.path.join(args.out, "_tile.png")
            sc.render.filepath = path
            bpy.ops.render.render(write_still=True)
            im = Image.open(path).copy()
            ImageDraw.Draw(im).text((6, 6), name, fill=(20, 20, 20))
            tiles.append(im)
            for o in attached:
                bpy.data.objects.remove(o, do_unlink=True)
            print("tile", name)
        w = args.res
        rows = (len(tiles) + args.cols - 1) // args.cols
        sheet = Image.new("RGB", (w * args.cols, w * rows), (60, 60, 60))
        for i, im in enumerate(tiles):
            sheet.paste(im, ((i % args.cols) * w, (i // args.cols) * w))
        sheet.save(args.gallery)
        print("wrote", args.gallery)
        return

    for name in args.anims:
        act = find_action(name)
        char_arm.animation_data.action = act
        f0, f1 = act.frame_range
        attached = []
        if args.props and props_mod is not None:
            attached = props_mod.attach_for_animation(name, char_arm)
        n = args.frames
        frames = [f0 + (f1 - f0) * i / max(1, n - 1) for i in range(n)]
        if name.endswith("-loop") and n > 1:
            frames = [f0 + (f1 - f0) * i / n for i in range(n)]
        tiles = []
        for yaw in views:
            row = []
            for fr in frames:
                sc.frame_set(int(math.floor(fr)), subframe=fr - math.floor(fr))
                hips = char_arm.pose.bones["Hips"].head
                target = char_arm.matrix_world @ Vector((hips.x, hips.y, 0.75))
                place_camera(cam, yaw, target, args.dist)
                path = os.path.join(args.out, "_tile.png")
                sc.render.filepath = path
                bpy.ops.render.render(write_still=True)
                row.append(Image.open(path).copy())
            tiles.append(row)
        for o in attached:
            bpy.data.objects.remove(o, do_unlink=True)
        w = args.res
        sheet = Image.new("RGB", (w * len(frames), w * len(views) + 22), (30, 30, 30))
        d = ImageDraw.Draw(sheet)
        d.text((6, 4), f"{name}   frames {f0:.0f}-{f1:.0f} ({(f1 - f0) / rigkit.FPS:.2f}s)", fill=(255, 255, 255))
        for r, row in enumerate(tiles):
            for c, im in enumerate(row):
                sheet.paste(im, (c * w, 22 + r * w))
        sheet.save(os.path.join(args.out, f"{name}.png"))
        print("wrote", name)


if __name__ == "__main__":
    main()
