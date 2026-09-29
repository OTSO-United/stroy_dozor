import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

const root = new URL("../", import.meta.url);
const model = JSON.parse(
  await readFile(new URL("models/yolo26m/manifest.json", root), "utf8"),
);
const catalog = JSON.parse(
  await readFile(new URL("backend/seed/catalog.json", root), "utf8"),
);
const source = await readFile(
  new URL("web/src/documentationEquipment.ts", root),
  "utf8",
);
const page = await readFile(
  new URL("web/src/DocumentationPage.tsx", root),
  "utf8",
);
const main = await readFile(new URL("web/src/main.tsx", root), "utf8");
const provenance = JSON.parse(
  await readFile(
    new URL("docs/data/documentation-photo-sources.json", root),
    "utf8",
  ),
);
const entries = [
  ...source.matchAll(
    /id: (\d+),\s+name: "([^"]+)",\s+english: "([^"]+)",\s+description: "([^"]+)"/g,
  ),
].map(([, id, name, english, description]) => ({
  id: Number(id),
  name,
  english,
  description,
}));

test("documentation lists every and only supported canonical equipment class", () => {
  const supported = [
    ...new Set(Object.values(model.class_map).map(Number)),
  ].sort((a, b) => a - b);
  assert.equal(entries.length, supported.length);
  assert.deepEqual(
    entries.map(({ id }) => id).sort((a, b) => a - b),
    supported,
  );
  for (const entry of entries) {
    assert.ok(entry.name && entry.english);
    assert.equal(
      entry.name,
      catalog.classes.find((item) => item.id === entry.id)?.name,
    );
    assert.ok(entry.description.split(/\s+/u).length <= 5, entry.name);
  }
});

test("each class has two distinct, traceable WebP examples", async () => {
  assert.equal(provenance.length, entries.length * 2);
  const assets = new Set();
  for (const entry of entries) {
    const pair = provenance.filter(({ class_id }) => class_id === entry.id);
    assert.deepEqual(
      pair.map(({ example }) => example).sort(),
      [1, 2],
      entry.name,
    );
    for (const photo of pair) {
      assert.ok(!assets.has(photo.asset), photo.asset);
      assets.add(photo.asset);
      assert.match(photo.source, /^datasets\/equipment_examples\//);
      if (photo.provenance_status === "verified") {
        assert.match(photo.source_page, /^https:\/\/commons\.wikimedia\.org\//);
        assert.ok(photo.artist && photo.license);
      } else {
        assert.equal(photo.provenance_status, "local_unverified");
        assert.equal(photo.source_page, "");
        assert.equal(photo.license_url, "");
      }
      assert.ok(photo.source_sha256);
      const file = await readFile(new URL(`web/public/${photo.asset}`, root));
      assert.equal(file.toString("ascii", 0, 4), "RIFF");
      assert.equal(file.toString("ascii", 8, 12), "WEBP");
      assert.equal(
        createHash("sha256").update(file).digest("hex"),
        photo.asset_sha256,
      );
    }
    assert.notEqual(pair[0].asset_sha256, pair[1].asset_sha256, entry.name);
  }
});

test("documentation is reachable from home and the global navigation", () => {
  assert.match(main, /href="\?tab=docs"/);
  assert.match(main, /<DocumentationPage onOpenProjects={openProjects} \/>/);
  assert.match(main, /Документация/);
  assert.match(page, /Шесть шагов от площадки до отчёта/);
  assert.match(page, /\/docs\/equipment\//);
  assert.match(page, /Авторы и лицензии фотографий/);
  assert.match(page, /docs-equipment-photo-button/);
  assert.match(page, /docs-photo-viewer/);
  assert.match(page, /aria-modal="true"/);
  assert.match(page, /event.key === "Escape"/);
});
