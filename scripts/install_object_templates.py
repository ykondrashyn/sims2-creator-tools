"""Validate extracted game profiles before installing browser object assets."""
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "package_creation/builder/target/debug/examples/object"


def call(op, assets, params, output=None, output_asset="output"):
    request = {"op": op, "assets": {k: str(v) for k, v in assets.items()}, "params": params}
    if output:
        request.update(output=str(output), output_asset=output_asset)
    p = subprocess.run([str(BINARY)], input=json.dumps(request), text=True, capture_output=True)
    if p.returncode:
        raise ValueError(p.stderr.strip())
    return json.loads(p.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True, help="Static GLB used for replacement validation")
    parser.add_argument("--reports", type=Path, required=True)
    args = parser.parse_args()
    args.reports.mkdir(parents=True, exist_ok=True)
    catalog = json.loads((args.input / "catalog.json").read_text())
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        for item in catalog["items"]:
            package = args.input / (item["id"] + ".package")
            if hashlib.sha256(package.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError("Source package hash changed: " + item["id"])
            source_hash = item["sha256"]
            extracted = td / (item["id"] + ".package")
            graph = call("object_extract", {"source": package}, {"asset":"source"}, extracted, "extracted")
            (args.reports / (item["id"] + "-scene-graph.json")).write_text(json.dumps(graph, indent=2))
            item["extracted_from_sha256"] = source_hash
            item["resources"] = [r for r in item["resources"] if r["key"] in graph["resource_keys"]]
            item["sha256"] = hashlib.sha256(extracted.read_bytes()).hexdigest()
            item["placement"] = {"known":True,"source":"Preserved OBJD tile definitions and pinned behavior"}
            item.update(call("object_profile", {"source": extracted}, {"asset": "source"}))
        profiles = td / "catalog.json"
        profiles.write_text(json.dumps(catalog, indent=2) + "\n")
        for item in catalog["items"]:
            name = item["id"]
            assets = {"source.package": td / (name + ".package"), "object-catalog": profiles, "object-game": args.input / "game.json", "model.glb": args.model}
            norm = args.reports / (name + "-normalized.package")
            inspection = call("object_inspect", assets, {"files": ["source.package"], "trusted": True}, norm, "object-template")
            (args.reports / (name + "-inspection.json")).write_text(json.dumps(inspection, indent=2))
            item["placement"] = inspection["placement"]
            item["dimensions"] = inspection["dimensions"]
            print(name, "inspected", inspection["resource_count"], "resources", flush=True)
            assets["object-template"] = norm
            for mode in ["clone"] + (["model"] if item["kind"].endswith("decor") else []):
                job = {"id": "01234567890123456789012345678912", "creator": "Validation", "object_name": name.replace("-", ""), "title": "Validation " + name, "description": "Object validation", "price": 70, "mode": mode, "model_file": "model.glb", "scale": 1, "rotation": 0}
                output = args.reports / (name + "-" + mode + ".package")
                preview = call("object_preview", assets, {"job": job}, output)
                (args.reports / (name + "-" + mode + ".json")).write_text(json.dumps(preview))
                repeat = call("object_build", assets, {"job": job})
                if repeat != preview["report"]:
                    raise ValueError("Preview and retry differ: " + name)
                print(name, mode, "passed", len(preview["converted"]["meshes"]), "preview meshes", flush=True)
        # Installation happens only after every source and generated package passes.
        args.output.mkdir(parents=True, exist_ok=True)
        for item in catalog["items"]:
            shutil.copy2(td / (item["id"] + ".package"), args.output)
        shutil.copy2(args.input / "game.json", args.output)
        (args.output / "catalog.json").write_text(json.dumps(catalog, indent=2) + "\n")


if __name__ == "__main__":
    main()
