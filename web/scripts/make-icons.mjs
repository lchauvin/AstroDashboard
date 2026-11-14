// Favicon, Apple touch icon and header mark from img/logo.png (run: node scripts/make-icons.mjs).
// The crop keeps the drawing (telescope, star, arcs) and drops most of the empty sky around it.
import sharp from "sharp";

const crop = { left: 150, top: 60, width: 1000, height: 1000 };
const out = [
  ["app/icon.png", 256],
  ["app/apple-icon.png", 180],
  ["public/logo-mark.png", 96],
];
for (const [path, size] of out) {
  await sharp("img/logo.png").extract(crop).resize(size, size, { kernel: "lanczos3" }).png({ compressionLevel: 9 }).toFile(path);
  console.log(path, size);
}
