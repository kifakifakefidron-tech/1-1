/**
 * 1+1 · Рекламные сообщения по запросам риелторов (Google Таблица + Apps Script)
 *
 * Что делает:
 *  - на листе «Запросы» по имени и запросу готовит личное сообщение (с ротацией синонимов),
 *    ключевые слова и строку поиска «как в чате»;
 *  - держит сообщение в лимите символов (по умолчанию 900), убирая менее важные блоки;
 *  - НИКОГДА не трогает строки со статусом «Отправлено», «Ответил», «Отказ», «Повтор номера»
 *    и любым другим статусом, кроме пустого и «Готово»;
 *  - сам ничего не отправляет: панель «Отправка по очереди» помогает скопировать текст
 *    и отметить «Отправлено».
 *
 * Режимы: меню «1+1» (кнопки) и автогенерация при заполнении строки (onEdit, выключается
 * на листе «Настройки»).
 *
 * Тексты сообщений — в блоке «ШАБЛОНЫ» ниже. Синонимы пишутся так: {вариант 1|вариант 2|вариант 3}.
 * Оффер (ссылка, цена, пробный период, число объектов) меняется на листе «Настройки» без правки кода.
 */

// ═══════════════════════════ НАСТРОЙКА СТРУКТУРЫ ═══════════════════════════

var SHEET_MAIN = 'Запросы';
var SHEET_SETTINGS = 'Настройки';
var SHEET_LOG = 'Лог';

// Номера колонок листа «Запросы» (A = 1)
var COL = { NAME: 1, PHONE: 2, SOURCE: 3, QUERY: 4, KEYS: 5, SEARCH: 6, MSG: 7, LEN: 8, STATUS: 9, DATE: 10, NOTE: 11 };
var NCOL = 11;
var HEADERS = ['Имя', 'Телефон', 'Источник', 'Запрос', 'Ключевые слова', 'Строка для поиска',
  'Готовое сообщение', 'Длина сообщения', 'Статус', 'Дата', 'Комментарий'];

var ST = { READY: 'Готово', SENT: 'Отправлено', ANSWERED: 'Ответил', REFUSED: 'Отказ', REPEAT: 'Повтор номера' };
var STATUS_LIST = [ST.READY, ST.SENT, ST.ANSWERED, ST.REFUSED, ST.REPEAT];

// Параметры листа «Настройки»: [название, значение по умолчанию, пояснение]
var SETTINGS_DEFAULTS = [
  ['Автогенерация', 'вкл', 'вкл — сообщение готовится само, когда в строке заполнены Имя и Запрос; выкл — только по кнопкам меню «1+1»'],
  ['Лимит символов', 900, 'Если сообщение длиннее, скрипт убирает менее важные блоки (сначала — про дубли)'],
  ['Ссылка', 'https://1plus1assistant.ru', 'Адрес сайта в конце сообщения'],
  ['Цена', '399 ₽ за 30 дней', 'Подставляется после «Дальше …»'],
  ['Пробный период', '2 недели бесплатно', 'Подставляется после «первые …»'],
  ['Объектов в базе', '10 000+', 'Подставляется в «В базе … объектов»'],
  ['Пример поиска', '2к фмр до 7 млн', 'Пример, если из запроса не удалось собрать строку поиска'],
  ['Ссылка на поиск по запросу', 'выкл', 'вкл — ссылка ведёт сразу на поиск по запросу человека: 1plus1assistant.ru/?q=2к+фмр+до+7+млн'],
  ['Проверять повтор номера', 'вкл', 'вкл — если этому номеру уже готовили/отправляли сообщение, строка получает статус «Повтор номера»'],
  ['Адрес выгрузки запросов', 'https://1plus1assistant.ru/api/export/requests', 'Откуда таблица забирает новые запросы покупателей. Ключ вводится через меню «1+1 → Ключ сайта…» (на листе его не видно)'],
  ['Первый забор: запросы за дней', 3, 'При самом первом заборе брать только запросы не старше этого числа дней (дальше — все новые)']
];

var LOG_MAX_ROWS = 2000;   // лист «Лог» не разрастается бесконечно
var QUOTE_MAX = 120;       // длиннее — запрос в цитате сокращается
var QUOTE_SHORT = 60;      // короткая цитата (при обрезке до лимита)

// ═══════════════════════════ ШАБЛОНЫ СООБЩЕНИЯ ═══════════════════════════
// %NAME% — имя, %SRC% — чат, %Q% — запрос, %OBJ% — число объектов, %EX% — пример поиска,
// %TRIAL% — пробный период, %PRICE% — цена, %LINK% — ссылка, %ONCE% — фраза «пишу один раз».
// Каждый блок — список шаблонов: берётся случайный, затем в нём случайно выбираются синонимы.

var T = {
  greetName: [
    '{Здравствуйте|Добрый день}, %NAME%{.|!}',
    '%NAME%, {здравствуйте|добрый день}{.|!}'
  ],
  greetAnon: ['{Здравствуйте|Добрый день}{.|!}'],

  seenSrc: [
    '{Увидел|Заметил} {в чате|в группе} «%SRC%» ваш запрос «%Q%».',
    '{Увидел|Заметил} ваш запрос в {чате|группе} «%SRC%»: «%Q%».'
  ],
  seenPlain: [
    '{Увидел|Заметил|Прочитал} ваш запрос «%Q%».',
    '{Увидел|Заметил} ваш запрос: «%Q%».',
    'Вижу ваш запрос «%Q%».'
  ],

  // «Пишу один раз» — обещание человеку, остаётся даже в коротком варианте
  once: ['{Пишу один раз|Пишу только один раз|Это единственное сообщение}: если {не интересно|неинтересно|не актуально}, просто не отвечайте{, больше писать не буду|, больше не побеспокою|, повторно писать не буду|}.'],
  polite: [
    '{Понимаю|Знаю}, что в чатах и так {много сообщений|хватает сообщений|очень много сообщений}, поэтому {коротко|кратко|по делу} и без {навязчивости|навязывания}. %ONCE%',
    'В чатах и так {много сообщений|шумно|хватает сообщений}, поэтому {коротко|кратко} и {без навязчивости|по делу}. %ONCE%'
  ],

  service: [
    '{Мы сделали|Мы запустили|Мы с командой сделали} 1+1 — сервис для риелторов, чтобы не {читать|листать|просматривать} сотни чатов {руками|вручную}. %TAIL%',
    '{Мы сделали|Мы запустили} 1+1 для риелторов, чтобы не {читать|листать|просматривать} сотни чатов {руками|вручную}. %TAIL%'
  ],
  serviceTail: ['{Сервис собирает|Сервис сводит|Он собирает} объявления из MAX, Telegram и WhatsApp в {один поиск|одну базу с поиском|одно место}. В базе %OBJ% объектов: квартиры, дома, участки, коммерция. %EXP% {В карточке есть карта, цена за метр и история цены|В каждой карточке — карта, цена за метр и история цены|В карточке объекта видно карту, цену за метр и историю цены}.'],
  exGeneric: ['Искать можно {как в чате|обычной фразой, как в чате|так же, как пишете в чате}: «%EX%».'],
  exPersonal: [
    '{Ваш запрос|Такой запрос} так и ищется: «%EX%».',
    'Искать можно {как в чате|обычной фразой}, например ваш запрос: «%EX%».'
  ],
  serviceShort: ['{Мы сделали|Мы запустили} 1+1 для риелторов: объявления из MAX, Telegram и WhatsApp в {одном поиске|одной базе|одном месте}, %OBJ% объектов. %EXP%'],
  exShortGeneric: ['{Искать можно как в чате|Ищется как в чате|Поиск — как в чате}: «%EX%».'],
  exShortPersonal: ['{Ваш запрос так и ищется|Ваш запрос ищется прямо так}: «%EX%».'],

  dup: ['По дублям {так|коротко|честно}: одинаковые объявления в одной подаче убираем. Если один объект {выставили|выложили|разместили} разные агенты и у них {отличается цена|разная цена}, это будут разные карточки. Мы {специально|намеренно|сознательно} не склеиваем разные условия{. Это рынок, и выбирать вам, с кем работать|: это рынок, и выбор, с кем работать, за вами}.'],

  feat: ['{Ещё|Также|Кроме того,} внутри есть подборки для клиентов: отправляете одну ссылку, человек ставит «нравится» или «хочу посмотреть», {и вы сразу видите реакцию|а вы сразу видите его реакцию|и реакция сразу видна вам}. Есть запросы коллег: кто что ищет {прямо сейчас|сейчас}. {Планер дня|Планер} с напоминаниями {по показам и созвонам|о показах и созвонах}. И уведомления, если цена {снизилась|упала} или появился новый объект под вашего клиента.'],
  featShort: ['{Внутри|Ещё внутри|Ещё в сервисе}: подборки для клиентов с реакциями «нравится» и «хочу посмотреть», запросы коллег, {планер показов и созвонов|планер с напоминаниями}, уведомления о снижении цены и новых объектах под клиента.'],

  offer: ['{Если захотите посмотреть|Если интересно попробовать|Если решите посмотреть}, первые %TRIAL%, без автосписаний. Дальше %PRICE%. {Ссылка|Сайт|Посмотреть можно здесь}: %LINK%'],
  offerShort: [
    'Первые %TRIAL%, без автосписаний, дальше %PRICE%: %LINK%',
    '{Попробовать|Посмотреть}: %LINK% — первые %TRIAL%, без автосписаний, дальше %PRICE%.'
  ],

  close: ['{Если не актуально, без проблем|Если не актуально — ничего страшного|Не актуально — без проблем|Если неинтересно — всё в порядке}. {Спасибо, что дочитали|Спасибо, что дочитали до конца|Спасибо за внимание|Спасибо за ваше время}{.|!}']
};

