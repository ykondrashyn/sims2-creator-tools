"""Generate synthetic reference pixels with the original PyTorch network."""

import json
from pathlib import Path

import numpy as np
from PIL import Image
import torch

from export import model


def main():
    out = Path(__file__).parent / "fixtures"
    out.mkdir(exist_ok=True)
    torch.set_num_threads(1)
    net = model()
    reports = []
    with torch.inference_mode():
        for name, width, height in [("small", 32, 24), ("seams", 133, 37)]:
            y, x = np.mgrid[:height, :width]
            rgb = np.stack(
                [
                    (x * 7 + y * 3) % 256,
                    (x * 11 + y * 13) % 256,
                    np.where((x // 9 + y // 7) % 2, 220, 30),
                ],
                axis=-1,
            ).astype(np.uint8)
            rgba = np.dstack([rgb, ((x * 255) // (width - 1)).astype(np.uint8)])
            Image.fromarray(rgba, "RGBA").save(out / f"{name}.png")
            padded = np.pad(
                rgb.astype(np.float32) / 255, ((0, 10), (0, 10), (0, 0)), mode="reflect"
            )
            source = torch.from_numpy(padded.transpose(2, 0, 1)[None].copy())
            full = net(source).numpy()[0, :, : height * 4, : width * 4]
            target = np.zeros_like(full)
            for top in range(0, height, 128):
                for left in range(0, width, 128):
                    x0, y0 = max(0, left - 64), max(0, top - 64)
                    x1, y1 = min(width + 10, left + 192), min(height + 10, top + 192)
                    output = net(source[:, :, y0:y1, x0:x1]).numpy()[0]
                    w, h = min(128, width - left) * 4, min(128, height - top) * 4
                    ox, oy = (left - x0) * 4, (top - y0) * 4
                    target[:, top * 4 : top * 4 + h, left * 4 : left * 4 + w] = output[
                        :, oy : oy + h, ox : ox + w
                    ]
            packed = np.rint(np.clip(target, 0, 1) * 255).astype(np.uint8).transpose(1, 2, 0)
            full_packed = np.rint(np.clip(full, 0, 1) * 255).astype(np.uint8).transpose(1, 2, 0)
            Image.fromarray(packed, "RGB").save(out / f"{name}-reference.png")
            difference = np.abs(packed.astype(np.int16) - full_packed.astype(np.int16))
            reports.append(
                {
                    "name": name,
                    "size": [width, height],
                    "tiled_vs_full_maximum_byte_error": int(difference.max()),
                    "tiled_vs_full_mean_byte_error": float(difference.mean()),
                }
            )
    (out / "report.json").write_text(json.dumps(reports, indent=2) + "\n")
    print(json.dumps(reports, indent=2))


if __name__ == "__main__":
    main()
