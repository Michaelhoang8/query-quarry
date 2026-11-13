const $ = (id) => document.getElementById(id),
  el = (tag, text) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    return node;
  };
let catalog = null,
  result = null,
  revision = 0,
  importRevision = 0,
  controller = null;
function lineNumbers() {
  $('line-numbers').textContent = $('sql')
    .value.split('\n')
    .map((_, index) => index + 1)
    .join('\n');
  $('line-numbers').scrollTop = $('sql').scrollTop;
}
$('sql').addEventListener('scroll', () => {
  $('line-numbers').scrollTop = $('sql').scrollTop;
});
$('sql').addEventListener('input', lineNumbers);
for (const button of document.querySelectorAll('[data-output]'))
  button.addEventListener('click', () => {
    for (const view of ['plans', 'rows']) {
      $(view + '-view').hidden = view !== button.dataset.output;
      document
        .querySelector(`[data-output="${view}"]`)
        .setAttribute('aria-pressed', String(view === button.dataset.output));
    }
  });
async function api(path, data, signal) {
  const response = await fetch(path, {
    method: data ? 'POST' : 'GET',
    headers: data ? { 'Content-Type': 'application/json' } : undefined,
    body: data ? JSON.stringify(data) : undefined,
    signal,
  });
  const body = await response.json();
  if (!response.ok) throw Error(body.error || 'Request failed.');
  return body;
}
function validate(data) {
  if (
    !data ||
    typeof data !== 'object' ||
    Array.isArray(data) ||
    Object.keys(data).length !== 3 ||
    !['sql', 'parameters', 'indexes'].every((k) => Object.hasOwn(data, k))
  )
    throw Error('Provide sql, parameters, and indexes only.');
  if (
    typeof data.sql !== 'string' ||
    !data.sql.trim() ||
    data.sql.length > 4000 ||
    data.sql.includes('\0')
  )
    throw Error('Use 1–4,000 SQL characters without NUL.');
  if (
    !data.parameters ||
    typeof data.parameters !== 'object' ||
    Array.isArray(data.parameters) ||
    Object.keys(data.parameters).length > 20
  )
    throw Error('Parameters must be a JSON object with at most 20 entries.');
  for (const [key, value] of Object.entries(data.parameters)) {
    if (!/^[A-Za-z_][A-Za-z0-9_]{0,31}$/.test(key))
      throw Error('Use simple parameter names without a colon.');
    if (value === null || (typeof value === 'string' && value.length <= 200)) continue;
    if (
      typeof value === 'number' &&
      Number.isFinite(value) &&
      (!Number.isInteger(value) || Number.isSafeInteger(value))
    )
      continue;
    throw Error(
      'Use null, short strings, or finite numbers; integers must be exactly representable in JavaScript.',
    );
  }
  if (
    !Array.isArray(data.indexes) ||
    data.indexes.some((k) => typeof k !== 'string' || !Object.hasOwn(catalog.indexes, k)) ||
    new Set(data.indexes).size !== data.indexes.length
  )
    throw Error('Choose unique known indexes.');
  return data;
}
function read() {
  return validate({
    sql: $('sql').value,
    parameters: JSON.parse($('parameters').value),
    indexes: [...$('indexes').querySelectorAll('input:checked')].map((input) => input.value),
  });
}
function invalidate(message = 'Experiment changed. Run the comparison again.') {
  revision++;
  controller?.abort();
  controller = null;
  result = null;
  $('results').hidden = true;
  $('status').textContent = message;
  $('run').disabled = false;
}
function setStudy(data) {
  $('sql').value = data.sql;
  lineNumbers();
  $('parameters').value = JSON.stringify(data.parameters, null, 2);
  for (const input of $('indexes').querySelectorAll('input'))
    input.checked = data.indexes.includes(input.value);
  invalidate();
}
for (const id of ['sql', 'parameters'])
  $(id).oninput = () => {
    importRevision++;
    invalidate();
  };