// Ступени сокращения: берётся первая, что влезает в лимит.
// Всегда остаются: приветствие с запросом, суть сервиса, функции, оффер, финал.
var LEVELS = [
  { dup: true,  polite: 'full',  service: 'full',  feat: 'full',  offer: 'full',  quote: 'full' },
  { dup: false, polite: 'full',  service: 'full',  feat: 'full',  offer: 'full',  quote: 'full' },
  { dup: false, polite: 'short', service: 'full',  feat: 'full',  offer: 'full',  quote: 'full' },
  { dup: false, polite: 'short', service: 'full',  feat: 'short', offer: 'full',  quote: 'full' },
  { dup: false, polite: 'short', service: 'short', feat: 'full',  offer: 'full',  quote: 'full' },
  { dup: false, polite: 'short', service: 'short', feat: 'short', offer: 'full',  quote: 'full' },
  { dup: false, polite: 'short', service: 'short', feat: 'short', offer: 'short', quote: 'full' },
  { dup: false, polite: 'short', service: 'short', feat: 'short', offer: 'short', quote: 'short' },
  { dup: false, polite: 'none',  service: 'short', feat: 'short', offer: 'short', quote: 'short' }
];

// ═══════════════════════════ МЕНЮ И ТРИГГЕРЫ ═══════════════════════════

function onOpen() {
  try { ensureSheets_(); } catch (e) { /* у зрителя без прав на правку — просто меню */ }
  SpreadsheetApp.getUi().createMenu('1+1')
    .addItem('Сгенерировать сообщения', 'generateAll')
    .addItem('Сгенерировать выбранную строку', 'generateRow')
    .addItem('Перегенерировать выбранную строку', 'regenerateRow')
    .addSeparator()
    .addItem('Отправка по очереди (панель)', 'showSender')
    .addItem('Отметить выбранные «Отправлено»', 'markSelectedSent')
    .addSeparator()
    .addItem('Забрать новые запросы с сайта', 'importRequests')
    .addItem('Автозабор с сайта: включить (каждые 15 мин)', 'enableAutoImport')
    .addItem('Автозабор с сайта: выключить', 'disableAutoImport')
    .addItem('Ключ сайта…', 'setExportKey')
    .addSeparator()
    .addItem('Очистить готовые', 'clearReady')
    .addItem('Подготовить таблицу (шапка, статусы, цвета)', 'setupSheet')
    .addItem('Настройки', 'openSettings')
    .addToUi();
}

/**
 * Автогенерация. Простой триггер: Google запускает его только при правке ЧЕЛОВЕКОМ —
 * изменения, которые пишет сам скрипт, onEdit не вызывают (рекурсии нет). Дополнительно:
 * работаем только на листе «Запросы», только если правили колонку A или D, только для
 * строк с пустым статусом, и под общей блокировкой с кнопками.
 */
function onEdit(e) {
  try {
    if (!e || !e.range) return;
    var range = e.range;
    var sh = range.getSheet();
    if (sh.getName() !== SHEET_MAIN) return;
    var r1 = Math.max(range.getRow(), 2), r2 = range.getLastRow();
    if (r2 < 2) return;                                   // правка шапки
    var c1 = range.getColumn(), c2 = range.getLastColumn();
    var touchedName = c1 <= COL.NAME && c2 >= COL.NAME;
    var touchedQuery = c1 <= COL.QUERY && c2 >= COL.QUERY;
    if (!touchedName && !touchedQuery) return;            // другие колонки — не наше дело
    var cfg = readSettings_();
    if (!cfg.autogen) return;

    var lock = getLock_();
    if (lock && !lock.tryLock(5000)) {
      logError_(r1, 'автогенерация', 'Таблица занята другой операцией — нажмите «1+1 → Сгенерировать сообщения»');
      return;
    }
    try {
      var rows = [];
      for (var r = r1; r <= r2; r++) rows.push(r);
      processRows_(sh, rows, 'auto', cfg);
    } finally {
      if (lock) lock.releaseLock();
    }
  } catch (err) {
    logError_(e && e.range ? e.range.getRow() : '', 'автогенерация', err);
  }
}

// ═══════════════════════════ КНОПКИ МЕНЮ ═══════════════════════════

/** Все новые строки: Имя и Запрос заполнены, статус пустой (или «Готово» без текста). */
function generateAll() {
  withLock_(function () {
    var sh = mainSheet_();
    var res = processRows_(sh, null, 'new', readSettings_());
    toast_(summary_(res, 'Новых строк нет'));
  });
}

/** Только выделенные строки (готовые «Готово» не перезаписываются). */
function generateRow() {
  var rows = selectedRows_();
  if (!rows) return;
  withLock_(function () {
    var res = processRows_(mainSheet_(), rows, 'new', readSettings_());
    explain_(res);
  });
}

/** Принудительно: перезаписывает и «Готово». «Отправлено», «Ответил», «Отказ» — никогда. */
function regenerateRow() {
  var rows = selectedRows_();
  if (!rows) return;
  withLock_(function () {
    var res = processRows_(mainSheet_(), rows, 'force', readSettings_());
    explain_(res);
  });
}

/** Очищает E–H у строк «Готово» и возвращает их в «новые» (статус и дата — тоже очищаются). */
function clearReady() {
  var ui = SpreadsheetApp.getUi();
  var sh = mainSheet_();
  var last = sh.getLastRow();
  if (last < 2) { toast_('Строк нет'); return; }
  var vals = sh.getRange(2, 1, last - 1, NCOL).getValues();
  var n = 0;
  vals.forEach(function (v) { if (statusOf_(v[COL.STATUS - 1]) === ST.READY) n++; });
  if (!n) { toast_('Строк со статусом «Готово» нет'); return; }
  var ans = ui.alert('Очистить готовые',
    'Очистить ключевые слова, строку поиска, сообщение и длину у ' + n + ' строк со статусом «Готово»?\n' +
    'Статус и дата тоже очистятся — строки станут «новыми», и их можно сгенерировать заново.\n' +
    '«Отправлено», «Ответил», «Отказ» не трогаются.', ui.ButtonSet.YES_NO);
  if (ans !== ui.Button.YES) return;
  withLock_(function () {
    var v2 = sh.getRange(2, 1, last - 1, NCOL).getValues();   // перечитываем под блокировкой
    var updates = {};
    v2.forEach(function (v, i) {
      if (statusOf_(v[COL.STATUS - 1]) !== ST.READY) return;
      updates[i + 2] = ['', '', '', '', '', '', cleanAutoNote_(v[COL.NOTE - 1])];
    });
    writeUpdates_(sh, updates);
    toast_('Очищено строк: ' + Object.keys(updates).length);
  });
}

/** Отмечает выделенные строки со статусом «Готово» как «Отправлено». */
function markSelectedSent() {
  var rows = selectedRows_();
  if (!rows) return;
  withLock_(function () {
    var sh = mainSheet_();
    var n = 0;
    rows.forEach(function (r) {
      var st = statusOf_(sh.getRange(r, COL.STATUS).getValue());
      if (st !== ST.READY) return;
      sh.getRange(r, COL.STATUS, 1, 2).setValues([[ST.SENT, new Date()]]);
      n++;
    });
    toast_(n ? 'Отмечено «Отправлено»: ' + n : 'Среди выделенных нет строк со статусом «Готово»');
  });
}

function openSettings() {
  setupSettings();
  var ss = SpreadsheetApp.getActive();
  ss.setActiveSheet(ss.getSheetByName(SHEET_SETTINGS));
}

// ═══════════════════════════ ОСНОВНАЯ ОБРАБОТКА ═══════════════════════════

/**
 * Обрабатывает строки листа.
 * rows: массив номеров строк или null (все строки).
 * mode: 'new' — пустой статус (и «Готово» без сообщения);
 *       'auto' — только пустой статус (автогенерация);
 *       'force' — пустой статус и «Готово» (перегенерация).
 * Строки с любым другим статусом не трогаются никогда.
 */
