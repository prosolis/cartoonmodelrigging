"""Bake the procedural animations and merge them into Characters_1_Animations.glb.

    python tools/build_animations.py [NAME ...]

With no names every animation in animations.ANIMATIONS is (re)built. The
original 47 animations in the library are left untouched; rebuilt ones
replace their previous version.
"""
import os
import sys
import tempfile

import bpy

sys.path.insert(0, os.path.dirname(__file__))
import animations  # noqa: E402
import merge_glb  # noqa: E402
import props  # noqa: E402
import rigkit  # noqa: E402


def main(argv):
    wanted = argv or list(animations.ANIMATIONS)
    unknown = [n for n in wanted if n not in animations.ANIMATIONS]
    if unknown:
        raise SystemExit(f"unknown animations: {unknown}")
    rigkit.reset_scene()
    arm, objs = rigkit.import_glb(rigkit.LIBRARY)
    for o in objs:
        if o is not arm:
            bpy.data.objects.remove(o, do_unlink=True)
    rig = rigkit.Rig(arm)
    src = {}
    for a in list(bpy.data.actions):
        name = a.name[:-len("_Armature")] if a.name.endswith("_Armature") else a.name
        if name in animations.ANIMATIONS:
            bpy.data.actions.remove(a)  # previous build of one of ours
        else:
            src[name] = a
    ctx = animations.Ctx(rig, src)
    for name in wanted:
        fn, frames, loop = animations.ANIMATIONS[name]
        pose_fn = fn(ctx, frames)
        rig.bake(name, pose_fn, frames, loop=loop)
        print("baked", name, frames, "frames")
    # export only our actions
    for a in list(bpy.data.actions):
        if a.name not in wanted:
            bpy.data.actions.remove(a)
    arm.animation_data_create()
    arm.animation_data.action = bpy.data.actions[wanted[0]]
    bpy.ops.object.select_all(action="DESELECT")
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    tmp = os.path.join(tempfile.mkdtemp(), "new_anims.glb")
    bpy.ops.export_scene.gltf(
        filepath=tmp, export_format="GLB", use_selection=True,
        export_animation_mode="ACTIONS", export_force_sampling=True,
        export_optimize_animation_size=False, export_anim_single_armature=True,
        export_skins=True, export_def_bones=False, export_frame_step=1,
    )
    target = merge_glb.read_glb(rigkit.LIBRARY)
    source = merge_glb.read_glb(tmp)
    exported = {a["name"] for a in source[0].get("animations", [])}
    missing = [n for n in wanted if n not in exported]
    if missing:
        raise SystemExit(f"exporter dropped: {missing} (got {sorted(exported)})")
    gltf, binary = merge_glb.merge(target, source, wanted)
    gltf, binary = merge_glb.compact(gltf, binary)
    merge_glb.write_glb(rigkit.LIBRARY, gltf, binary)
    print(f"library now has {len(gltf['animations'])} animations")

    # prop attachment offsets, measured on the reference frames
    props.save_offsets({prop: ctx.prop_offset(prop) for prop in animations.PROP_REFS})
    props.save_lines(animations.fishing_line_tracks(ctx))  # the fishing line follows the rod tip
    props.export_all()


if __name__ == "__main__":
    main(sys.argv[1:])
