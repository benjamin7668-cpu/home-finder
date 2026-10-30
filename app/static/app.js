"use strict";

const STAGE_LABEL = {
  molit: "실거래가 수집 중",
  geocode: "동네 위치 확인 중",
  commute: "통근 시간 계산 중",
  infra: "주변 시설 확인 중",
};
const INFRA_LABEL = { subway: "지하철역", school: "학교", mart: "대형마트", hospital: "병원", park: "공원" };

const state = { work: { a: null, b: null }, data: null, results: [], selected: null, weights: { commute: 50, infra: 30, budget: 20 } };
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// 슬라이더 비중으로 종합 점수 재계산 (서버 호출 없음)
function recomputeTotals(results, weights) {
  const sum = weights.commute + weights.infra + weights.budget || 1;
  return results
    .map((r) => ({
      ...r,
      scores: {
        ...r.scores,
        total: Math.round((weights.commute * r.scores.commute + weights.infra * r.scores.infra + weights.budget * r.scores.budget) / sum),
      },
    }))
    .sort((x, y) => y.scores.total - x.scores.total);
}

/* ---------- 지도 ---------- */
// map.api: 카카오 또는 Leaflet(OpenStreetMap) 위에 같은 함수(clear/pin/fit/focus)를 제공한다
const map = { api: null };
const LEAFLET = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet";

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = resolve;
    s.onerror = reject;
    document.head.appendChild(s);
  });
}

function kakaoApi() {
  const obj = new kakao.maps.Map($("#map"), { center: new kakao.maps.LatLng(37.5, 127.0), level: 9 });
  let overlays = [], circle = null;
  return {
    clear() { overlays.forEach((o) => o.setMap(null)); overlays = []; },
    pin(lat, lng, el) {
      const ov = new kakao.maps.CustomOverlay({ position: new kakao.maps.LatLng(lat, lng), content: el, yAnchor: 0.5 });
      ov.setMap(obj);
      overlays.push(ov);
    },
    fit() {
      if (!overlays.length) return;
      const bounds = new kakao.maps.LatLngBounds();
      overlays.forEach((o) => bounds.extend(o.getPosition()));
      obj.setBounds(bounds);
    },
    focus(lat, lng) {
      const pos = new kakao.maps.LatLng(lat, lng);
      if (circle) circle.setMap(null);
      circle = new kakao.maps.Circle({ center: pos, radius: 1000, strokeWeight: 2, strokeColor: "#2563eb", strokeOpacity: 0.8, fillColor: "#2563eb", fillOpacity: 0.08 });
      circle.setMap(obj);
      obj.setLevel(6);
      obj.panTo(pos);
    },
  };
}

function leafletApi() {
  const obj = L.map($("#map")).setView([37.5, 127.0], 10);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(obj);
  let markers = [], circle = null;
  return {
    clear() { markers.forEach((m) => m.remove()); markers = []; },
    pin(lat, lng, el) {
      const icon = L.divIcon({ html: el, className: "", iconSize: null });
      markers.push(L.marker([lat, lng], { icon }).addTo(obj));
    },
    fit() { if (markers.length) obj.fitBounds(L.featureGroup(markers).getBounds(), { padding: [40, 40] }); },
    focus(lat, lng) {
      if (circle) circle.remove();
      circle = L.circle([lat, lng], { radius: 1000, weight: 2, color: "#2563eb", opacity: 0.8, fillOpacity: 0.08 }).addTo(obj);
      obj.setView([lat, lng], 15);
    },
  };
}

async function loadLeaflet() {
  const css = document.createElement("link");
  css.rel = "stylesheet";
  css.href = `${LEAFLET}.css`;
  document.head.appendChild(css);
  await loadScript(`${LEAFLET}.js`);
  return leafletApi();
}

async function loadMap(key) {
  try {
    if (!key) throw new Error("no key");
    await loadScript(`https://dapi.kakao.com/v2/maps/sdk.js?appkey=${encodeURIComponent(key)}&autoload=false`);
    await new Promise((resolve) => kakao.maps.load(resolve));
    $("#map").innerHTML = "";
    map.api = kakaoApi();
  } catch {
    // 카카오 키가 없거나 도메인이 등록되지 않았으면 키가 필요 없는 OpenStreetMap으로 대신 그린다
    try {
      $("#map").innerHTML = "";
      map.api = await loadLeaflet();
    } catch {
      $("#map").innerHTML = '<p class="map-empty">지도를 불러오지 못했습니다. 인터넷 연결을 확인하세요. 추천 목록은 그대로 사용할 수 있습니다.</p>';
      return;
    }
  }
  drawMap();
}