function processRows_(sh, rows, mode, cfg) {
  var res = { done: 0, repeat: 0, skipped: 0, errors: 0, reasons: [] };
  var last = sh.getLastRow();
  if (last < 2) return res;
  var vals = sh.getRange(2, 1, last - 1, NCOL).getValues();

  // Карта номеров: кому уже готовили/отправляли сообщение (любой непустой статус)
  var phoneRow = {};
  if (cfg.checkRepeat) {
    vals.forEach(function (v, i) {
      var p = canonPhone_(v[COL.PHONE - 1]);
      if (p && statusOf_(v[COL.STATUS - 1]) && !(p in phoneRow)) phoneRow[p] = i + 2;
    });
  }

  var targets = rows ? rows.filter(function (r) { return r >= 2 && r <= last; }) : vals.map(function (_, i) { return i + 2; });
  var updates = {};

  targets.forEach(function (r) {
    var v = vals[r - 2];
    var name = String(v[COL.NAME - 1] || '').trim();
    var query = String(v[COL.QUERY - 1] || '').trim();
    var status = statusOf_(v[COL.STATUS - 1]);
    var hasMsg = String(v[COL.MSG - 1] || '').trim() !== '';

    if (!query) {                              // без запроса писать не о чем; без имени — «Здравствуйте» без имени
      if (rows && name) res.reasons.push('строка ' + r + ': нет запроса (колонка D)');
      res.skipped++;
      return;
    }
    // «Готово» без имени, а имя потом вписали — пересобираем с именем (только автоматом, пока не отправлено)
    var nameAdded = status === ST.READY && name && /(^|;\s*)без имени/.test(String(v[COL.NOTE - 1] || ''));
    var allowed =
      status === '' ||
      (status === ST.READY && mode === 'force') ||
      (status === ST.READY && (mode === 'new' || mode === 'auto') && (!hasMsg || nameAdded));
    if (!allowed) {
      if (rows) res.reasons.push('строка ' + r + ': статус «' + v[COL.STATUS - 1] + '» — не трогаю' +
        (status === ST.READY ? ' (для перезаписи — «Перегенерировать выбранную строку»)' : ''));
      res.skipped++;
      return;
    }

    try {
      var phone = canonPhone_(v[COL.PHONE - 1]);
      var note = cleanAutoNote_(v[COL.NOTE - 1]);
      if (cfg.checkRepeat && phone && phoneRow[phone] && phoneRow[phone] !== r) {
        updates[r] = ['', '', '', '', ST.REPEAT, new Date(), joinNote_(note, 'этому номеру уже писали: строка ' + phoneRow[phone])];
        res.repeat++;
        return;
      }
      var kw = extractKeywords_(query);
      var msg = buildMessage_({ name: name, source: v[COL.SOURCE - 1], query: query, search: kw.search }, cfg);
      if (msg.trimmed) note = joinNote_(note, 'обрезано до ' + cfg.limit);
      if (!msg.named) note = joinNote_(note, 'без имени');
      updates[r] = [kw.keys.join(', '), kw.search, msg.text, msg.len, ST.READY, new Date(), note];
      if (phone && !phoneRow[phone]) phoneRow[phone] = r;   // следующие строки с этим номером — повтор
      res.done++;
    } catch (err) {
      res.errors++;
      logError_(r, 'генерация', err);
    }
  });

  writeUpdates_(sh, updates);
  return res;
}

/** Пишет колонки E–K пачками подряд идущих строк (быстро и без лишних вызовов). */
function writeUpdates_(sh, updates) {
  var rows = Object.keys(updates).map(Number).sort(function (a, b) { return a - b; });
  var i = 0;
  while (i < rows.length) {
    var j = i;
    while (j + 1 < rows.length && rows[j + 1] === rows[j] + 1) j++;
    var block = [];
    for (var k = i; k <= j; k++) block.push(updates[rows[k]]);
    sh.getRange(rows[i], COL.KEYS, block.length, NCOL - COL.KEYS + 1).setValues(block);
    sh.getRange(rows[i], COL.DATE, block.length, 1).setNumberFormat('dd.MM.yyyy HH:mm');
    i = j + 1;
  }
}

// ═══════════════════════════ СБОРКА СООБЩЕНИЯ ═══════════════════════════

/**
 * d: {name, source, query, search}; cfg — настройки.
 * Возвращает {text, len, trimmed, level}.
 */
function buildMessage_(d, cfg) {
  var name = cleanName_(d.name);
  var src = cleanSource_(d.source);
  var q = cleanQuery_(d.query);
  var search = String(d.search || '').trim();
  var personal = search.split(' ').length >= 2 && search.length <= 45;
  var ex = personal ? search : cfg.example;

  var qFull = q.length <= QUOTE_MAX ? q : cutWords_(q, QUOTE_MAX - 5) + '…';
  var qShort = q.length <= QUOTE_SHORT ? q : (search || cutWords_(q, QUOTE_SHORT - 5) + '…');

  var link = String(cfg.link || '').replace(/\/+$/, '');
  if (cfg.searchLink && search) link += '/?q=' + searchParam_(search);
  var trial = String(cfg.trial || '').replace(/^\s*первые\s+/i, '');

  var vars = { NAME: name, SRC: src, OBJ: cfg.objects, EX: ex, TRIAL: trial, PRICE: cfg.price, LINK: link };

  // Каждый блок крутится один раз — на всех ступенях сокращения выбор синонимов одинаковый
  var once = spin_(pick_(T.once));
  var seenTpl = spin_(pick_(src && Math.random() < 0.6 ? T.seenSrc : T.seenPlain));
  var exFull = spin_(pick_(personal && Math.random() < 0.7 ? T.exPersonal : T.exGeneric));
  var exShort = spin_(pick_(personal && Math.random() < 0.7 ? T.exShortPersonal : T.exShortGeneric));
  var p = {
    greet: fill_(spin_(pick_(name ? T.greetName : T.greetAnon)), vars),
    seen: fill_(seenTpl, merge_(vars, { Q: qFull })),
    seenShort: fill_(seenTpl, merge_(vars, { Q: qShort })),
    polite: fill_(spin_(pick_(T.polite)).split('%ONCE%').join(once), vars),
    politeShort: fill_(once, vars),
    service: fill_(spin_(pick_(T.service)).split('%TAIL%').join(spin_(pick_(T.serviceTail)).split('%EXP%').join(exFull)), vars),
    serviceShort: fill_(spin_(pick_(T.serviceShort)).split('%EXP%').join(exShort), vars),
    dup: fill_(spin_(pick_(T.dup)), vars),
    feat: fill_(spin_(pick_(T.feat)), vars),
    featShort: fill_(spin_(pick_(T.featShort)), vars),
    offer: fill_(spin_(pick_(T.offer)), vars),
    offerShort: fill_(spin_(pick_(T.offerShort)), vars),
    close: fill_(spin_(pick_(T.close)), vars)
  };

  var limit = cfg.limit;
  for (var i = 0; i < LEVELS.length; i++) {
    var text = compose_(p, LEVELS[i]);
    if (len_(text) <= limit) return { text: text, len: len_(text), trimmed: i > 0, level: i, named: !!name };
  }
  // Лимит очень маленький: оставляем приветствие, оффер и финал, середину режем по словам
  var lv = LEVELS[LEVELS.length - 1];
  var head = [p.greet, p.seenShort].join(' ');
  var tail = [p.offerShort, p.close].join('\n\n');
  var room = limit - len_(head) - len_(tail) - 4;
  var mid = room > 20 ? cutWords_(compose_(p, lv).split('\n\n').slice(1, 3).join(' '), room - 1) + '…' : '';
  var t2 = [head, mid, tail].filter(Boolean).join('\n\n');
  if (len_(t2) > limit) t2 = cutWords_(t2, limit - 1) + '…';
  return { text: t2, len: len_(t2), trimmed: true, level: LEVELS.length, named: !!name };
}

function compose_(p, lv) {
  var politeText = lv.polite === 'full' ? p.polite : lv.polite === 'short' ? p.politeShort : '';
  var p1 = [p.greet, lv.quote === 'short' ? p.seenShort : p.seen, politeText].filter(Boolean).join(' ');
  return [
    p1,
    lv.service === 'full' ? p.service : p.serviceShort,
    lv.dup ? p.dup : '',
    lv.feat === 'full' ? p.feat : p.featShort,
    lv.offer === 'full' ? p.offer : p.offerShort,
    p.close
  ].filter(Boolean).join('\n\n');
}

/** Раскрывает {а|б|в} (можно вкладывать), выбирая случайный вариант. */
function spin_(tpl) {
  var s = String(tpl), re = /\{([^{}]*)\}/;
  for (var guard = 0; guard < 500 && re.test(s); guard++) {
    s = s.replace(re, function (_, body) { return pick_(body.split('|')); });
  }
  return s;
}

/** Подстановка %ИМЯ% → значение (через split/join, чтобы символы $ в данных не ломали замену). */
function fill_(s, vars) {
  Object.keys(vars).forEach(function (k) { s = s.split('%' + k + '%').join(String(vars[k] == null ? '' : vars[k])); });
  return s;
}

function pick_(arr) { return arr[Math.floor(Math.random() * arr.length)]; }
function merge_(a, b) { var o = {}; Object.keys(a).forEach(function (k) { o[k] = a[k]; }); Object.keys(b).forEach(function (k) { o[k] = b[k]; }); return o; }
function len_(s) { return Array.from(String(s)).length; }   // символы, а не половинки эмодзи

function cutWords_(s, max) {
  s = String(s);
  if (len_(s) <= max) return s;
  var cut = Array.from(s).slice(0, max).join('');
  var sp = cut.lastIndexOf(' ');
  return (sp > max * 0.5 ? cut.slice(0, sp) : cut).replace(/[\s,;:.\-–—]+$/, '');
}

/** Имя для обращения: первое настоящее слово («Ольга АН Этажи» → «Ольга»), + отчество, если есть. */
function cleanName_(raw) {
  var words = String(raw || '').replace(/[^A-Za-zА-Яа-яЁё\- ]+/g, ' ').trim().split(/\s+/).filter(Boolean);
  var SKIP = /^(ип|ан|ооо|ао|ук|жк|агент|агентство|риелтор|риэлтор|риелтер|риэлтер|realtor|realty|недвижимость|эксперт|юрист|брокер|маклер|менеджер|офис|группа|компания|собственник)$/i;
  for (var i = 0; i < words.length; i++) {
    var w = words[i];
    if (SKIP.test(w) || w.replace(/-/g, '').length < 2) continue;
    if (/^[А-ЯЁA-Z]{2,3}$/.test(w) && words.length > 1) continue;   // АН, ЦН и т. п.
    var out = cap_(w);
    var next = words[i + 1];
    if (next && /(вич|вна|чна|ична|инична)$/i.test(next)) out += ' ' + cap_(next);
    return out;
  }
  return '';
}

