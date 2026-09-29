import './styles.css';

// ---------------------------------------------------------------------------
// Draft state
// ---------------------------------------------------------------------------

const demoData = () => ({
  exposures: [
    { name: '校准暗场', duration: 3, est: 0, lst: 8, equipment: '探测器A', cooldown: 2 },
    { name: '样品P1-广角', duration: 4, est: 2, lst: 14, equipment: '探测器A', cooldown: 1 },
    { name: '样品P1-小角', duration: 3, est: 0, lst: 18, equipment: '探测器B', cooldown: 2 },
    { name: '样品P2-广角', duration: 5, est: 6, lst: 20, equipment: '探测器A', cooldown: 0 },
    { name: '样品P2-小角', duration: 2, est: 4, lst: 22, equipment: '探测器B', cooldown: 0 },
  ],
  links: [
    { a: 0, b: 1, min_gap: 1, max_gap: 10 },
    { a: 2, b: 4, min_gap: 2, max_gap: '' },
  ],
});

let draft = demoData();
let lastResult = null; // last successful response
let dirty = false;     // draft changed since last successful solve

const $ = (sel) => document.querySelector(sel);

// ---------------------------------------------------------------------------
// Tables
// ---------------------------------------------------------------------------

function renderExposures() {
  const tbody = $('#exposure-table tbody');
  tbody.innerHTML = '';
  draft.exposures.forEach((row, i) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="idx">${i}</td>
      <td><input data-f="name" value="${esc(row.name)}"></td>
      <td><input data-f="duration" type="number" min="1" step="1" value="${row.duration}"></td>
      <td><input data-f="est" type="number" step="1" value="${row.est}"></td>
      <td><input data-f="lst" type="number" step="1" value="${row.lst}"></td>
      <td><input data-f="equipment" value="${esc(row.equipment)}"></td>
      <td><input data-f="cooldown" type="number" min="0" step="1" value="${row.cooldown}"></td>`;
    tr.querySelectorAll('input').forEach((input) => {
      input.addEventListener('input', () => {
        const f = input.dataset.f;
        row[f] = f === 'name' || f === 'equipment' ? input.value : Number(input.value);
        markDirty();
      });
    });
    tbody.appendChild(tr);
  });
}

function renderLinks() {
  const tbody = $('#link-table tbody');
  tbody.innerHTML = '';
  draft.links.forEach((lk, i) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${exposureSelect('a', lk.a)}</td>
      <td>${exposureSelect('b', lk.b)}</td>
      <td><input data-f="min_gap" type="number" step="1" value="${lk.min_gap}"></td>
      <td><input data-f="max_gap" type="number" step="1" placeholder="不限" value="${lk.max_gap ?? ''}"></td>`;
    tr.querySelectorAll('select,input').forEach((input) => {
      input.addEventListener('change', () => {
        const f = input.dataset.f || input.dataset.s;
        if (input.tagName === 'SELECT') {
          lk[input.dataset.s] = Number(input.value);
        } else if (f === 'max_gap') {
          lk.max_gap = input.value.trim() === '' ? '' : Number(input.value);
        } else {
          lk[f] = Number(input.value);
        }
        markDirty();
      });
    });
    tbody.appendChild(tr);
  });
}

function exposureSelect(which, value) {
  const opts = draft.exposures
    .map((e, i) => `<option value="${i}" ${i === value ? 'selected' : ''}>${i} · ${esc(e.name)}</option>`)
    .join('');
  return `<select data-s="${which}">${opts}</select>`;
}

