export type EquipmentDoc = {
  id: number;
  name: string;
  english: string;
  description: string;
  detail?: string;
  group: "earth" | "transport" | "lifting" | "road";
};

export const equipmentDocs: EquipmentDoc[] = [
  {
    id: 0,
    name: "Самосвал",
    english: "Dump truck",
    description: "Перевозит и выгружает сыпучие материалы",
    group: "transport",
  },
  {
    id: 1,
    name: "Экскаватор",
    english: "Excavator",
    description: "Копает грунт ковшом",
    group: "earth",
  },
  {
    id: 2,
    name: "Автогрейдер",
    english: "Motor grader",
    description: "Выравнивает и профилирует грунт",
    group: "earth",
  },
  {
    id: 3,
    name: "Каток",
    english: "Roller",
    description: "Уплотняет грунт и покрытие",
    group: "road",
  },
  {
    id: 4,
    name: "Кран-манипулятор",
    english: "Crane manipulator",
    description: "Поднимает грузы автомобильной стрелой",
    group: "lifting",
  },
  {
    id: 5,
    name: "Газель",
    english: "Gazelle",
    description: "Доставляет небольшие грузы на площадку",
    detail: "Лёгкий коммерческий фургон",
    group: "transport",
  },
  {
    id: 6,
    name: "Вилочный погрузчик",
    english: "Forklift Standart",
    description: "Поднимает паллеты на вилах",
    group: "lifting",
  },
  {
    id: 7,
    name: "Погрузчик большой",
    english: "Bucket loader Big",
    description: "Зачерпывает и перемещает сыпучие материалы",
    detail: "Фронтальный ковшовый погрузчик",
    group: "earth",
  },
  {
    id: 28,
    name: "Погрузчик маленький",
    english: "Bucket loader Standart",
    description: "Зачерпывает и перемещает сыпучие материалы",
    detail: "Фронтальный ковшовый погрузчик маленький",
    group: "earth",
  },
  {
    id: 8,
    name: "Автобетоносмеситель",
    english: "Mixer",
    description: "Доставляет бетон во вращающемся барабане",
    group: "transport",
  },
  {
    id: 9,
    name: "Автоцистерна",
    english: "Tanker",
    description: "Перевозит жидкости, поливает и моет",
    detail: "Автоцистерна / поливомоечная машина",
    group: "transport",
  },
  {
    id: 10,
    name: "Бульдозер",
    english: "Bulldozer",
    description: "Толкает грунт передним отвалом",
    group: "earth",
  },
  {
    id: 11,
    name: "Уборочная техника",
    english: "Cleaning equipment",
    description: "Очищает территорию и покрытия",
    detail: "Уборочная и коммунальная техника",
    group: "road",
  },
  {
    id: 12,
    name: "Фура",
    english: "Truck",
    description: "Перевозит материалы в грузовом кузове",
    detail: "Бортовой / грузовой автомобиль",
    group: "transport",
  },
  {
    id: 13,
    name: "Автопоезд с тралом",
    english: "Trailer",
    description: "Перевозит тяжёлую технику на трале",
    detail: "Седельный тягач с низкорамным полуприцепом-тралом",
    group: "transport",
  },
  {
    id: 16,
    name: "Автокран",
    english: "Autocran",
    description: "Поднимает грузы телескопической стрелой",
    group: "lifting",
  },
  {
    id: 17,
    name: "Башенный кран",
    english: "Tower crane",
    description: "Поднимает грузы над строительной площадкой",
    group: "lifting",
  },
  {
    id: 18,
    name: "Асфальтоукладчик",
    english: "Asphalt paver",
    description: "Укладывает асфальтобетон ровным слоем",
    group: "road",
  },
  {
    id: 19,
    name: "Буровая / сваебойная установка",
    english: "Piling rig",
    description: "Бурит скважины или погружает сваи",
    group: "earth",
  },
  {
    id: 20,
    name: "Автобетононасос",
    english: "Concrete pump truck",
    description: "Подаёт бетон по стреле",
    group: "lifting",
  },
];