function cap_(w) {
  return w.toLowerCase().split('-').map(function (x) { return x ? x.charAt(0).toUpperCase() + x.slice(1) : x; }).join('-');
}

/** Название чата без «MAX/TG/WA» и слова «чат»: «MAX ФМР чат» → «ФМР». */
function cleanSource_(raw) {
  var s = String(raw || '').replace(/[«»"“”„]/g, ' ').trim();
  var PLATFORM = /^(max|макс|tg|тг|telegram|телеграм|телеграмм|wa|ва|whatsapp|ватсап|вотсап|вацап)(?![A-Za-zА-Яа-яЁё0-9])\s*[:\-–—|·•/]?\s*/i;
  for (var i = 0; i < 3 && PLATFORM.test(s); i++) s = s.replace(PLATFORM, '');
  s = s.replace(/(^|\s)(чат|чате|группа|канал)(?=\s|$)/gi, ' ').replace(/\s+/g, ' ').trim();
  return s.length >= 2 && s.length <= 40 ? s : '';
}

/** Запрос для цитаты: одна строка, без эмодзи, «#», кавычек и подписи «Запрос:». */
function cleanQuery_(raw) {
  return String(raw || '')
    .replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE0F}\u{200D}\u{20E3}]/gu, ' ')
    .replace(/[«»"“”„#]/g, '')
    .replace(/^\s*(?:срочно\s*[!.:]*\s*)?(?:новый\s+)?запрос\s*[:\-–—!.]*\s*/i, '')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/[\s.,;!]+$/, '');
}