function refreshLinkSelectors() {
  // Exposure names/count may have changed: rebuild link rows preserving values.
  renderLinks();
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

// ---------------------------------------------------------------------------
// Dirty handling: any draft change immediately invalidates the old result
// ---------------------------------------------------------------------------

function markDirty() {
  dirty = true;
  $('#dirty-banner').classList.remove('hidden');
  if (lastResult) {
    document.querySelectorAll('.tl-block').forEach((el) => el.classList.add('stale'));
    $('#result').classList.add('stale');
  }
}

function clearDirtyAfterSuccess(result) {
  dirty = false;
  lastResult = result;
  $('#dirty-banner').classList.add('hidden');
  $('#result').classList.remove('stale');
}

// ---------------------------------------------------------------------------
// Solve
// ---------------------------------------------------------------------------

function buildPayload() {
  return {
    exposures: draft.exposures.map((e) => ({
      name: String(e.name ?? ''),
      duration: Number(e.duration),
      est: Number(e.est),
      lst: Number(e.lst),
      equipment: String(e.equipment ?? ''),
      cooldown: Number(e.cooldown),
    })),
    links: draft.links.map((l) => ({
      a: Number(l.a),
      b: Number(l.b),
      min_gap: Number(l.min_gap),
      max_gap: l.max_gap === '' || l.max_gap === null ? null : Number(l.max_gap),
    })),
  };
}

function hideBanners() {
  ['#input-errors', '#infeasible', '#loading'].forEach((s) =>
    $(s).classList.add('hidden'));
}

async function solve() {
  hideBanners();
  // A fresh request always discards the previous answer up-front: the UI
  // never keeps serving a partial or stale schedule.
  $('#result').classList.add('hidden');
  lastResult = null;
  $('#loading').classList.remove('hidden');

  let resp;
  try {
    resp = await fetch('/api/schedule', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(buildPayload()),
    });
  } catch (e) {
    $('#loading').classList.add('hidden');
    showInfeasibleBox('网络或服务异常：未获得任何排程结果，请检查 API 服务后重试。');
    return;
  }

  $('#loading').classList.add('hidden');
  const body = await resp.json().catch(() => null);

  if (resp.status === 422 && body) {
    showInputErrors(body);
    return;
  }
  if (resp.status === 400 && body) {
    showInputErrors(body);
    return;
  }
  if (resp.status === 409 && body) {
    showInfeasibleBox(body.reason_detail);
    return;
  }
  if (resp.status !== 200 || !body || body.status !== 'feasible') {
    showInfeasibleBox('服务返回异常，未获得排程结果（不会沿用旧方案）。');
    return;
  }

  clearDirtyAfterSuccess(body);
  renderResult(body);
}

function showInputErrors(body) {
  const box = $('#input-errors');
  const items = (body.errors || [])
    .map((e) => `<li>${esc(e.message)}${e.path ? ` <span class="ctx">位置：${esc(e.path)}</span>` : ''}</li>`)
    .join('');
  box.innerHTML =
    `<strong>输入错误（共 ${body.errors?.length ?? 0} 处）——未执行求解：</strong><ul>${items}</ul>`;
  box.classList.remove('hidden');
}

function showInfeasibleBox(detail) {
  const box = $('#infeasible');
  box.innerHTML =
    '<strong>无可执行时序</strong>：输入已通过格式校验，但不存在满足全部' +
    '设备占用/冷却与衔接间隔的整数开始时刻。服务未返回任何部分曝光，' +
    '页面也不会沿用旧方案。<br>' + esc(detail || '');
  box.classList.remove('hidden');
}

// ---------------------------------------------------------------------------
// Result rendering
// ---------------------------------------------------------------------------

function paletteColor(i) {
  const colors = [
    '#4da3ff', '#38c172', '#f5b041', '#b388ff', '#26c6da',
    '#ff8a65', '#9ccc65', '#f06292', '#80d8ff', '#ffd54f',
  ];
  return colors[i % colors.length];
}

function renderResult(body) {
  $('#result').classList.remove('hidden');
  const obj = body.objective;
  $('#objective').innerHTML =
    `最终结束时刻 <b>${body.final_end}</b> ｜ 开始时刻总和 <b>${obj.sum_starts}</b>` +
    ` ｜ 开始序列 <b>[${obj.start_vector.join(', ')}]</b>`;

  renderTimeline(body);
  renderEquipmentOrder(body);
  renderMargins(body);
}

function renderTimeline(body) {
  const starts = body.starts;
  const exposures = body.exposures;
  const tMax = Math.max(
    ...exposures.map((e, i) => starts[i] + e.duration + e.cooldown),
    body.final_end,
  );
  const pct = (t) => (t / tMax) * 100;

  const wrap = $('#timeline');
  wrap.innerHTML = '';
  exposures.forEach((e, i) => {
    const s = starts[i];
    const row = document.createElement('div');
    row.className = 'tl-row';
    const color = paletteColor(i);
    row.innerHTML = `
      <div class="tl-label" title="${esc(e.name)}">${i} · ${esc(e.name)}</div>
      <div class="tl-track">
        <div class="tl-block" style="left:${pct(s)}%;width:${pct(e.duration)}%;background:${color}"
             title="${esc(e.name)}：${s} → ${s + e.duration}">
          ${s}–${s + e.duration}
        </div>
        ${
          e.cooldown > 0
            ? `<div class="tl-block cool" style="left:${pct(s + e.duration)}%;width:${pct(e.cooldown)}%;background:${color}"></div>`
            : ''
        }
      </div>`;
    wrap.appendChild(row);
  });

  // axis
  const axis = document.createElement('div');
  axis.className = 'tl-axis';
  const step = Math.max(1, Math.ceil(tMax / 12));
  let ticks = '';
  for (let t = 0; t <= tMax; t += step) {
    ticks += `<span class="tl-tick" style="left:${pct(t)}%">${t}</span>`;
  }
  ticks += `<span class="tl-tick" style="left:100%">${tMax}</span>`;
  axis.innerHTML = `<div></div><div class="tl-ticks">${ticks}</div>`;
  wrap.appendChild(axis);
}