$('indexes').onchange = () => {
  importRevision++;
  invalidate();
};
$('sql').onkeydown = (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
    event.preventDefault();
    run();
  }
};
$('example').onchange = () => {
  if (!catalog) return;
  importRevision++;
  setStudy(catalog.examples[$('example').value]);
  run();
};
function planCard(data, label) {
  const card = el('article');
  card.className = 'plan';
  card.append(el('h3', label));
  if (data.error) {
    const message = el('p', data.error);
    message.className = 'error';
    card.append(message);
    return card;
  }
  const numbers = el('div');
  numbers.className = 'numbers';
  for (const [value, caption] of [
    [data.median_ms.toFixed(3) + ' ms', 'MEDIAN PREVIEW TIME'],
    [
      data.vm_steps_estimate ? data.vm_steps_estimate.toLocaleString() : '< 1,000',
      'ESTIMATED VM INSTRUCTIONS',
    ],
  ]) {
    const item = el('div');
    item.append(el('strong', value), el('span', caption));
    numbers.append(item);
  }
  card.append(numbers);
  const lines = el('div');
  lines.className = 'plan-lines';
  for (const node of data.plan) {
    const line = el('div', node.detail);
    line.className = 'plan-line';
    const parent = data.plan.find((n) => n.id === node.parent);
    if (parent) line.prepend('↳ ');
    lines.append(line);
  }
  if (!data.plan.length) lines.append(el('p', 'No scan/search nodes for this statement.'));
  card.append(lines);
  const note = el(
    'p',
    `Index build: ${data.build_ms.toFixed(3)} ms · allocated index pages: ${(data.allocated_index_bytes / 1024).toFixed(1)} KiB`,
  );
  note.className = 'hint';
  card.append(note);
  return card;
}
function preview() {
  if (!result) return;
  const data = result[$('variant').value];
  $('table').replaceChildren();
  $('csv').disabled = Boolean(data.error);
  if (data.error) {
    $('row-status').textContent = 'This variant did not produce a result preview.';
    return;
  }
  $('row-status').textContent =
    `${data.rows.length} displayed rows${data.truncated ? ' · more rows exist; preview capped at 100' : ' · complete result'}${data.clipped_cells ? ` · ${data.clipped_cells} long cells clipped` : ''}. Blob values display as hexadecimal strings.`;
  const thead = el('thead'),
    heading = el('tr');
  for (const name of data.columns) heading.append(el('th', name));
  thead.append(heading);
  const tbody = el('tbody');
  for (const values of data.rows) {
    const row = el('tr');
    for (const value of values) row.append(el('td', value === null ? 'NULL' : String(value)));
    tbody.append(row);
  }
  $('table').append(thead, tbody);
}
function display(data) {
  result = data;
  $('results').hidden = false;
  $('plans').replaceChildren(
    planCard(data.baseline, '01 / BASELINE'),
    planCard(data.indexed, '02 / SELECTED INDEXES'),
  );
  const comparison = data.comparison;
  if (comparison.ordered_preview_equal === null)
    $('equality').textContent = 'A variant failed; result equality was not checked.';
  else if (comparison.complete_unclipped_results)
    $('equality').textContent = comparison.ordered_preview_equal
      ? 'Both completed runs returned identical rows in the same order.'
      : comparison.unordered_preview_equal
        ? 'Both completed runs returned the same row multiset, in a different order. Use ORDER BY when order matters.'
        : 'The completed result previews differ. Check ordering, expressions, and time-dependent functions.';
  else
    $('equality').textContent =
      `${comparison.ordered_preview_equal ? 'Displayed previews match.' : 'Displayed previews differ.'} One or both previews are truncated or clipped; full-result equality is not established.`;
  $('status').textContent =
    data.baseline.error || data.indexed.error
      ? 'Experiment finished with a query error. See the variant details.'
      : `Comparison complete · SQLite ${data.sqlite_version} · one warm-up and three measured runs per variant.`;
  preview();
}
async function run() {
  if (!catalog) return;
  let data;
  try {
    data = read();
  } catch (error) {
    $('status').textContent = error.message;
    return;
  }
  invalidate('Building identical fixtures and measuring both plans…');
  const ticket = revision;
  controller = new AbortController();
  $('run').disabled = true;
  try {
    const answer = await api('/api/compare', data, controller.signal);
    if (ticket === revision) display(answer);
  } catch (error) {
    if (ticket === revision && error.name !== 'AbortError') $('status').textContent = error.message;
  } finally {
    if (ticket === revision) {
      controller = null;
      $('run').disabled = false;
    }
  }
}
$('run').onclick = run;
$('variant').onchange = preview;
function download(value, name, type = 'application/json') {
  const url = URL.createObjectURL(
      new Blob([type === 'application/json' ? JSON.stringify(value, null, 2) : value], { type }),
    ),
    a = el('a');
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
$('save').onclick = () => {
  try {
    download(read(), 'query-experiment.json');
  } catch (error) {
    $('status').textContent = error.message;
  }
};
$('report').onclick = () => {
  if (result) download(result, 'query-comparison.json');
};
function csvCell(value) {
  let text = value === null ? 'NULL' : String(value);
  if (/^[=+\-@\t\r]/.test(text)) text = "'" + text;
  return '"' + text.replaceAll('"', '""') + '"';
}
$('csv').onclick = () => {
  const data = result?.[$('variant').value];
  if (!data || data.error) return;
  download(
    [data.columns, ...data.rows].map((row) => row.map(csvCell).join(',')).join('\r\n'),
    'query-preview.csv',
    'text/csv;charset=utf-8',
  );
};
$('import').onchange = async (event) => {
  const file = event.target.files[0];
  event.target.value = '';
  if (!file || !catalog) return;
  const ticket = ++importRevision;
  try {
    if (file.size > 32000) throw Error('Experiment file exceeds 32 KB.');
    const data = validate(JSON.parse(await file.text()));
    if (ticket !== importRevision) return;
    setStudy(data);
    $('status').textContent = 'Experiment loaded. Run the comparison when ready.';
  } catch (error) {
    if (ticket === importRevision) $('status').textContent = `Import failed: ${error.message}`;
  }
};
try {
  catalog = await api('/api/catalog');
  $('version').textContent = `SQLite ${catalog.sqlite_version}`;
  $('schema').textContent = catalog.schema;
  $('functions').textContent = catalog.functions.join(', ');
  for (const [name, sql] of Object.entries(catalog.indexes)) {
    const label = el('label'),
      input = el('input');
    input.type = 'checkbox';
    input.value = name;
    input.setAttribute('aria-label', `${name} index`);
    label.append(input, document.createTextNode(name), el('small', sql));
    $('indexes').append(label);
  }
  setStudy(catalog.examples.customer);
  await run();
} catch (error) {
  $('status').textContent = `Unable to load: ${error.message}`;
}