/** Для ссылки ?q=: пробелы → «+», кириллица остаётся читаемой. */
function searchParam_(s) {
  return String(s).replace(/[%&#+?=]/g, function (c) { return encodeURIComponent(c); }).replace(/\s+/g, '+');
}

// ═══════════════════════════ КЛЮЧЕВЫЕ СЛОВА ═══════════════════════════

var LB_ = '(?<![0-9a-zа-яё])';   // начало слова (\b в JavaScript не понимает кириллицу)
var RB_ = '(?![0-9a-zа-яё])';    // конец слова
var W_ = '[а-яё]*';              // хвост слова
function rx_(src) { return new RegExp(src.split('{L}').join(LB_).split('{R}').join(RB_), 'g'); }

var NUM_ = '(\\d{1,3}(?:[ \\u00a0]\\d{3})+(?:[.,]\\d+)?|\\d+(?:[.,]\\d+)?)(?![0-9])';   // число целиком: «до 10 минут» не станет «до 1»
var UNIT_ = '(млн' + W_ + '\\.?|миллион' + W_ + '|лям' + W_ + '|мл{R}|м{R}|тыс' + W_ + '\\.?|т\\.?\\s?р\\.?|тр{R}|к{R}|руб' + W_ + '\\.?|р\\.|р{R}|₽)';
var NOT_MONEY_ = '(?!\\s*(?:мин|км|шаг|час|год|лет|сек|остан|этаж|комн))';

var DISTRICTS_ = [
  ['фмр', 'фмр|фестивальн' + W_],
  ['юмр', 'юмр|юбилейн' + W_],
  ['гмр', 'гмр|гидростро' + W_],
  ['кмр', 'кмр|комсомольск' + W_],
  ['чмр', 'чмр|черемушк' + W_],
  ['рип', 'рип|ипподром' + W_],
  ['цмр', 'цмр|центральн' + W_ + '\\s+(?:район|мкр|микрорайон)'],
  ['центр', 'центр(?:е|а|у)?'],
  ['смр', 'смр|славянск' + W_],
  ['вкмр', 'вкмр|восточк' + W_ + '|кругликовск' + W_ + '|восточно-кругликовск' + W_],
  ['школьный', 'школьн' + W_],
  ['музыкальный', 'музыкальн' + W_],
  ['пашковский', 'пашковск' + W_ + '|пашковк' + W_],
  ['немецкая деревня', 'немецк' + W_ + '\\s+деревн' + W_],
  ['кожзавод', 'кожзавод' + W_],
  ['табачка', 'табачн' + W_ + '|табачк' + W_],
  ['энка', 'энка|энке|энку'],
  ['зип', 'зип|измерительн' + W_],
  ['хбк', 'хбк'],
  ['мхг', 'мхг|микрохирург' + W_],
  ['ккб', 'ккб'],
  ['тэц', 'тэц'],
  ['почтовый', 'почтов' + W_],
  ['дубинка', 'дубинк' + W_],
  ['солнечный', 'солнечн' + W_],
  ['губернский', 'губернск' + W_],
  ['московский', 'московск' + W_ + '\\s+(?:мкр|микрорайон|район)'],
  ['северный', 'северн' + W_ + '\\s+(?:мкр|микрорайон|район)'],
  ['витаминкомбинат', 'витаминк' + W_],
  ['аврора', 'аврор' + W_],
  ['знаменский', 'знаменск' + W_],
  ['яблоновский', 'яблоновск' + W_],
  ['новая адыгея', 'нов' + W_ + '\\s+адыге' + W_],
  ['российский', 'российск' + W_],
  ['калинино', 'калинин' + W_],
  ['березовый', 'березов' + W_],
  ['прикубанский', 'прикубанск' + W_ + '|пко'],
  ['карасунский', 'карасунск' + W_]
];

var TYPES_ = [
  ['{L}пол\\s*-?\\s*дома{R}', 'полдома'],
  ['{L}квартир' + W_, 'квартира'],
  ['{L}апартамент' + W_, 'апартаменты'],
  ['{L}гостинк' + W_, 'гостинка'],
  ['{L}малосемейк' + W_, 'малосемейка'],
  ['{L}комнат(?:а|у|ы)?{R}', 'комната'],
  ['{L}таунхаус' + W_, 'таунхаус'],
  ['{L}дуплекс' + W_, 'дуплекс'],
  ['{L}(?:дом|дома|домик|домика|домом|домов|коттедж' + W_ + '){R}', 'дом'],
  ['{L}дач(?:а|у|и|ный|ную|ное){R}', 'дача'],
  ['{L}участ(?:ок|ка|ком|ки|ке|ков){R}|{L}зу{R}', 'участок'],
  ['{L}ижс{R}', 'ижс'],
  ['{L}коммерц' + W_ + '|{L}коммерческ' + W_, 'коммерция'],
  ['{L}офис' + W_, 'офис'],
  ['{L}помещени' + W_, 'помещение'],
  ['{L}склад' + W_, 'склад'],
  ['{L}магазин' + W_, 'магазин'],
  ['{L}гараж' + W_, 'гараж'],
  ['{L}машино\\s*-?\\s*мест' + W_ + '|{L}парковочн' + W_ + '\\s+мест' + W_, 'машиноместо'],
  ['{L}здани' + W_, 'здание'],
  ['{L}новостро' + W_ + '|{L}дду{R}', 'новостройка'],
  ['{L}вторичк' + W_ + '|{L}вторичн' + W_, 'вторичка'],
  ['{L}посуточн' + W_, 'посуточно'],
  ['{L}аренд' + W_ + '|{L}сниму{R}|{L}снять{R}|{L}снимет{R}', 'аренда']
];

var MISC_ = [
  ['{L}семейн' + W_ + '\\s+ипотек' + W_, 'семейная ипотека'],
  ['{L}военн' + W_ + '\\s+ипотек' + W_, 'военная ипотека'],
  ['{L}ипотек' + W_, 'ипотека'],
  ['{L}мат(?:еринск' + W_ + ')?\\.?\\s*-?\\s*капитал' + W_ + '|{L}маткапитал' + W_ + '|{L}мск{R}', 'маткапитал'],
  ['{L}сертификат' + W_, 'сертификат'],
  ['{L}наличн' + W_ + '|{L}нал{R}', 'наличные'],
  ['{L}без\\s+ремонт' + W_ + '|{L}под\\s+ремонт{R}|{L}требует\\s+ремонт' + W_ + '|{L}без\\s+отделк' + W_ + '|{L}под\\s+отделк' + W_, 'без ремонта'],
  ['{L}пчо{R}|{L}п\\s*/\\s*чо{R}|{L}предчистов' + W_ + '|{L}white\\s*box{R}|{L}вайт\\s*бокс{R}', 'предчистовая'],
  ['{L}чернов' + W_, 'черновая'],
  ['{L}(?:евро)?ремонт' + W_ + '|{L}с\\s+отделк' + W_ + '|{L}чистов' + W_, 'с ремонтом'],
  ['{L}без\\s+мебел' + W_, 'без мебели'],
  ['{L}мебел' + W_ + '|{L}меблирован' + W_, 'с мебелью'],
  ['{L}балкон' + W_ + '|{L}лоджи' + W_, 'балкон'],
  ['{L}паркинг' + W_ + '|{L}парковк' + W_, 'парковка'],
  ['{L}срочн' + W_, 'срочно']
];

var CX_STOP_ = /^(до|от|в|во|на|за|с|со|по|и|или|а|не|без|для|под|кроме|либо|рядом|около|бюджет|цена|ищу|ищем|нужна|нужен|нужно|рассмотрю|рассматриваю|желательно|район|мкр|квартира|квартиру|студия|студию|дом|этаж|ипотека|срочно|можно|только)$/;

/**
 * Разбирает запрос. Возвращает {keys: [...], search: 'строка поиска'}.
 * Ключевые слова идут в том порядке, в каком их написал человек.
 */
function extractKeywords_(query) {
  var s0 = String(query || '').toLowerCase().replace(/ё/g, 'е').replace(/ /g, ' ')
    .replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}\u{200D}\u{20E3}]/gu, ' ')
    .replace(/[«»"“”„'#]/g, ' ');
  var ctx = { s: s0, tokens: [] };
  var rent = /аренд|сниму|снять|посуточн|в месяц|\/мес/.test(s0);

  // 1. Площадь: «60-80 м2», «от 50 кв.м»
  take_(ctx, rx_('{L}(?:(от|до|не менее|не меньше|не более|больше|более|меньше|менее)\\s*)?' + NUM_ +
      '\\s*(?:(?:-|–|—)\\s*' + NUM_ + '\\s*)?(м2|м²|кв\\.?\\s?м\\.?|м\\.?\\s?кв\\.?|квм|кв\\.?|метр' + W_ + '|квадрат' + W_ + ')(?![0-9a-zа-яё])'),
    function (m) {
      var a = num_(m[2]), b = m[3] ? num_(m[3]) : null, unit = m[4];
      var strong = /^(м2|м²|кв\.?\s?м|м\.?\s?кв|квм)/.test(unit);
      if (!strong && a < 10) return null;                      // «2 кв» — это комнаты
      if (b) return fmt_(a) + '-' + fmt_(b) + ' м2';
      if (/^(от|не менее|не меньше|больше|более)$/.test(m[1] || '')) return 'от ' + fmt_(a) + ' м2';
      if (/^(до|не более|меньше|менее)$/.test(m[1] || '')) return 'до ' + fmt_(a) + ' м2';
      return fmt_(a) + ' м2';
    });

  // 2. Земля: «6 соток», «от 4 сот», «1 га»
  take_(ctx, rx_('{L}(?:(от|до|не менее|больше|более)\\s*)?' + NUM_ + '\\s*(?:(?:-|–|—)\\s*' + NUM_ + '\\s*)?(?:сот' + W_ + '\\.?)'),
    function (m) {
      var a = fmt_(num_(m[2])), b = m[3] ? fmt_(num_(m[3])) : null;
      if (b) return a + '-' + b + ' сот.';
      if (/^(от|не менее|больше|более)$/.test(m[1] || '')) return 'от ' + a + ' сот.';
      if (m[1] === 'до') return 'до ' + a + ' сот.';
      return a + ' сот.';
    });
  take_(ctx, rx_('{L}' + NUM_ + '\\s*(?:га|гектар' + W_ + '){R}'), function (m) { return fmt_(num_(m[1])) + ' га'; });

  // 3. Этаж
  var FIRST = '(?:1|перв' + W_ + ')(?:-?(?:го|й|ый|ом))?';
  take_(ctx, rx_('{L}(?:не|кроме)\\s*(?:на\\s*)?' + FIRST + '\\s*(?:и|,)\\s*(?:не\\s*)?(?:на\\s*)?последн' + W_ + '(?:\\s*этаж' + W_ + ')?'),
    function () { return ['не первый этаж', 'не последний этаж']; });
  take_(ctx, rx_('{L}(?:не|кроме)\\s*(?:на\\s*)?' + FIRST + '\\s*этаж' + W_), function () { return 'не первый этаж'; });
  take_(ctx, rx_('{L}(?:не|кроме)\\s*(?:на\\s*)?последн' + W_ + '(?:\\s*этаж' + W_ + ')?'), function () { return 'не последний этаж'; });
  take_(ctx, rx_('{L}(\\d{1,2})\\s*(?:-?х)?\\s*-?\\s*(?:этажн' + W_ + ')'), function (m) { return m[1] + '-этажный'; });
  take_(ctx, rx_('{L}(\\d{1,2})\\s*[-–]\\s*(\\d{1,2})\\s*(?:-?(?:й|ой|ый|ом|ем|м|го|ого))?\\s*этаж' + W_), function (m) { return m[1] + '-' + m[2] + ' этаж'; });
  take_(ctx, rx_('{L}(от|с|не ниже|выше|до|не выше|ниже)\\s*(\\d{1,2})\\s*(?:-?(?:й|ой|ый|ом|ем|м|го|ого))?\\s*этаж' + W_), function (m) {
    var p = m[1];
    if (p === 'от' || p === 'с' || p === 'не ниже') return 'от ' + m[2] + ' этажа';
    if (p === 'выше') return 'выше ' + m[2] + ' этажа';
    return 'до ' + m[2] + ' этажа';
  });
  take_(ctx, rx_('{L}([2-4])\\s*(?:-?х)?\\s*этажа{R}'), function (m) { return m[1] + '-этажный'; });   // «дом, 2 этажа»
  take_(ctx, rx_('{L}(\\d{1,2})\\s*(?:-?(?:й|ой|ый|ом|ем|м|го|ого))?\\s*этаж' + W_), function (m) { return m[1] + ' этаж'; });
  take_(ctx, rx_('{L}этаж' + W_ + '\\s*(?:№|:|-)?\\s*(\\d{1,2}){R}'), function (m) { return m[1] + ' этаж'; });
  take_(ctx, rx_('{L}' + FIRST + '\\s*этаж' + W_), function () { return '1 этаж'; });
  take_(ctx, rx_('{L}высок' + W_ + '\\s*этаж' + W_), function () { return 'высокий этаж'; });
  take_(ctx, rx_('{L}последн' + W_ + '\\s*этаж' + W_), function () { return 'последний этаж'; });

  // 4. Комнаты
  take_(ctx, rx_('{L}евро\\s*-?\\s*(\\d){R}'), function (m) { return 'евро' + m[1]; });
  take_(ctx, rx_('{L}евро\\s*-?\\s*(одн|двуш|дву|треш|тре)' + W_), function (m) { return 'евро' + ({ 'одн': 1, 'двуш': 2, 'дву': 2, 'треш': 3, 'тре': 3 })[m[1]]; });
  take_(ctx, rx_('{L}(?:однушк|однокомнатн|однокомн|одн[оа]к)' + W_), function () { return '1к'; });
  take_(ctx, rx_('{L}(?:двушк|двушечк|двухкомнатн|двухкомн|двух\\s*комнатн|двуха)' + W_), function () { return '2к'; });
  take_(ctx, rx_('{L}(?:трешк|трешечк|трехкомнатн|трехкомн|трех\\s*комнатн|треха)' + W_), function () { return '3к'; });
  take_(ctx, rx_('{L}(?:четырехкомнатн|четырехкомн|четырешк)' + W_), function () { return '4к'; });
  take_(ctx, rx_('{L}([1-6])(?:\\s*(?:-|–|,|/|или)\\s*([1-6]))?\\s*-?\\s*(?:х\\s*-?\\s*)?(?:кк|к|кв|ккв|комн' + W_ + '|ком){R}'),
    function (m) { return m[2] ? m[1] + '-' + m[2] + 'к' : m[1] + 'к'; });
  take_(ctx, rx_('{L}студи' + W_), function () { return 'студия'; });

  // 5. ЖК: «ЖК Панорама», «жк «Самолёт 2»»
  take_(ctx, rx_('{L}(?:жк|жилой комплекс|жилом комплексе|жилого комплекса)\\s+([а-яёa-z0-9][а-яёa-z0-9-]*(?:\\s+[а-яёa-z0-9-]+){0,3})'),
    function (m) {
      var words = m[1].split(/\s+/), take = [];
      for (var i = 0; i < words.length; i++) {
        var w = words[i];
        if (CX_STOP_.test(w) || /^\d+[а-я]+$/.test(w) || /^\d{3,}$/.test(w) || isDistrict_(w)) break;
        take.push(w);
      }
      if (!take.length) return null;
      var head = m[0].slice(0, m[0].length - m[1].length);
      return { kw: 'жк ' + take.join(' '), len: head.length + take.join(' ').length };
    });

  // 6. Бюджет: «до 7 млн», «7-9 млн», «от 5 до 7 млн», «до 7000000», «бюджет 40 тыс»
  var budgetDone = false;
  take_(ctx, rx_('{L}от\\s*' + NUM_ + '\\s*' + UNIT_ + '?\\s*до\\s*' + NUM_ + '\\s*' + UNIT_ + '?' + NOT_MONEY_), function (m) {
    if (budgetDone) return null;
    var u2 = m[4] || m[2];
    var a = toRub_(num_(m[1]), m[2] || u2, rent), b = toRub_(num_(m[3]), u2, rent);
    budgetDone = true;
    return fmtRange_(a, b);
  });
  take_(ctx, rx_('{L}(?:(до|от|за|бюджет' + W_ + '|не\\s*дороже|не\\s*более|в\\s*пределах|около|примерно|в\\s*районе|макс' + W_ + '\\.?|max)\\s*:?\\s*)?' +
      NUM_ + '(?:\\s*(?:-|–|—)\\s*' + NUM_ + ')?\\s*' + UNIT_ + '?' + NOT_MONEY_), function (m) {
    if (budgetDone) return null;
    var pre = (m[1] || '').replace(/\s+/g, ' '), a = num_(m[2]), b = m[3] ? num_(m[3]) : null, unit = m[4] || '';
    if (!pre && !unit && !b && a < 100000) return null;       // просто число (номер дома и т. п.)
    if (unit === 'м' && a >= 1000) return null;
    budgetDone = true;
    var ra = toRub_(a, unit, rent);
    if (b) return fmtRange_(toRub_(a, unit, rent), toRub_(b, unit, rent));
    if (pre === 'от') return 'от ' + fmtRub_(ra);
    if (/^(около|примерно|в районе)$/.test(pre)) return 'около ' + fmtRub_(ra);
    return 'до ' + fmtRub_(ra);
  });

  // 7. Улица
  take_(ctx, rx_('{L}(?:ул\\.?|улиц' + W_ + ')\\s*([а-яё][а-яё-]{2,})'), function (m) {
    return CX_STOP_.test(m[1]) ? null : 'ул. ' + m[1];
  });

  // 8. Районы
  DISTRICTS_.forEach(function (d) { take_(ctx, rx_('{L}(?:' + d[1] + '){R}'), function () { return d[0]; }); });

  // 9. Тип объекта и прочее
  TYPES_.concat(MISC_).forEach(function (t) { take_(ctx, rx_(t[0]), function () { return t[1]; }); });

  // Порядок — как в тексте, без повторов
  ctx.tokens.sort(function (a, b) { return a.pos - b.pos; });
  var keys = [];
  ctx.tokens.forEach(function (t) { if (keys.indexOf(t.kw) < 0) keys.push(t.kw); });

  // Строка поиска: без лишнего («2к квартира» → «2к», «коммерция помещение» → «коммерция»,
  // «2к … в новом доме» — «дом» тут не тип объекта)
  var hasRooms = keys.some(function (k) { return k === 'студия' || /^\d(-\d)?к$/.test(k) || /^евро\d$/.test(k); });
  var hasCommerce = keys.indexOf('коммерция') >= 0;
  var search = keys.filter(function (k) {
    if ((k === 'квартира' || k === 'дом') && hasRooms) return false;
    if (k === 'помещение' && hasCommerce) return false;
    return true;
  }).join(' ');
  return { keys: keys, search: search };
}

/** Находит совпадения re в ctx.s, кладёт ключевые слова и «закрашивает» найденное пробелами. */
function take_(ctx, re, fn) {
  re.lastIndex = 0;
  var m;
  while ((m = re.exec(ctx.s)) !== null) {
    if (!m[0].length) { re.lastIndex++; continue; }
    var out = fn(m);
    if (out === null || out === undefined || out === false) continue;
    var len = m[0].length, list;
    if (typeof out === 'object' && !Array.isArray(out)) { list = [out.kw]; len = out.len; }
    else list = Array.isArray(out) ? out : [out];
    list.forEach(function (kw, i) { ctx.tokens.push({ pos: m.index + i * 0.01, kw: kw }); });
    ctx.s = ctx.s.slice(0, m.index) + new Array(len + 1).join(' ') + ctx.s.slice(m.index + len);
  }
}

var DISTRICT_TEST_ = null;
function isDistrict_(w) {
  if (!DISTRICT_TEST_) DISTRICT_TEST_ = new RegExp('^(?:' + DISTRICTS_.map(function (d) { return d[1]; }).join('|') + ')$');
  return DISTRICT_TEST_.test(w);
}

function num_(s) { return parseFloat(String(s).replace(/[\s ]/g, '').replace(',', '.')); }
function fmt_(n) { return String(Math.round(n * 100) / 100).replace('.', ','); }

function toRub_(n, unit, rent) {
  var u = String(unit || '');
  if (/^(млн|миллион|лям|мл)/.test(u) || u === 'м') return n * 1e6;
  if (/^т/.test(u) || u === 'к') return n * 1e3;
  if (/^(руб|р|₽)/.test(u)) return n;
  if (rent) return n < 1000 ? n * 1e3 : n;                    // аренда: «до 40» = 40 тыс
  if (n < 100) return n * 1e6;                                // «до 7» = 7 млн
  if (n < 100000) return n * 1e3;                             // «до 7000» = 7 млн (цена в тысячах)
  return n;
}

function fmtRub_(v) {
  if (v >= 1e6) return fmt_(v / 1e6) + ' млн';
  if (v >= 1000) return fmt_(v / 1000) + ' тыс';
  return fmt_(v) + ' ₽';
}

function fmtRange_(a, b) {
  if (a >= 1e6 && b >= 1e6) return fmt_(a / 1e6) + '-' + fmt_(b / 1e6) + ' млн';
  if (a < 1e6 && b < 1e6 && a >= 1000 && b >= 1000) return fmt_(a / 1000) + '-' + fmt_(b / 1000) + ' тыс';
  return fmtRub_(a) + '-' + fmtRub_(b);
}

// ═══════════════════════════ НОВЫЕ ЗАПРОСЫ С САЙТА ═══════════════════════════
// Сайт 1+1 отдаёт новые запросы покупателей из чатов: /api/export/requests?after=<последний id>.
// Только с номером, без номеров тех, кто уже пользуется 1+1, и тех, кто просил скрыть номер.
// Ключ (EXPORT_KEY из .env на сервере) хранится в свойствах скрипта, а не на листе.

var PROP_KEY = 'EXPORT_KEY';
var PROP_CURSOR = 'REQ_CURSOR';
var IMPORT_PAGES = 10;      // за один запуск — не больше 10 × 200 запросов

/** Меню «Ключ сайта…»: ввести/заменить ключ выгрузки. */
function setExportKey() {
  var ui = SpreadsheetApp.getUi();
  var props = PropertiesService.getScriptProperties();
  var r = ui.prompt('Ключ сайта',
    'Вставьте EXPORT_KEY из файла .env на сервере (тот же самый).' +
    (props.getProperty(PROP_KEY) ? '\nКлюч уже сохранён — новый заменит его.' : ''), ui.ButtonSet.OK_CANCEL);
  if (r.getSelectedButton() !== ui.Button.OK) return;
  var key = r.getResponseText().trim();
  if (key.length < 20) { ui.alert('Ключ слишком короткий — нужен тот же, что в .env (20+ знаков).'); return; }
  props.setProperty(PROP_KEY, key);
  ui.alert('Ключ сохранён. Теперь «1+1 → Забрать новые запросы с сайта» или включите автозабор.');
}

/** Забрать новые запросы, добавить строками внизу листа и сразу подготовить сообщения. */
function importRequests() {
  var lock = getLock_();
  if (lock && !lock.tryLock(30000)) { logError_('', 'забор с сайта', 'Таблица занята — попробую в следующий раз'); return; }
  try {
    var res = importRequests_();
    toast_(res.text);
    return res;
  } catch (err) {
    logError_('', 'забор с сайта', err);
    toast_('Не удалось забрать запросы — см. лист «Лог»');
  } finally {
    if (lock) lock.releaseLock();
  }
}

function importRequests_() {
  var cfg = readSettings_();
  var props = PropertiesService.getScriptProperties();
  var key = props.getProperty(PROP_KEY);
  if (!key) throw new Error('Нет ключа сайта: меню «1+1 → Ключ сайта…»');
  var firstRun = props.getProperty(PROP_CURSOR) === null;
  var cursor = Number(props.getProperty(PROP_CURSOR) || 0);
  var minTs = firstRun && cfg.firstDays ? Math.floor(Date.now() / 1000) - cfg.firstDays * 86400 : 0;

  var sh = mainSheet_();
  var last = sh.getLastRow();
  var known = {};   // номера, которые уже есть в таблице — второй раз не добавляем
  if (last >= 2) sh.getRange(2, COL.PHONE, last - 1, 1).getValues().forEach(function (r) {
    var p = canonPhone_(r[0]); if (p) known[p] = true;
  });

  var add = [], skippedKnown = 0, skippedOld = 0;
  for (var page = 0; page < IMPORT_PAGES; page++) {
    var resp = UrlFetchApp.fetch(cfg.exportUrl + '?after=' + cursor + '&limit=200', {
      headers: { 'X-Export-Key': key }, muteHttpExceptions: true, followRedirects: true
    });
    var code = resp.getResponseCode();
    if (code === 403) throw new Error('Сайт не принял ключ (403): проверьте «1+1 → Ключ сайта…» и EXPORT_KEY в .env');
    if (code === 404) throw new Error('Выгрузка на сайте выключена (404): на сервере в .env не задан EXPORT_KEY (20+ знаков) или сайт не обновлён');
    if (code !== 200) throw new Error('Сайт ответил ' + code + ': ' + resp.getContentText().slice(0, 200));
    var d = JSON.parse(resp.getContentText());
    (d.items || []).forEach(function (it) {
      var p = canonPhone_(it.phone);
      if (!p || known[p]) { skippedKnown++; return; }
      if (minTs && it.ts && it.ts < minTs) { skippedOld++; return; }
      known[p] = true;
      add.push([it.name || '', it.phone, it.source || '', it.text || '', '', '', '', '', '', '', 'с сайта, запрос №' + it.id]);
    });
    cursor = Math.max(cursor, Number(d.last_id) || cursor);
    if (!d.more) break;
  }

  var newRows = [];
  if (add.length) {
    var start = sh.getLastRow() + 1;
    sh.getRange(start, COL.PHONE, add.length, 1).setNumberFormat('@');   // «+7…» — текстом, а не числом
    sh.getRange(start, 1, add.length, NCOL).setValues(add);
    for (var i = 0; i < add.length; i++) newRows.push(start + i);
  }
  props.setProperty(PROP_CURSOR, String(cursor));   // курсор — только после записи строк
  var gen = newRows.length ? processRows_(sh, newRows, 'new', cfg) : { done: 0, repeat: 0, errors: 0 };

  var text = add.length
    ? 'С сайта: новых запросов ' + add.length + ' · сообщений готово ' + gen.done +
      (gen.repeat ? ' · повтор номера ' + gen.repeat : '') + (gen.errors ? ' · ошибок ' + gen.errors : '')
    : 'Новых запросов с сайта нет';
  if (skippedKnown) text += ' · уже были в таблице: ' + skippedKnown;
  if (skippedOld) text += ' · старше ' + cfg.firstDays + ' дн. (первый забор): ' + skippedOld;
  return { added: add.length, done: gen.done, text: text };
}

/** Автозабор: каждые 15 минут (триггер по времени). */
function enableAutoImport() {
  var ui = SpreadsheetApp.getUi();
  if (!PropertiesService.getScriptProperties().getProperty(PROP_KEY)) {
    ui.alert('Сначала введите ключ: «1+1 → Ключ сайта…»'); return;
  }
  removeImportTriggers_();
  ScriptApp.newTrigger('importRequests').timeBased().everyMinutes(15).create();
  var res = importRequests();
  ui.alert('Автозабор включён: каждые 15 минут таблица сама забирает новые запросы и готовит сообщения.\n\n' +
    (res ? res.text : 'Первый забор не удался — см. лист «Лог».'));
}

function disableAutoImport() {
  var n = removeImportTriggers_();
  SpreadsheetApp.getUi().alert(n ? 'Автозабор выключен.' : 'Автозабор и так был выключен.');
}

function removeImportTriggers_() {
  var n = 0;
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'importRequests') { ScriptApp.deleteTrigger(t); n++; }
  });
  return n;
}

