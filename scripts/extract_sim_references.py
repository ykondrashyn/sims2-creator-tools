"""Read pinned rig resources over SSH. Only the local output directory is written."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = {
    "am": [("Sims03.package", "ac4f8687-1c0532fa-e80d128affcc8568", "f638153d71f5008ef46be9580932dd72fd0dac34bcbc54c6958da1511c2ae7d0"),
           ("Sims06.package", "e519c933-1c0532fa-d5ae3aedffbc4356", "bf7ff09eaf3ac84fa4e6aee391b0ce8d50b2624116786936509a9242bb92c275")],
    "af": [("Sims03.package", "ac4f8687-1c0532fa-ccbc1af8ffe2ede9", "c29dee5107d07bb11d7d92f83cd971684ba8ba765901107fad72d2a6bdf49eee"),
           ("Sims06.package", "e519c933-1c0532fa-448642eeffa452ab", "f7bff083aee408ca9a23477d79e831d904a58a7c2312fe65fac5ef9c084c8ad9")],
}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="yevhenkondrashyn@192.168.50.185")
    p.add_argument("--output", type=Path, default=ROOT / "package_creation/sims/assets")
    args = p.parse_args()
    shared = (ROOT / "scripts/extract_object_templates.py").read_text().split("def main():")[0]
    script = shared + '\nimport base64\nselected = ' + repr(RESOURCES) + '''
root=Path.home()/"Games/The Sims 2 Legacy/Base/TSData/Res/Sims3D"
result={}
for body, items in selected.items():
    resources={}
    for filename, text, expected in items:
        k=tuple(int(s,16) for s in text.split('-'))
        f,idx=index(root/filename)
        off,size=idx[k]
        f.seek(off)
        data=unpack(f.read(size))
        f.close()
        assert digest(data)==expected, "Reference resource hash changed: "+text
        resources[k]=data
    result[body]=base64.b64encode(pack(resources)).decode()
print(json.dumps(result))
'''
    data = json.loads(subprocess.check_output(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", args.host, "python3", "-"], input=script.encode(), timeout=60))
    args.output.mkdir(parents=True, exist_ok=True)
    items = {}
    for body, content in data.items():
        raw = base64.b64decode(content, validate=True)
        (args.output / f"{body}-rig.package").write_bytes(raw)
        items[body] = {"asset": f"sim-rig:{body}", "file": f"{body}-rig.package", "sha256": hashlib.sha256(raw).hexdigest(), "sources": RESOURCES[body]}
    (args.output / "references.json").write_text(json.dumps({"version":1, "requirements":"The Sims 2 Legacy Collection", "credit":"Maxis / Electronic Arts", "items":items}, indent=2)+"\n")


if __name__ == "__main__":
    main()
