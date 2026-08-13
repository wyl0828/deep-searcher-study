import { readdir, stat } from "node:fs/promises";
import { resolve } from "node:path";

const limit = 500 * 1024;
const assets = resolve("dist", "assets");
const files = await readdir(assets);
const javascript = files.filter((name) => name.endsWith(".js"));
const oversized = [];
for (const file of javascript) {
  const bytes = (await stat(resolve(assets, file))).size;
  if (bytes > limit) oversized.push({ file, bytes });
}
if (oversized.length) {
  for (const item of oversized) {
    console.error(`${item.file}: ${item.bytes} bytes exceeds ${limit}`);
  }
  process.exit(1);
}
console.log(`bundle size gate passed: ${javascript.length} JS chunks`);