// ═══════════════════════════ ПАНЕЛЬ «ОТПРАВКА ПО ОЧЕРЕДИ» ═══════════════════════════

function showSender() {
  var html = HtmlService.createHtmlOutput(SENDER_HTML_).setTitle('1+1 · отправка по очереди');
  SpreadsheetApp.getUi().showSidebar(html);
}

/** Следующая строка «Готово» начиная с fromRow (по кругу). */
function senderData(fromRow) {
  var sh = mainSheet_();
  var last = sh.getLastRow();
  if (last < 2) return { done: true, total: 0 };
  var vals = sh.getRange(2, 1, last - 1, NCOL).getValues();
  var ready = [];
  vals.forEach(function (v, i) {
    if (statusOf_(v[COL.STATUS - 1]) === ST.READY && String(v[COL.MSG - 1]).trim()) ready.push(i + 2);
  });
  if (!ready.length) return { done: true, total: 0 };
  var start = Math.max(2, Number(fromRow) || 2);
  var row = ready.filter(function (r) { return r >= start; })[0] || ready[0];
  var v = vals[row - 2];
  var digits = canonPhone_(v[COL.PHONE - 1]);
  var text = String(v[COL.MSG - 1]);
  sh.getRange(row, 1, 1, NCOL).activate();   // строка подсвечивается в таблице
  return {
    done: false, total: ready.length, pos: ready.indexOf(row) + 1, row: row,
    name: String(v[COL.NAME - 1]), phone: String(v[COL.PHONE - 1]), source: String(v[COL.SOURCE - 1]),
    query: String(v[COL.QUERY - 1]), text: text, len: len_(text),
    wa: digits ? 'https://wa.me/' + digits + '?text=' + encodeURIComponent(text) : '',
    tg: digits ? 'https://t.me/+' + digits : ''
  };
}

