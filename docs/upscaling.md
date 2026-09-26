# Local upscaling

Choose Real-ESRGAN CPU or Real-ESRGAN WebGPU. Both use the pinned full FP32
RealESRGAN_x4plus model with 4x output. CPU is the initial default. WebGPU requires
supported hardware and HTTPS or localhost and never silently switches to CPU.

Images stay on your device. There is no token or paid prediction. PNG output is
lossless. Original transparency is resized locally with Lanczos filtering, rather
than reconstructed by AI. Long local runs continue until completion or Cancel.
Closing the page loses this session's images and results.

Use Upscale again to process the original image with a different backend. The last
successful download remains available during another run and after a failure.
