// Synthetic domain fixtures. No model, camera, network or production writes.
export const works = {
  earth: {title: 'Выемка грунта', zone: 'Котлован А', expected: 'Экскаватор + самосвал', machine: 'Экскаватор', resource: 'Самосвал', rule: 'RES-EARTH-01'},
  concrete: {title: 'Бетонирование', zone: 'Секция Б', expected: 'Автобетоносмеситель', machine: 'Кран · контекст', resource: 'Миксер', rule: 'RES-CONCRETE-01'}
};

export function getDemoState({scenario = 'missing', mode = 'dense_video', work = 'earth'} = {}) {
  if (!['missing', 'normal', 'unavailable'].includes(scenario)) throw new Error('Unknown scenario');
  if (!['dense_video', 'sparse_snapshots'].includes(mode)) throw new Error('Unknown mode');
  if (!Object.hasOwn(works, work)) throw new Error('Unknown work');
  const online = scenario !== 'unavailable';
  const sparse = mode === 'sparse_snapshots';
  const missing = scenario === 'missing';
  const spec = works[work];
  const status = !online || sparse ? 'insufficient_evidence' : missing ? 'potential_mismatch' : 'supports_plan';
  return {
    synthetic: true, scenario, selectedMode: mode, work, spec, online, sparse,
    mode: online ? mode : 'insufficient_data', status,
    machineCount: online ? 1 : null, resourceCount: online ? (missing ? 0 : 1) : null,
    observedMachineSeconds: online && !sparse ? 720 : null,
    presenceSampleRatio: online ? (missing ? 0 : sparse ? 0.75 : 0.9) : null,
    samples: !online ? '0 пригодных' : sparse ? '4 из 5 снимков' : '720 из 900 выборок',
    badge: !online ? 'Нет наблюдения' : sparse ? 'Редкие снимки' : missing ? 'Требуется проверка' : 'Есть свидетельства',
    headline: !online ? 'Вывод о работе недоступен' : sparse ? 'Видим снимки, не весь интервал' : missing ? 'Ожидаемый ресурс не наблюдается' : 'Наблюдения совместимы с планом',
    explanation: !online
      ? 'Камера недоступна. Неизвестно, есть ли техника в зоне; строительное отклонение не создаётся.'
      : sparse
        ? 'Показаны отдельные наблюдения. Непрерывное присутствие, активность и машинное время между снимками неизвестны.'
        : missing
          ? spec.resource + ' не обнаружен в пригодных наблюдениях текущего окна. По демо-правилу это повод проверить обеспеченность работы.'
          : 'Ожидаемый ресурс виден в зоне. Это не подтверждает объём или завершение работы.',
    limitation: !online ? 'Причина: camera_unavailable' : sparse ? 'Между снимками — неизвестно' : 'Часть интервала не наблюдалась; вывод ограничен видимыми данными',
    evidenceTime: '11.09.2026 · 09:14:32 МСК'
  };
}

export function reviewKey(state) {
  return [state.work, state.scenario, state.selectedMode, 'demo-revision-1'].join(':');
}