/** Отметить строку (только если она всё ещё «Готово») и перейти к следующей. */
function senderMark(row, status) {
  if (STATUS_LIST.indexOf(status) < 0) throw new Error('Неизвестный статус');
  return withLock_(function () {
    var sh = mainSheet_();
    if (statusOf_(sh.getRange(row, COL.STATUS).getValue()) === ST.READY) {
      sh.getRange(row, COL.STATUS, 1, 2).setValues([[status, new Date()]]);
      sh.getRange(row, COL.DATE).setNumberFormat('dd.MM.yyyy HH:mm');
    }
    return senderData(row + 1);
  });
}

function senderSkip(row) { return senderData(Number(row) + 1); }

function senderRegen(row) {
  return withLock_(function () {
    processRows_(mainSheet_(), [Number(row)], 'force', readSettings_());
    return senderData(row);
  });
}

var SENDER_HTML_ = [
  '<!doctype html><html><head><base target="_blank"><meta charset="utf-8"><style>',
  'body{font:13px/1.45 Arial,sans-serif;margin:12px;color:#1d1d1f}',
  'h3{margin:0 0 4px;font-size:15px}.muted{color:#6e6e73;font-size:12px}',
  'textarea{width:100%;box-sizing:border-box;height:300px;font:13px/1.45 Arial;padding:8px;border:1px solid #ccc;border-radius:8px}',
  '.btn{display:block;width:100%;box-sizing:border-box;margin:6px 0;padding:9px;border-radius:8px;border:1px solid #035352;',
  'background:#fff;color:#035352;font-weight:bold;text-align:center;text-decoration:none;cursor:pointer;font-size:13px}',
  '.main{background:#035352;color:#F3E8BC}.row{display:flex;gap:6px}.row .btn{flex:1}',
  '.ok{color:#137333;font-weight:bold;min-height:18px}.err{color:#c5221f}',
  '</style></head><body>',
  '<div id="box">Загрузка…</div>',
  '<script>',
  'var cur=null;',
  'function esc(s){return String(s||"").replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;"}[c];});}',
  'function show(d){cur=d;var b=document.getElementById("box");',
  ' if(d.done){b.innerHTML="<h3>Очередь пуста</h3><p class=muted>Нет строк со статусом «Готово». Сгенерируйте сообщения через меню «1+1».</p><button class=btn onclick=load(2)>Обновить</button>";return;}',
  ' b.innerHTML="<h3>"+esc(d.name)+"</h3>"+',
  '  "<div class=muted>Строка "+d.row+" · "+d.pos+" из "+d.total+" готовых<br>"+esc(d.phone)+" · "+esc(d.source)+"</div>"+',
  '  "<p class=muted>Запрос: "+esc(d.query)+"</p>"+',
  '  "<textarea id=t readonly>"+esc(d.text)+"</textarea><div class=muted>"+d.len+" символов</div>"+',
  '  "<div id=msg class=ok></div>"+',
  '  "<button class=\\"btn main\\" onclick=copy()>Скопировать текст</button>"+',
  '  (d.wa?"<a class=btn href=\\""+d.wa+"\\" onclick=copy()>Открыть WhatsApp с текстом</a>":"")+',
  '  (d.tg?"<a class=btn href=\\""+d.tg+"\\" onclick=copy()>Скопировать и открыть Telegram</a>":"")+',
  '  "<div class=muted>MAX: текст скопирован — найдите человека по номеру и вставьте.</div>"+',
  '  "<button class=\\"btn main\\" onclick=mark(\'Отправлено\')>Отправлено → следующий</button>"+',
  '  "<div class=row><button class=btn onclick=skip()>Пропустить</button><button class=btn onclick=mark(\'Отказ\')>Отказ</button></div>"+',
  '  "<button class=btn onclick=regen()>Другой вариант текста</button>";',
  '}',
  'function copy(){var t=document.getElementById("t");t.focus();t.select();var ok=false;',
  ' try{ok=document.execCommand("copy");}catch(e){}',
  ' if(navigator.clipboard){navigator.clipboard.writeText(t.value).catch(function(){});ok=true;}',
  ' document.getElementById("msg").textContent=ok?"Скопировано":"Выделено — нажмите Ctrl+C";}',
  'function fail(e){document.getElementById("box").innerHTML="<p class=err>"+esc(e&&e.message||e)+"</p><button class=btn onclick=load(2)>Ещё раз</button>";}',
  'function load(r){google.script.run.withSuccessHandler(show).withFailureHandler(fail).senderData(r);}',
  'function mark(s){if(!cur)return;google.script.run.withSuccessHandler(show).withFailureHandler(fail).senderMark(cur.row,s);}',
  'function skip(){if(!cur)return;google.script.run.withSuccessHandler(show).withFailureHandler(fail).senderSkip(cur.row);}',
  'function regen(){if(!cur)return;document.getElementById("msg").textContent="Готовлю…";google.script.run.withSuccessHandler(show).withFailureHandler(fail).senderRegen(cur.row);}',
  'load(2);',
  '</script></body></html>'
].join('\n');

// ═══════════════════════════ ЛИСТЫ, НАСТРОЙКИ, ЛОГ ═══════════════════════════

function ensureSheets_() {
  var ss = SpreadsheetApp.getActive();
  var sh = ss.getSheetByName(SHEET_MAIN);
  if (!sh) {
    var first = ss.getSheets()[0];
    // Пустой первый лист переименовываем, иначе создаём новый
    if (first && first.getLastRow() === 0 && ss.getSheets().length === 1) { first.setName(SHEET_MAIN); sh = first; }
    else sh = ss.insertSheet(SHEET_MAIN, 0);
  }
  if (String(sh.getRange(1, 1).getValue()).trim() === '') setupSheet();
  if (!ss.getSheetByName(SHEET_SETTINGS)) setupSettings();
  if (!ss.getSheetByName(SHEET_LOG)) setupLog();
}

