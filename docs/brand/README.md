# AASTAR brand assets

One artwork on a 64-unit grid: a palm-green tile, a gold oil drop with a rising price line that ends in a
live-price dot, and a four-point star (the "STAR" in AASTAR). The favicon is a separate 32-unit drawing,
simplified so it still reads at 16 px.

| File | Use |
|---|---|
| `aastar-app-icon.svg` / `aastar-mark.svg` | master icon: DWS Hub app tile, documents, anything ≥ 48 px |
| `aastar-logo.svg` / `aastar-logo-dark.svg` | lockup with the wordmark, for light and dark backgrounds |
| `aastar-icon-maskable.svg` | full-bleed, content inside the 80 % safe zone (Android / PWA) |
| `aastar-icon-apple.svg` | full-bleed; iOS rounds the corners |
| `frontend/public/favicon.svg` / `favicon.ico` | browser tab (ICO holds 16, 32 and 48 px) |
| `frontend/public/icons/*.png` | 192 / 512 app icons, maskable 512, Apple touch 180 |

Colours come from `frontend/src/styles/tokens.css` (brand greens, accent golds).

## Changing the icon

1. Edit `make_icons.py` (paths and colours) and run `python docs/brand/make_icons.py`.
2. Mirror the paths in `LogoMark` (`frontend/src/components/Logo.tsx`).
3. Re-export the PNGs and the ICO. No local tools are needed beyond Docker and Pillow:

```bash
docker run --rm -v "$PWD:/w" -w /w alpine:3.20 sh -c 'apk add --no-cache rsvg-convert >/dev/null && B=docs/brand P=frontend/public/icons && rsvg-convert -w 192 -h 192 $B/aastar-app-icon.svg -o $P/icon-192.png && rsvg-convert -w 512 -h 512 $B/aastar-app-icon.svg -o $P/icon-512.png && rsvg-convert -w 512 -h 512 $B/aastar-icon-maskable.svg -o $P/icon-maskable-512.png && rsvg-convert -w 180 -h 180 $B/aastar-icon-apple.svg -o $P/apple-touch-icon.png && for s in 16 32 48; do rsvg-convert -w $s -h $s frontend/public/favicon.svg -o .f$s.png; done'
```

```bash
python -c "from PIL import Image; i=[Image.open(f'.f{s}.png') for s in (16,32,48)]; i[2].save('frontend/public/favicon.ico', sizes=[(16,16),(32,32),(48,48)], append_images=i[:2])" && rm .f16.png .f32.png .f48.png
```
