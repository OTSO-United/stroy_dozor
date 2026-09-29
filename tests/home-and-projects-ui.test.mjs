import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const main = await readFile(
  new URL("../web/src/main.tsx", import.meta.url),
  "utf8",
);
const styles = await readFile(
  new URL("../web/src/workspace.css", import.meta.url),
  "utf8",
);
const html = await readFile(
  new URL("../web/index.html", import.meta.url),
  "utf8",
);
const favicon = await readFile(
  new URL("../web/public/stroydzor-logo.png", import.meta.url),
);

test("root opens a dedicated home page with both primary routes", () => {
  assert.match(main, /initial\.get\("project"\) \? "overview" : "home"/);
  assert.match(main, /page === "home" \? \(/);
  assert.match(main, /Техника на площадке\. План под контролем\./);
  assert.match(main, /Выбрать объект/);
  assert.match(main, /Проверить детектор/);
  assert.match(main, /onOpenProjects/);
  assert.match(main, /onOpenDetector/);
  assert.match(styles, /\.home-hero[\s\S]*?linear-gradient/);
});

test("home page briefly lists every available application tab", () => {
  for (const label of [
    "Все объекты",
    "Обзор объекта",
    "План работ",
    "Камеры и видео",
    "Отклонения",
    "Отчёты",
    "Журнал событий",
    "Настройки",
    "Проверка детектора",
  ]) {
    assert.match(main, new RegExp(`label: "${label}"`));
  }
  assert.match(main, /Что доступно в приложении/);
});

test("project list filters by object type and plan status", () => {
  assert.match(main, /const \[planFilter, setPlanFilter\]/);
  assert.match(main, /aria-label="Фильтр по статусу плана"/);
  assert.match(main, /Все статусы плана/);
  assert.match(main, /План утверждён/);
  assert.match(main, /Плана нет/);
  assert.match(main, /planFilter === "approved"/);
  assert.match(styles, /\.projects-filters/);
  assert.match(main, /aria-label="Сортировка объектов"/);
  assert.match(main, /Сначала новые/);
  assert.match(main, /По алфавиту/);
});

test("all nine project types have distinct visual icons", () => {
  const mappings = {
    housing: "Home",
    education: "GraduationCap",
    healthcare: "Hospital",
    sport: "Trophy",
    culture: "Drama",
    administration: "Landmark",
    preschool: "Baby",
    office: "BriefcaseBusiness",
    roads: "Route",
  };
  for (const [typeId, icon] of Object.entries(mappings)) {
    assert.match(main, new RegExp(`case "${typeId}":[\\s\\S]{0,80}<${icon}`));
    assert.match(styles, new RegExp(`\\.project-type-${typeId}`));
  }
});

test("browser tab uses the supplied StroyDozor logo", () => {
  assert.match(html, /rel="icon"[^>]+href="\/stroydzor-logo\.png"/);
  assert.equal(favicon.subarray(0, 8).toString("hex"), "89504e470d0a1a0a");
  assert.match(main, /СтройДозор/);
});

test("address input locates the project on the map", () => {
  assert.doesNotMatch(main, /Необязательно, адрес можно сохранить отдельно/);
  assert.match(main, /\/map\/search\?address=/);
  assert.match(main, /setAddressSuggestions\(result\.suggestions \|\| \[\]\)/);
  assert.match(main, /Подсказки адресов/);
  assert.match(main, /setShowMap\(true\)/);
});
