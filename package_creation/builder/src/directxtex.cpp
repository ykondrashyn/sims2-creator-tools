
// C ABI adapter. Callers supply one validated 4x4 RGBA8 block and enough output.
extern "C" void ts2_directxtex_block(uint32_t format, const uint8_t* rgba, uint8_t* output) noexcept
{
    DirectX::XMVECTOR pixels[16];
    for (size_t i = 0; i < 16; ++i) {
        const uint8_t* p = rgba + i * 4;
        pixels[i] = DirectX::XMVectorSet(
            float(p[0]) * (1.0f / 255.0f), float(p[1]) * (1.0f / 255.0f),
            float(p[2]) * (1.0f / 255.0f), float(p[3]) * (1.0f / 255.0f));
    }
    switch (format) {
    case 4: DirectX::D3DXEncodeBC1(output, pixels, 0.5f, DirectX::BC_FLAGS_NONE); break;
    case 5: DirectX::D3DXEncodeBC2(output, pixels, DirectX::BC_FLAGS_NONE); break;
    case 8: DirectX::D3DXEncodeBC3(output, pixels, DirectX::BC_FLAGS_NONE); break;
    }
}
