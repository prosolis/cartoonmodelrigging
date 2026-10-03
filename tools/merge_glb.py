"""Append animations from one GLB into another, matching target nodes by name.

    python tools/merge_glb.py TARGET.glb SOURCE.glb NAME [NAME ...] [-o OUT.glb]

Existing animations in TARGET with the same name are replaced, everything
else in TARGET (including the original animations' data) is kept byte for
byte. Unused buffer data from replaced animations is left in place; run
with --compact to rebuild the buffer without it.
"""
import argparse
import json
import struct


def read_glb(path):
    data = open(path, "rb").read()
    magic, version, _ = struct.unpack("<III", data[:12])
    assert magic == 0x46546C67 and version == 2, path
    off = 12
    gltf, binary = None, b""
    while off < len(data):
        ln, typ = struct.unpack("<II", data[off:off + 8])
        chunk = data[off + 8:off + 8 + ln]
        if typ == 0x4E4F534A:
            gltf = json.loads(chunk)
        elif typ == 0x004E4942:
            binary = chunk
        off += 8 + ln
    return gltf, bytearray(binary)


def write_glb(path, gltf, binary):
    while len(binary) % 4:
        binary.append(0)
    gltf["buffers"] = [{"byteLength": len(binary)}]
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(binary)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(js), 0x4E4F534A))
        f.write(js)
        f.write(struct.pack("<II", len(binary), 0x004E4942))
        f.write(binary)


def accessor_bytes(gltf, binary, idx):
    acc = gltf["accessors"][idx]
    bv = gltf["bufferViews"][acc["bufferView"]]
    comp = {5126: 4, 5125: 4, 5123: 2, 5121: 1}[acc["componentType"]]
    ncomp = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[acc["type"]]
    size = comp * ncomp
    stride = bv.get("byteStride", size)
    start = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
    if stride == size:
        return bytes(binary[start:start + size * acc["count"]])
    return b"".join(bytes(binary[start + i * stride:start + i * stride + size]) for i in range(acc["count"]))


def append_accessor(gltf, binary, src_gltf, src_bin, idx, cache):
    if idx in cache:
        return cache[idx]
    raw = accessor_bytes(src_gltf, src_bin, idx)
    while len(binary) % 4:
        binary.append(0)
    gltf["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(raw)})
    binary.extend(raw)
    src = src_gltf["accessors"][idx]
    acc = {k: v for k, v in src.items() if k in ("componentType", "count", "type", "min", "max", "normalized")}
    acc["bufferView"] = len(gltf["bufferViews"]) - 1
    gltf["accessors"].append(acc)
    cache[idx] = len(gltf["accessors"]) - 1
    return cache[idx]


def merge(target, source, names):
    gltf, binary = target
    src_gltf, src_bin = source
    node_index = {n.get("name"): i for i, n in enumerate(gltf["nodes"])}
    by_name = {a["name"]: a for a in src_gltf.get("animations", [])}
    missing = [n for n in names if n not in by_name]
    if missing:
        raise SystemExit(f"not in source: {missing}")
    gltf["animations"] = [a for a in gltf.get("animations", []) if a["name"] not in names]
    for name in names:
        src = by_name[name]
        cache = {}
        samplers, channels = [], []
        for ch in src["channels"]:
            node_name = src_gltf["nodes"][ch["target"]["node"]]["name"]
            if node_name not in node_index:
                continue  # e.g. a preview mesh node, not part of the skeleton
            s = src["samplers"][ch["sampler"]]
            samplers.append({
                "input": append_accessor(gltf, binary, src_gltf, src_bin, s["input"], cache),
                "output": append_accessor(gltf, binary, src_gltf, src_bin, s["output"], cache),
                "interpolation": s.get("interpolation", "LINEAR"),
            })
            channels.append({"sampler": len(samplers) - 1,
                             "target": {"node": node_index[node_name], "path": ch["target"]["path"]}})
        gltf["animations"].append({"name": name, "channels": channels, "samplers": samplers})
    gltf["animations"].sort(key=lambda a: a["name"].lower())
    return gltf, binary


def compact(gltf, binary):
    """Drop buffer views / accessors no longer referenced (animations only file)."""
    used_acc = set()
    for a in gltf.get("animations", []):
        for s in a["samplers"]:
            used_acc.update((s["input"], s["output"]))
    for m in gltf.get("meshes", []):
        for p in m["primitives"]:
            used_acc.update(p["attributes"].values())
            if "indices" in p:
                used_acc.add(p["indices"])
            for t in p.get("targets", []):
                used_acc.update(t.values())
    for s in gltf.get("skins", []):
        if "inverseBindMatrices" in s:
            used_acc.add(s["inverseBindMatrices"])
    images_bv = {img["bufferView"] for img in gltf.get("images", []) if "bufferView" in img}
    new_bin = bytearray()
    bv_map, acc_map, new_bvs, new_accs = {}, {}, [], []

    def copy_bv(i):
        if i in bv_map:
            return bv_map[i]
        bv = dict(gltf["bufferViews"][i])
        raw = binary[bv.get("byteOffset", 0):bv.get("byteOffset", 0) + bv["byteLength"]]
        while len(new_bin) % 4:
            new_bin.append(0)
        bv["byteOffset"] = len(new_bin)
        new_bin.extend(raw)
        new_bvs.append(bv)
        bv_map[i] = len(new_bvs) - 1
        return bv_map[i]

    for i in sorted(used_acc):
        acc = dict(gltf["accessors"][i])
        acc["bufferView"] = copy_bv(acc["bufferView"])
        new_accs.append(acc)
        acc_map[i] = len(new_accs) - 1
    for i in sorted(images_bv):
        copy_bv(i)
    for a in gltf.get("animations", []):
        for s in a["samplers"]:
            s["input"], s["output"] = acc_map[s["input"]], acc_map[s["output"]]
    for m in gltf.get("meshes", []):
        for p in m["primitives"]:
            p["attributes"] = {k: acc_map[v] for k, v in p["attributes"].items()}
            if "indices" in p:
                p["indices"] = acc_map[p["indices"]]
            p["targets"] = [{k: acc_map[v] for k, v in t.items()} for t in p.get("targets", [])] or None
            if p["targets"] is None:
                del p["targets"]
    for s in gltf.get("skins", []):
        if "inverseBindMatrices" in s:
            s["inverseBindMatrices"] = acc_map[s["inverseBindMatrices"]]
    for img in gltf.get("images", []):
        if "bufferView" in img:
            img["bufferView"] = bv_map[img["bufferView"]]
    gltf["bufferViews"], gltf["accessors"] = new_bvs, new_accs
    return gltf, new_bin


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("source")
    ap.add_argument("names", nargs="+")
    ap.add_argument("-o", "--out")
    ap.add_argument("--compact", action="store_true")
    a = ap.parse_args()
    gltf, binary = merge(read_glb(a.target), read_glb(a.source), a.names)
    if a.compact:
        gltf, binary = compact(gltf, binary)
    write_glb(a.out or a.target, gltf, binary)
    print(f"merged {len(a.names)} animations -> {a.out or a.target} ({len(gltf['animations'])} total)")


if __name__ == "__main__":
    main()