/** Шапка, ширина колонок, выпадающий список статусов, цвета статусов. */
function setupSheet() {
  var ss = SpreadsheetApp.getActive();
  var sh = ss.getSheetByName(SHEET_MAIN) || ss.insertSheet(SHEET_MAIN, 0);
  sh.getRange(1, 1, 1, NCOL).setValues([HEADERS]).setFontWeight('bold').setBackground('#035352').setFontColor('#F3E8BC');
  sh.setFrozenRows(1);
  var widths = [120, 130, 170, 260, 200, 180, 420, 80, 120, 120, 200];
  widths.forEach(function (w, i) { sh.setColumnWidth(i + 1, w); });
  var maxR = Math.max(sh.getMaxRows(), 1000);
  if (sh.getMaxRows() < maxR) sh.insertRowsAfter(sh.getMaxRows(), maxR - sh.getMaxRows());
  sh.getRange(2, COL.MSG, maxR - 1, 1).setWrap(true).setVerticalAlignment('top');
  sh.getRange(2, COL.QUERY, maxR - 1, 1).setWrap(true).setVerticalAlignment('top');
  sh.getRange(2, COL.DATE, maxR - 1, 1).setNumberFormat('dd.MM.yyyy HH:mm');

  var statusRange = sh.getRange(2, COL.STATUS, maxR - 1, 1);
  statusRange.setDataValidation(SpreadsheetApp.newDataValidation()
    .requireValueInList(STATUS_LIST, true).setAllowInvalid(true).build());

  var colors = {};
  colors[ST.READY] = '#fff4c2'; colors[ST.SENT] = '#d2e3fc'; colors[ST.ANSWERED] = '#ceead6';
  colors[ST.REFUSED] = '#f6d0cd'; colors[ST.REPEAT] = '#e8eaed';
  var rules = sh.getConditionalFormatRules().filter(function (r) {
    return !r.getRanges().some(function (g) { return g.getColumn() === COL.STATUS; });
  });
  Object.keys(colors).forEach(function (st) {
    rules.push(SpreadsheetApp.newConditionalFormatRule().whenTextEqualTo(st)
      .setBackground(colors[st]).setRanges([statusRange]).build());
  });
  sh.setConditionalFormatRules(rules);
}

/** Лист «Настройки»: создаёт или дописывает недостающие параметры (свои значения не трогает). */
function setupSettings() {
  var ss = SpreadsheetApp.getActive();
  var sh = ss.getSheetByName(SHEET_SETTINGS);
  if (!sh) {
    sh = ss.insertSheet(SHEET_SETTINGS);
    sh.getRange(1, 1, 1, 3).setValues([['Параметр', 'Значение', 'Пояснение']])
      .setFontWeight('bold').setBackground('#035352').setFontColor('#F3E8BC');
    sh.setFrozenRows(1);
    sh.setColumnWidth(1, 220); sh.setColumnWidth(2, 260); sh.setColumnWidth(3, 560);
  }
  var last = sh.getLastRow();
  var have = last > 1 ? sh.getRange(2, 1, last - 1, 1).getValues().map(function (r) { return String(r[0]).trim().toLowerCase(); }) : [];
  SETTINGS_DEFAULTS.forEach(function (d) {
    if (have.indexOf(d[0].toLowerCase()) >= 0) return;
    sh.appendRow(d);
    var r = sh.getLastRow();
    if (d[1] === 'вкл' || d[1] === 'выкл') {
      sh.getRange(r, 2).setDataValidation(SpreadsheetApp.newDataValidation().requireValueInList(['вкл', 'выкл'], true).build());
    }
  });
  sh.getRange(2, 3, Math.max(sh.getLastRow() - 1, 1), 1).setWrap(true);
  return sh;
}

function setupLog() {
  var ss = SpreadsheetApp.getActive();
  var sh = ss.getSheetByName(SHEET_LOG);
  if (!sh) {
    sh = ss.insertSheet(SHEET_LOG);
    sh.getRange(1, 1, 1, 4).setValues([['Дата', 'Строка', 'Тип ошибки', 'Текст']]).setFontWeight('bold');
    sh.setFrozenRows(1);
    sh.setColumnWidth(1, 140); sh.setColumnWidth(4, 600);
  }
  return sh;
}

/** Ошибка → лист «Лог» (дата, строка, тип, текст). Сам никогда не падает. */
function logError_(row, type, err) {
  try {
    var sh = setupLog();
    var text = err && err.stack ? String(err.message || err) + ' | ' + String(err.stack).split('\n').slice(0, 3).join(' ') : String(err);
    sh.appendRow([new Date(), row, type, text]);
    var extra = sh.getLastRow() - 1 - LOG_MAX_ROWS;
    if (extra > 0) sh.deleteRows(2, extra);
  } catch (e) {
    console.error('Не удалось записать в Лог: ' + e + ' | исходная ошибка: ' + err);
  }
}
function logError(row, type, err) { logError_(row, type, err); }   // имя из ТЗ

/** Настройки с листа «Настройки» (если параметра нет — значение по умолчанию). */
function readSettings_() {
  var map = {};
  SETTINGS_DEFAULTS.forEach(function (d) { map[d[0].toLowerCase()] = d[1]; });
  var sh = SpreadsheetApp.getActive().getSheetByName(SHEET_SETTINGS);
  if (sh && sh.getLastRow() > 1) {
    sh.getRange(2, 1, sh.getLastRow() - 1, 2).getValues().forEach(function (r) {
      var k = String(r[0]).trim().toLowerCase();
      if (k && String(r[1]).trim() !== '') map[k] = r[1];
    });
  }
  var limit = parseInt(String(map['лимит символов']).replace(/\D/g, ''), 10);
  return {
    autogen: isOn_(map['автогенерация']),
    limit: limit >= 200 ? limit : 900,
    link: String(map['ссылка']).trim(),
    price: String(map['цена']).trim(),
    trial: String(map['пробный период']).trim(),
    objects: String(map['объектов в базе']).trim(),
    example: String(map['пример поиска']).trim(),
    searchLink: isOn_(map['ссылка на поиск по запросу']),
    checkRepeat: isOn_(map['проверять повтор номера']),
    exportUrl: String(map['адрес выгрузки запросов']).trim(),
    firstDays: Math.max(0, parseInt(String(map['первый забор: запросы за дней']).replace(/\D/g, ''), 10) || 0)
  };
}

function isOn_(v) { return /^(вкл|да|on|true|1|включ)/i.test(String(v).trim()); }

// ═══════════════════════════ МЕЛОЧИ ═══════════════════════════

function mainSheet_() {
  ensureSheets_();
  return SpreadsheetApp.getActive().getSheetByName(SHEET_MAIN);
}

/** Статус: «Готово»/«Отправлено»/… в каноническом написании, любой другой — как есть, пусто — ''. */
function statusOf_(v) {
  var s = String(v == null ? '' : v).trim();
  if (!s) return '';
  var low = s.toLowerCase();
  for (var i = 0; i < STATUS_LIST.length; i++) if (STATUS_LIST[i].toLowerCase() === low) return STATUS_LIST[i];
  return s;
}

/** Номер в виде 7XXXXXXXXXX (для проверки повторов и ссылок). Пусто — ''. */
function canonPhone_(v) {
  var d = String(v == null ? '' : v).replace(/\D/g, '');
  if (d.length === 11 && d.charAt(0) === '8') d = '7' + d.slice(1);
  if (d.length === 10 && d.charAt(0) === '9') d = '7' + d;
  return d.length >= 10 ? d : '';
}

/** Убирает из комментария пометки, которые ставит скрипт (ручной текст остаётся). */
function cleanAutoNote_(note) {
  return String(note || '').split(/\s*;\s*/).filter(function (x) {
    return x && !/^обрезано до \d+/.test(x) && !/^этому номеру уже писали/.test(x) && x !== 'без имени';
  }).join('; ');
}
function joinNote_(note, add) { return note ? note + '; ' + add : add; }

function selectedRows_() {
  var ss = SpreadsheetApp.getActive();
  var sh = ss.getActiveSheet();
  if (sh.getName() !== SHEET_MAIN) {
    SpreadsheetApp.getUi().alert('Перейдите на лист «' + SHEET_MAIN + '» и выделите строку (или несколько).');
    return null;
  }
  var rows = [];
  var list = sh.getActiveRangeList();
  var ranges = list ? list.getRanges() : [sh.getActiveRange()];
  ranges.forEach(function (rg) {
    for (var r = rg.getRow(); r <= rg.getLastRow(); r++) if (r >= 2 && rows.indexOf(r) < 0) rows.push(r);
  });
  if (!rows.length) { SpreadsheetApp.getUi().alert('Выделите строку с данными (не шапку).'); return null; }
  return rows.sort(function (a, b) { return a - b; });
}

function getLock_() {
  try { return LockService.getDocumentLock(); } catch (e) { return null; }
}

/** Выполнить под блокировкой (кнопки и автогенерация не мешают друг другу). */
function withLock_(fn) {
  var lock = getLock_();
  if (lock && !lock.tryLock(20000)) {
    SpreadsheetApp.getUi().alert('Таблица занята другой операцией скрипта. Подождите несколько секунд и повторите.');
    return null;
  }
  try { return fn(); }
  catch (err) { logError_('', 'операция', err); throw err; }
  finally { if (lock) lock.releaseLock(); }
}

function summary_(res, emptyText) {
  if (!res.done && !res.repeat && !res.errors) return emptyText;
  var parts = ['Готово: ' + res.done];
  if (res.repeat) parts.push('повтор номера: ' + res.repeat);
  if (res.errors) parts.push('ошибок: ' + res.errors + ' (см. лист «Лог»)');
  return parts.join(' · ');
}

function explain_(res) {
  var text = summary_(res, 'Ничего не изменено');
  if (res.reasons.length) {
    SpreadsheetApp.getUi().alert(text + '\n\n' + res.reasons.slice(0, 15).join('\n'));
  } else {
    toast_(text);
  }
}

function toast_(text) {
  try { SpreadsheetApp.getActive().toast(text, '1+1', 5); } catch (e) { console.log(text); }   // по расписанию экрана нет
}