function scoreColor(total) {
  const alpha = 0.35 + 0.65 * Math.max(0, Math.min(1, total / 100));
  return `rgba(37, 99, 235, ${alpha.toFixed(2)})`;
}

function drawMap() {
  if (!map.api) return;
  map.api.clear();
  const add = (lat, lng, html, onClick) => {
    const el = document.createElement("div");
    el.innerHTML = html;
    const pin = el.firstElementChild;
    if (onClick) pin.addEventListener("click", onClick);
    map.api.pin(lat, lng, pin);
  };
  for (const who of ["a", "b"]) {
    const w = state.work[who];
    if (w) add(w.lat, w.lng, `<div class="pin work">${who.toUpperCase()} 직장</div>`);
  }
  state.results.forEach((r, i) => {
    add(r.coord.lat, r.coord.lng, `<div class="pin" style="background:${scoreColor(r.scores.total)}" title="${esc(r.name)}">${i + 1}</div>`, () => select(r.key));
  });
  map.api.fit();
}

function focusMap(r) {
  if (map.api) map.api.focus(r.coord.lat, r.coord.lng);
}

/* ---------- 직장 주소 ---------- */
async function findWork(box) {
  const who = box.dataset.who;
  const q = $(".work-q", box).value.trim();
  const pick = $(".work-pick", box);
  const picked = $(".work-picked", box);
  if (q === box.dataset.lastQuery) return; // 엔터·변경·버튼이 겹쳐도 한 번만 검색
  box.dataset.lastQuery = q;
  state.work[who] = null;
  updateSubmit(); // 검색이 끝나기 전에 추천 버튼을 누르지 못하게
  pick.hidden = true;
  if (!q) return updateSubmit();
  picked.textContent = "검색 중…";
  try {
    const resp = await fetch(`/api/geocode?q=${encodeURIComponent(q)}`);
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || "주소 검색 실패");
    const list = body.candidates;
    if (!list.length) { picked.textContent = "검색 결과가 없습니다. 다른 이름으로 입력해 보세요."; return updateSubmit(); }
    if (list.length === 1) { setWork(box, list[0]); return; }
    pick.innerHTML = '<option value="">여러 곳이 검색됐습니다. 선택하세요</option>' +
      list.map((p, i) => `<option value="${i}">${esc(p.name)} · ${esc(p.address)}</option>`).join("");
    pick.hidden = false;
    pick.onchange = () => {
      if (pick.value !== "") return setWork(box, list[Number(pick.value)]);
      state.work[who] = null;
      $(".work-picked", box).textContent = "";
      updateSubmit();
    };
    picked.textContent = "";
  } catch (e) {
    picked.textContent = e.message;
    box.dataset.lastQuery = ""; // 실패하면 같은 검색어로 다시 시도할 수 있게
  }
  updateSubmit();
}

function setWork(box, place) {
  state.work[box.dataset.who] = place;
  $(".work-picked", box).textContent = `✓ ${place.name} (${place.address})`;
  updateSubmit();
}

function updateSubmit() {
  $("#submit").disabled = !(state.work.a && state.work.b);
}

/* ---------- 추천 요청 (SSE) ---------- */
async function recommend(ev) {
  ev.preventDefault();
  if (!(state.work.a && state.work.b)) return updateSubmit();
  const progress = $("#progress");
  progress.className = "progress";
  progress.textContent = "준비 중…";
  $("#submit").disabled = true;
  const payload = {
    budget_eok: Number($("#budget").value),
    band: $('input[name="band"]:checked').value,
    work_a: [state.work.a.lat, state.work.a.lng],
    work_b: [state.work.b.lat, state.work.b.lng],
    max_a: Number($("#max-a").value),
    max_b: Number($("#max-b").value),
  };
  try {
    const resp = await fetch("/api/recommend", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!resp.ok) throw new Error(`요청 오류 (${resp.status})`);
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        handleEvent(buf.slice(0, idx));
        buf = buf.slice(idx + 2);
      }
    }
  } catch (e) {
    progress.className = "progress error";
    progress.textContent = e.message;
  } finally {
    updateSubmit();
  }
}

function handleEvent(block) {
  const lines = Object.fromEntries(block.split("\n").map((l) => [l.slice(0, l.indexOf(": ")), l.slice(l.indexOf(": ") + 2)]));
  const data = JSON.parse(lines.data);
  const progress = $("#progress");
  if (lines.event === "progress") {
    progress.textContent = `${STAGE_LABEL[data.stage]} (${data.done}/${data.total})`;
  } else if (lines.event === "error") {
    progress.className = "progress error";
    progress.textContent = data.message;
  } else if (lines.event === "result") {
    progress.textContent = data.results.length ? `추천 동네 ${data.results.length}곳` : "조건에 맞는 동네를 찾지 못했습니다.";
    state.data = data;
    state.results = recomputeTotals(data.results, state.weights);
    state.selected = null;
    $("#detail").hidden = true;
    $("#weights").hidden = !data.results.length;
    renderNotes();
    renderResults();
    drawMap();
  }
}

