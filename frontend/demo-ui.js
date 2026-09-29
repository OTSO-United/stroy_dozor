const root = document.getElementById('stroykontur-demo');
const selection = {scenario: 'missing', mode: 'dense_video', work: 'earth'};
const localReviews = new Map();
let currentPage = 'overview';
let boxesVisible = true;
const design = {compact: false, radius: 12};

function setText(name, value) {
  for (const element of root.querySelectorAll('[data-field="' + name + '"]')) element.textContent = value;
}
function showPage(page) {
  currentPage = page;
  for (const panel of root.querySelectorAll('[data-panel]')) panel.hidden = panel.dataset.panel !== page;
  for (const button of root.querySelectorAll('[data-page]')) button.setAttribute('aria-pressed', String(button.dataset.page === page));
}
function drawTrack(target, kinds) {
  target.replaceChildren();
  for (const kind of kinds) {
    const mark = document.createElement('span');
    mark.className = 'sk-slot';
    mark.dataset.kind = kind;
    mark.setAttribute('aria-hidden', 'true');
    target.append(mark);
  }
}
function renderReviews() {
  const list = root.querySelector('[data-review-list]');
  list.replaceChildren();
  if (localReviews.size === 0) {
    const item = document.createElement('li');
    item.textContent = 'Проверок пока нет. Все отметки локальные и сбрасываются после перезагрузки.';
    list.append(item);
  }
  for (const record of localReviews.values()) {
    const item = document.createElement('li');
    item.textContent = record.title + ' · проверено в демо';
    const detail = document.createElement('small');
    detail.textContent = record.detail + ' · ревизия 1 · только локальная отметка';
    item.append(detail); list.append(item);
  }
}
function render() {
  const state = getDemoState(selection);
  root.classList.toggle('sk-compact', design.compact);
  root.style.setProperty('--sk-radius', design.radius + 'px');
  for (const name of ['badge', 'headline', 'explanation', 'samples', 'limitation', 'evidenceTime']) setText(name, state[name]);
  setText('cameraTitle', 'Камера ' + (state.work === 'earth' ? '01' : '02') + ' · ' + state.spec.zone);
  setText('zone', state.spec.zone);
  setText('workTitle', state.spec.title);
  setText('expected', state.spec.expected);
  setText('machineLabel', state.spec.machine);
  setText('resourceLabel', state.spec.resource);
  setText('machineCount', state.machineCount === null ? 'Неизвестно' : String(state.machineCount));
  setText('resourceCount', state.resourceCount === null ? 'Неизвестно' : String(state.resourceCount));
  setText('timelineMode', state.sparse && state.online ? 'Точки — снимки; интервалы неизвестны' : 'Демонстрационное окно · 15 минут');
  setText('duration', state.observedMachineSeconds === null
    ? 'Машинное время: неизвестно. По разрывам не интерполируем.'
    : 'Наблюдение основной машины: 12 мин суммарно. Это не время производительной работы.');
  setText('lineage', 'План v3 · геометрия v1 · правило ' + state.spec.rule + ' · демо-ревизия 1');
  root.querySelector('[data-field="badge"]').dataset.status = state.status;
  root.querySelector('[data-offline]').hidden = state.online;
  root.querySelector('[data-box="machine"]').hidden = !boxesVisible || !state.online;
  root.querySelector('[data-box="resource"]').hidden = !boxesVisible || !state.online || state.resourceCount === 0;
  root.querySelector('[data-scene-resource]').style.display = state.resourceCount ? '' : 'none';
  root.querySelector('[data-scene-earth]').style.display = state.work === 'earth' ? '' : 'none';
  root.querySelector('[data-scene-concrete]').style.display = state.work === 'concrete' ? '' : 'none';
  root.querySelector('[data-scene-drum]').style.display = state.work === 'concrete' ? '' : 'none';
  const observed = root.querySelector('[data-track="observed"]');
  const sparse = state.online && state.sparse;
  observed.classList.toggle('sk-sparse', sparse);
  drawTrack(root.querySelector('[data-track="plan"]'), Array(5).fill('plan'));
  drawTrack(observed, !state.online ? Array(5).fill('gap')
    : sparse ? [state.resourceCount ? 'sample' : 'sample-missing', state.resourceCount ? 'sample' : 'sample-missing', 'gap', state.resourceCount ? 'sample' : 'sample-missing', state.resourceCount ? 'sample' : 'sample-missing']
    : ['gap', ...Array(4).fill(state.resourceCount ? 'present' : 'missing')]);
  observed.setAttribute('role', 'img');
  observed.setAttribute('aria-label', !state.online ? 'Всё окно без наблюдения' : sparse ? 'Четыре снимка из пяти; между снимками неизвестно' : state.resourceCount ? 'Ресурс наблюдается, один интервал отсутствует' : 'Ресурс не обнаружен, один интервал отсутствует');
  const reviewed = localReviews.has(reviewKey(state));
  const reviewButton = root.querySelector('[data-action="review"]');
  reviewButton.disabled = reviewed;
  reviewButton.textContent = reviewed ? 'Проверка отмечена' : 'Отметить как проверенное';
  setText('reviewNote', reviewed ? 'Локальная отметка добавлена в журнал. Вывод системы не изменён.' : 'Действие сохранится только в этом демо.');
  for (const control of root.querySelectorAll('[data-control]')) control.value = selection[control.dataset.control];
  renderReviews();
  showPage(currentPage);
}
for (const control of root.querySelectorAll('[data-control]')) {
  control.addEventListener('change', () => {
    selection[control.dataset.control] = control.value; render();
    root.querySelector('[data-announcement]').textContent = getDemoState(selection).headline;
  });
}
for (const button of root.querySelectorAll('[data-page]')) button.addEventListener('click', () => showPage(button.dataset.page));
for (const button of root.querySelectorAll('[data-work]')) button.addEventListener('click', () => {
  selection.work = button.dataset.work; currentPage = 'overview'; render();
});
root.querySelector('[data-action="boxes"]').addEventListener('click', event => {
  boxesVisible = !boxesVisible; event.currentTarget.setAttribute('aria-pressed', String(boxesVisible)); render();
});
root.querySelector('[data-action="review"]').addEventListener('click', () => {
  const state = getDemoState(selection);
  localReviews.set(reviewKey(state), {title: state.spec.title, detail: state.badge + ' / ' + (state.sparse ? 'снимки' : 'видео')});
  render();
});
render();
if (globalThis.Tweak) {
  const tweak = new Tweak({container: root, onChange: render});
  tweak.addToggle(design, 'compact', {label: 'Компактные отступы'});
  tweak.addSlider(design, 'radius', {label: 'Скругление панелей', min: 0, max: 18, step: 2, unit: 'px'});
}
