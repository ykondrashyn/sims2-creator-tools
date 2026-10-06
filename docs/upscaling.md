# Local upscaling

The model selector offers CPU and WebGPU entries for each model:

| Model | Native output | Intended use |
| --- | --- | --- |
| Real-ESRGAN Compact | 4× | Lightweight general image restoration, the CPU default |
| AnimeVideo v3 | 4× | Illustrations, line art and stylized textures |
| Nomos SPAN | 2× | General enlargement with JPEG degradation training |
| Real-ESRGAN Full | 4× | The original full model for comparison |

All models use pinned FP32 ONNX weights. Only the selected model and backend are
loaded. WebGPU needs supported hardware and HTTPS or localhost and never silently
switches to CPU. Smaller models use their own GPU buffer requirements.

Images stay on your device. There is no token or paid prediction. PNG output is
lossless. Original transparency is resized locally with Lanczos filtering and is
not reconstructed by AI. Long local runs continue until completion or Cancel.
Closing the page loses this session's images and results.

Inputs are limited to 32 MiB encoded, 4 megapixels and 2048 pixels per side for
all models. No automatic input resizing occurs. Nomos produces 2× output, while
the other models produce 4×. Compact uses the official named checkpoint directly,
without weak-denoise blending or face enhancement.

Use Upscale again to process the original image with a different model or backend.
The last successful download remains available during another run and after a
failure. Its caption, model hash and filename continue to identify that result.
Changing the selection alone does not start processing.