function renderEquipmentOrder(body) {
  const wrap = $('#equipment-order');
  wrap.innerHTML = '';
  const startByName = new Map(
    body.exposures.map((e, i) => [e.name, body.starts[i]]));
  Object.entries(body.equipment_order).forEach(([equip, names]) => {
    const line = document.createElement('div');
    line.className = 'eq-line';
    const steps = names
      .map((nm) => {
        const s = startByName.get(nm);
        return `<span class="eq-step">${esc(nm)} <span class="ctx" style="display:inline">@t=${s}</span></span>`;
      })
      .join('<span class="eq-arrow">→</span>');
    line.innerHTML = `<span class="eq-name">${esc(equip)}</span>${steps}`;
    wrap.appendChild(line);
  });
}

function renderMargins(body) {
  const tbody = $('#margin-table tbody');
  tbody.innerHTML = '';
  const labels = { window: '时间窗', link: '衔接间隔', equipment: '设备占用/冷却' };

  body.margins.forEach((m) => {
    const tr = document.createElement('tr');
    let name, actual, low, high, slack;
    if (m.type === 'window') {
      name = `${m.exposure} 须在 [${m.earliest}, ${m.latest}] 内开始`;
      actual = `开始 = ${m.start}`;
      low = m.earliest;
      high = m.latest;
      slack = `左 ${m.slack_start} ｜ 右 ${m.slack_end}`;
    } else if (m.type === 'link') {
      name = `${m.a} → ${m.b}`;
      actual = `间隔 = ${m.gap}`;
      low = `≥ ${m.min_gap}`;
      high = m.max_gap === null ? '不限' : `≤ ${m.max_gap}`;
      slack = `对下界 ${m.slack_min} ｜ 对上界 ${
        m.slack_max === null ? '—' : m.slack_max}`;
    } else {
      name = `设备「${m.equipment}」：${m.a} → ${m.b}`;
      actual = `间隔 = ${m.gap}`;
      low = `占用+冷却 ≥ ${m.required}`;
      high = '—';
      slack = `${m.slack}`;
    }
    tr.innerHTML = `
      <td>${labels[m.type]}</td><td>${esc(name)}</td><td>${actual}</td>
      <td>${low}</td><td>${high}</td><td>${esc(slack)}</td>`;
    tbody.appendChild(tr);
  });
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

$('#add-row').addEventListener('click', () => {
  if (draft.exposures.length >= 10) return;
  draft.exposures.push({
    name: `曝光${draft.exposures.length + 1}`,
    duration: 2, est: 0, lst: 20, equipment: '探测器A', cooldown: 0,
  });
  renderExposures();
  refreshLinkSelectors();
  markDirty();
});

$('#remove-row').addEventListener('click', () => {
  if (draft.exposures.length <= 5) return;
  const removed = draft.exposures.pop();
  // drop links referencing the removed last index and re-base others
  const n = draft.exposures.length;
  draft.links = draft.links
    .filter((l) => l.a < n && l.b < n);
  renderExposures();
  refreshLinkSelectors();
  markDirty();
});

$('#add-link').addEventListener('click', () => {
  draft.links.push({ a: 0, b: Math.min(1, draft.exposures.length - 1), min_gap: 0, max_gap: '' });
  renderLinks();
  markDirty();
});

$('#remove-link').addEventListener('click', () => {
  draft.links.pop();
  renderLinks();
  markDirty();
});

$('#load-demo').addEventListener('click', () => {
  draft = demoData();
  renderExposures();
  renderLinks();
  markDirty();
});

$('#solve-btn').addEventListener('click', solve);

async function pollHealth() {
  const el = $('#health');
  try {
    const r = await fetch('/api/health');
    if (r.ok) {
      el.textContent = '● API 正常';
      el.className = 'health ok';
    } else {
      throw new Error();
    }
  } catch {
    el.textContent = '● API 不可达';
    el.className = 'health bad';
  }
}

renderExposures();
renderLinks();
pollHealth();
setInterval(pollHealth, 10000);