/* ---------- 결과 ---------- */
function trendText(pct) {
  if (pct === null || pct === undefined) return "변화율 계산 불가";
  const cls = pct > 0 ? "up" : pct < 0 ? "down" : "";
  return `<span class="${cls}">${pct > 0 ? "▲" : pct < 0 ? "▼" : "–"} ${Math.abs(pct).toFixed(1)}%</span>`;
}

function renderResults() {
  $("#results").innerHTML = state.results.map((r, i) => `
    <li class="result${r.key === state.selected ? " active" : ""}" data-key="${esc(r.key)}" tabindex="0">
      <span class="rank" style="background:${scoreColor(r.scores.total)}">${i + 1}</span>
      <div>
        <h3>${esc(r.name)}</h3>
        <div class="meta">${r.median_price.toFixed(2)}억 · A ${r.commute.a.min}분 / B ${r.commute.b.min}분 · ${trendText(r.trend_change_pct)}</div>
      </div>
      <div class="score">${r.scores.total}<small>점</small></div>
    </li>`).join("");
  for (const li of $("#results").children) {
    li.addEventListener("click", () => select(li.dataset.key));
    li.addEventListener("keydown", (e) => { if (e.key === "Enter") select(li.dataset.key); });
  }
}

function renderNotes() {
  const n = state.data.notes;
  const items = [];
  if (n.failed_sggs.length) items.push(`실거래가를 받지 못한 시·군·구 ${n.failed_sggs.length}곳 (${n.failed_sggs.map(esc).join(", ")})`);
  if (n.empty_sggs.length) items.push(`12개월 동안 아파트 거래가 0건인 시·군·구 ${n.empty_sggs.length}곳 (${n.empty_sggs.map(esc).join(", ")})`);
  if (n.low_deal_excluded) items.push(`최근 6개월 거래가 3건 미만이라 제외한 동네 ${n.low_deal_excluded}곳`);
  if (n.geocode_failed) items.push(`위치를 찾지 못해 제외한 동네 ${n.geocode_failed}곳`);
  if (n.commute_unavailable) items.push(`통근 시간 확인 불가로 제외한 동네 ${n.commute_unavailable}곳`);
  if (n.over_max_commute) items.push(`최대 통근 시간을 넘어 제외한 동네 ${n.over_max_commute}곳`);
  if (n.nearest_over_budget.length) {
    items.push("예산 안에 드는 동네가 없습니다. 참고로 가장 가까운 가격대: " +
      n.nearest_over_budget.map((x) => `${esc(x.name)} ${x.median_price.toFixed(2)}억`).join(", "));
  }
  const box = $("#notes");
  box.hidden = !items.length;
  box.innerHTML = `<strong>참고</strong><ul>${items.map((t) => `<li>${t}</li>`).join("")}</ul>`;
}

function select(key) {
  state.selected = key;
  const r = state.results.find((x) => x.key === key);
  if (!r) return;
  renderResults();
  renderDetail(r);
  focusMap(r);
}

