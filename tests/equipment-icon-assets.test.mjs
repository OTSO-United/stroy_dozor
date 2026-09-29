import test from "node:test";
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";

const root = new URL("../", import.meta.url);
const model = JSON.parse(
  await readFile(new URL("models/yolo26m/manifest.json", root), "utf8"),
);
const source = await readFile(
  new URL("web/src/equipmentIcons.ts", root),
  "utf8",
);
const iconDirectory = new URL("web/public/equipment-icons/", root);

test("all model classes have one transparent WebP icon in the app catalog", async () => {
  const declaration = source.match(
    /export const equipmentIconIds = \[([\s\S]*?)\] as const;/,
  );
  assert.ok(declaration);
  const ids = [...declaration[1].matchAll(/\d+/g)].map(([value]) =>
    Number(value),
  );
  const supported = [...new Set(Object.values(model.class_map).map(Number))];
  assert.deepEqual(
    [...ids].sort((a, b) => a - b),
    supported.sort((a, b) => a - b),
  );
  const files = (await readdir(iconDirectory))
    .filter((name) => name.endsWith(".webp"))
    .sort();
  assert.deepEqual(files, ids.map((id) => `${id}.webp`).sort());
  for (const name of files) {
    const bytes = await readFile(new URL(name, iconDirectory));
    assert.equal(bytes.toString("ascii", 0, 4), "RIFF", name);
    assert.equal(bytes.toString("ascii", 8, 12), "WEBP", name);
  }
});