function priceChart(trend) {
  const W = 340, H = 170, L = 34, R = 18, T = 10, B = 26;
  const prices = trend.map((p) => p.median).filter((v) => v !== null);
  if (!prices.length) return "<p>거래 기록이 없습니다.</p>";
  let lo = Math.min(...prices), hi = Math.max(...prices);
  if (lo === hi) { lo -= 0.5; hi += 0.5; }
  const maxCount = Math.max(...trend.map((p) => p.count), 1);
  const x = (i) => L + (i * (W - L - R)) / (trend.length - 1);
  const volH = 22, volBase = H - B;
  const y = (v) => T + ((hi - v) * (H - T - B - volH - 12)) / (hi - lo); // 가격선은 거래량 막대 위쪽에만
  let path = "", pen = false;
  trend.forEach((p, i) => {
    if (p.median === null) { pen = false; return; }
    path += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(p.median).toFixed(1)} `;
    pen = true;
  });
  const bw = ((W - L - R) / trend.length) * 0.6;
  const vols = trend.map((p, i) => `<rect class="vol" x="${(x(i) - bw / 2).toFixed(1)}" y="${(volBase - (p.count / maxCount) * volH).toFixed(1)}" width="${bw.toFixed(1)}" height="${((p.count / maxCount) * volH).toFixed(1)}"><title>${p.ym} 거래 ${p.count}건</title></rect>`).join("");
  const dots = trend.map((p, i) => p.median === null ? "" : `<circle class="dot" cx="${x(i).toFixed(1)}" cy="${y(p.median).toFixed(1)}" r="3"><title>${p.ym} 중위 ${p.median.toFixed(2)}억 (${p.count}건)</title></circle>`).join("");
  const labels = trend.map((p, i) => (i % 3 === 0 || i === trend.length - 1) ? `<text x="${x(i).toFixed(1)}" y="${H - 8}" text-anchor="middle">${p.ym.slice(2).replace("-", ".")}</text>` : "").join("");
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="월별 중위 거래가 추이">
    <text x="${L - 4}" y="${y(hi) + 4}" text-anchor="end">${hi.toFixed(1)}</text>
    <text x="${L - 4}" y="${y(lo) + 4}" text-anchor="end">${lo.toFixed(1)}</text>
    ${vols}<path class="line" d="${path}"/>${dots}${labels}</svg>
    <small>선: 월별 중위 거래가(억 원) · 막대: 거래 건수 · 거래 없는 달은 선이 끊깁니다</small>`;
}

function commuteRow(label, c) {
  const route = c.walk ? "도보" : `${c.summary || "경로 정보 없음"} · 환승 ${c.transfers}회`;
  return `<dt>${label}</dt><dd><b>${c.min}분</b> · ${esc(route)}</dd>`;
}

function renderDetail(r) {
  const d = $("#detail");
  const w = state.weights, sum = w.commute + w.infra + w.budget || 1;
  const parts = [["통근", "commute"], ["인프라", "infra"], ["예산 여유", "budget"]];
  d.innerHTML = `
    <button class="close" type="button" aria-label="닫기">✕</button>
    <h2>${esc(r.name)}</h2>
    <dl class="kv">
      <dt>최근 6개월 중위가</dt><dd><b>${r.median_price.toFixed(2)}억</b> (${r.deals_6m}건)</dd>
      <dt>가격 변화</dt><dd>${trendText(r.trend_change_pct)} <small>(처음 3개월 → 최근 3개월)</small></dd>
    </dl>
    <h3>가격 흐름 (12개월)</h3>
    ${priceChart(r.trend)}
    <h3>통근</h3>
    <dl class="kv">${commuteRow("A", r.commute.a)}${commuteRow("B", r.commute.b)}</dl>
    <h3>반경 1km 생활 인프라</h3>
    ${r.infra ? `<div class="infra">${Object.entries(INFRA_LABEL).map(([k, v]) => `<div><b>${r.infra[k]}</b>${v}</div>`).join("")}</div>` : "<p>주변 시설 정보를 확인하지 못했습니다.</p>"}
    <h3>점수 구성 (종합 ${r.scores.total}점)</h3>
    <div class="bars">${parts.map(([label, k]) => {
      const contrib = (w[k] * r.scores[k]) / sum;
      return `<div class="bar"><span>${label}</span><i style="width:${contrib}%"></i><span>${Math.round(contrib)}</span></div>`;
    }).join("")}</div>
    <p class="source">출처: 국토교통부 실거래가 (${state.data.period.from} ~ ${state.data.period.to}), ODsay, 카카오 로컬<br>
    중위가는 법정동 안 선택한 면적 구간 아파트 매매 거래의 대표값입니다.</p>`;
  d.hidden = false;
  $(".close", d).addEventListener("click", () => { d.hidden = true; state.selected = null; renderResults(); });
}

/* ---------- 시작 ---------- */
async function init() {
  try {
    const cfg = await (await fetch("/api/config")).json();
    if (cfg.missing.length) {
      const warn = $("#warning");
      warn.textContent = `설정되지 않은 키: ${cfg.missing.join(", ")} — 해당 기능을 쓸 수 없습니다. .env 파일을 확인하세요.`;
      warn.hidden = false;
    }
    loadMap(cfg.kakao_js_key);
  } catch {
    loadMap(null);
  }
  for (const box of document.querySelectorAll(".work")) {
    $(".work-find", box).addEventListener("click", () => findWork(box));
    $(".work-q", box).addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); findWork(box); } });
    $(".work-q", box).addEventListener("change", () => findWork(box));
  }
  for (const input of document.querySelectorAll("#weights input")) {
    input.addEventListener("input", () => {
      state.weights[input.dataset.w] = Number(input.value);
      input.nextElementSibling.textContent = input.value;
      if (!state.data) return;
      state.results = recomputeTotals(state.results, state.weights);
      renderResults();
      drawMap();
      if (state.selected) renderDetail(state.results.find((x) => x.key === state.selected));
    });
  }
  $("#form").addEventListener("submit", recommend);
}

init();
