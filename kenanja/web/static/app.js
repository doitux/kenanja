// Kenanja – Oberfläche (ohne Build-Schritt, reines JavaScript)
const $ = (s, el = document) => el.querySelector(s);
const app = $('#app');
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
async function api(path, opts = {}) {
  const r = await fetch(path, {headers: opts.body && !(opts.body instanceof FormData) ? {'Content-Type': 'application/json'} : {}, ...opts,
    body: opts.body && !(opts.body instanceof FormData) ? JSON.stringify(opts.body) : opts.body});
  if (!r.ok) { let t = await r.text(); try { t = JSON.parse(t).detail } catch (e) {} throw new Error(t); }
  return r.headers.get('content-type')?.includes('json') ? r.json() : r;
}
const pu = id => '/api/projekte/' + encodeURIComponent(id);
let BRIDGE = null;     // im eigenen Kenanja-Fenster: Ordnerauswahl, Links im normalen Browser
if (window.qt && window.qt.webChannelTransport && window.QWebChannel) new QWebChannel(qt.webChannelTransport, ch => { BRIDGE = ch.objects.bridge; });
let LIST = [];
let P = null;          // aktuelles Projekt
let SETTINGS = null;
const KINDS = {intro: 'Intro', verse: 'Strophe', prechorus: 'Pre-Chorus', chorus: 'Chorus', bridge: 'Bridge', interlude: 'Zwischenspiel',
  solo: 'Solo', outro: 'Outro', tag: 'Tag', choral: 'Choral', other: 'Sonstiges'};
const ROLES = {auto: 'automatisch', tacet: 'tacet', pad: 'Liegetöne', counter: 'Gegenstimme', build: 'Steigerung', hook: 'Solo/Hook',
  melody: 'Melodie', high: 'hoch/Finale', roots: 'Grundtöne', tenor: 'Tenorlage', pulse: 'Achtel-Puls'};

// „Mehr einstellen“: einmal aufgeklappt, bleiben alle Details sichtbar (auch in den anderen Schritten)
let MEHR = false;
try { MEHR = localStorage.getItem('kenanja_mehr') === '1'; } catch (e) {}
function mehrBtn(fn) {
  return `<div class="mehr"><button class="link" onclick="toggleMehr(${fn})">${MEHR ? 'Weniger anzeigen ▴' : 'Willst du noch mehr einstellen? ▾'}</button></div>`;
}
function toggleMehr(fn) {
  if (P && STEP_COLLECT[STEP]) STEP_COLLECT[STEP]();
  if (!P) neuSammeln();
  MEHR = !MEHR; try { localStorage.setItem('kenanja_mehr', MEHR ? '1' : '0'); } catch (e) {}
  fn();
}
function stepper(steps, cur, go) {
  return `<div class="stepper">${steps.map(([k, t], i) => `<button class="${i === cur ? 'active' : i < cur ? 'done' : ''}" onclick="${go}(${i})">
    <span class="num">${i < cur ? '✓' : i + 1}</span>${t}</button>`).join('<span class="sep"></span>')}</div>`;
}
function navBar(cur, n, go, last = '') {
  return `<div class="navbar">${cur > 0 ? `<button onclick="${go}(${cur - 1})">← Zurück</button>` : '<span></span>'}
    ${cur < n - 1 ? `<button class="primary big" onclick="${go}(${cur + 1})">Weiter →</button>` : last}</div>`;
}
function choice(name, val, opts) {   // große Auswahlknöpfe statt Dropdown
  return `<div class="choices">${opts.map(([v, t, d]) => `<label class="choice ${String(v) === String(val) ? 'on' : ''}">
    <input type="radio" name="${name}" value="${v}" ${String(v) === String(val) ? 'checked' : ''}
      onchange="this.closest('.choices').querySelectorAll('.choice').forEach(c=>c.classList.toggle('on', c.contains(this)))">
    <b>${t}</b>${d ? `<span>${d}</span>` : ''}</label>`).join('')}</div>`;
}
const radio = name => (document.querySelector(`input[name="${name}"]:checked`) || {}).value;

const MELODIE = [
  ['erste', 'Melodie wie beim ersten Mal', 'Standard. Die Melodie aus Strophe 1, Refrain 1 … gilt für alle Wiederholungen – auf deren Text gelegt.'],
  ['zweite', '+ zweite Stimme des Leadsängers', 'Singt der Leadsänger in einer Wiederholung eine andere Stimme, wird sie zusätzlich zur Melodie notiert.'],
  ['mehrstimmig', 'Mehrstimmig', 'Die Stimmen der Gruppe bzw. der Backgroundsänger werden zusätzlich notiert, wo sie neben der Melodie eigene Töne singen.']];
const ABLAUF = [
  ['audio', 'Wie in der Aufnahme', 'So, wie der Song im Audio gespielt wird – mit Intro, Zwischenspielen und Wiederholungen.'],
  ['text', 'Wie im Liedtext', 'In der Reihenfolge deines eingefügten Textes.'],
  ['leadsheet', 'Wie im Leadsheet', 'Nach einem vorhandenen Leadsheet (MuseScore-Datei).']];
const TEMPO = [[1, 'wie erkannt', ''], [2, 'doppeltes Tempo', 'weniger Sechzehntel'], [0.5, 'halbes Tempo', 'weniger lange Noten']];

// ------------------------------------------------------------- Start ----
let NEU = {step: 0, file: null, titel: '', autoren: '', text: '', ablauf: 'audio', melodie: 'erste', tf: 1, leadsheet: null};
const NEU_STEPS = [['audio', 'Aufnahme'], ['text', 'Liedtext'], ['los', 'Analysieren']];
async function showHome() {
  P = null;
  LIST = await api('/api/projekte');
  app.innerHTML = `<div class="home"><div class="card" id="neu"></div>
   <div class="card"><h2 style="margin-top:0">Deine Songs</h2>
    ${LIST.length ? `<table>${LIST.map((p, i) => `<tr><td><a href="#" onclick="openProject(LIST[${i}].id);return false"><b>${esc(p.titel)}</b></a>
       <div class="muted small">${esc(p.autoren || '')}</div></td><td class="small">${p.status === 'analyse' ? '⏳ Analyse läuft' : p.status === 'fehler' ? '⚠️ Fehler' : '✔'}</td>
       <td style="text-align:right"><button class="small danger" onclick="delProject(LIST[${i}].id)">Löschen</button></td></tr>`).join('')}</table>` : '<p class="muted">Noch keine Songs.</p>'}
    ${MEHR ? `<h3 style="margin-top:22px">Vorhandene Noten übernehmen</h3>
    <p class="muted small">Ein Leadsheet bzw. eine Partitur aus MuseScore (.mscz) als Song einlesen – z. B. um daraus Chord-Sheet, Streicher und
    Chorblatt zu erzeugen oder ein in MuseScore korrigiertes Leadsheet zurückzuholen.</p>
    <input type="file" id="f_mscz" accept=".mscz,.mscx,.json"> <button onclick="importMscz()">Importieren</button>` : ''}
    ${mehrBtn('showHome')}</div></div>`;
  neuRender();
}
function neuSammeln() {
  const v = id => $('#' + id) ? $('#' + id).value : null;
  if ($('#f_audio') && $('#f_audio').files[0]) NEU.file = $('#f_audio').files[0];
  if ($('#f_ls') && $('#f_ls').files[0]) NEU.leadsheet = $('#f_ls').files[0];
  if (v('f_titel') !== null) NEU.titel = v('f_titel');
  if (v('f_autoren') !== null) NEU.autoren = v('f_autoren');
  if (v('f_text') !== null) NEU.text = v('f_text');
  if (radio('n_abl')) NEU.ablauf = radio('n_abl');
  if (radio('n_mel')) NEU.melodie = radio('n_mel');
  if (radio('n_tf')) NEU.tf = +radio('n_tf');
}
function neuGo(i) {
  neuSammeln();
  if (i > 0 && !NEU.file) { NEU.step = 0; neuRender(); return alert('Bitte zuerst die Aufnahme (MP3 oder WAV) auswählen.'); }
  NEU.step = i; neuRender();
}
function neuAudio(inp) {
  const f = inp.files[0]; if (!f) return;
  NEU.file = f;
  if (!$('#f_titel').value) $('#f_titel').value = f.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ');
}
function neuRender() {
  const box = $('#neu'); if (!box) return;
  const s = NEU.step;
  let body = '';
  if (s === 0) body = `
    <p class="muted">Kenanja hört Takt, Tonart, Akkorde, Ablauf und Melodie heraus und legt deinen Text auf die Melodie.</p>
    <label>Aufnahme (MP3 oder WAV)</label>
    ${NEU.file ? `<div class="row"><span class="pill">✔ ${esc(NEU.file.name)}</span><button class="small" onclick="NEU.file=null;neuRender()">andere wählen</button></div>`
      : `<input type="file" id="f_audio" accept="audio/*,.mp3,.wav,.flac,.m4a" onchange="neuAudio(this)">`}
    <label>Titel</label><input type="text" id="f_titel" value="${esc(NEU.titel)}">
    ${MEHR ? `<label>Autoren (Text/Musik)</label><input type="text" id="f_autoren" value="${esc(NEU.autoren)}">` : ''}`;
  else if (s === 1) body = `
    <div class="spread"><label>Liedtext in Sing-Reihenfolge – Überschriften wie [Strophe 1], [Refrain]</label>
      <button class="small" onclick="neuSammeln();searchLyrics()">Text suchen ↗</button></div>
    <textarea id="f_text" class="lyrics" placeholder="[Strophe 1]&#10;Erste Zeile …&#10;Zweite Zeile …&#10;&#10;[Refrain]&#10;…&#10;&#10;Wiederholungen ausschreiben – oder nur die Überschrift erneut setzen (z. B. [Refrain]), dann wird der Text übernommen.">${esc(NEU.text)}</textarea>
    <p class="muted small">Ohne Text geht es auch – dann gibt es Akkorde und Ablauf, aber keine Melodie mit Silben.</p>`;
  else body = `
    <div class="summary"><div><span class="muted">Aufnahme</span><b>${esc(NEU.file ? NEU.file.name : '–')}</b></div>
     <div><span class="muted">Titel</span><b>${esc(NEU.titel || '–')}</b></div>
     <div><span class="muted">Liedtext</span><b>${NEU.text.trim() ? NEU.text.trim().split('\n').filter(l => l.trim()).length + ' Zeilen' : 'ohne'}</b></div></div>
    <p class="muted small">Standard: Ablauf wie in der Aufnahme, Melodie wie beim ersten Mal (für alle Wiederholungen übernommen).</p>
    ${MEHR ? `
     <h3>Ablauf</h3>${choice('n_abl', NEU.ablauf, ABLAUF)}
     <div id="n_ls_box" style="${NEU.ablauf === 'leadsheet' ? '' : 'display:none'}"><label>Leadsheet (.mscz)</label>
      ${NEU.leadsheet ? `<span class="pill">✔ ${esc(NEU.leadsheet.name)}</span> <button class="small" onclick="neuSammeln();NEU.leadsheet=null;neuRender()">andere</button>`
        : '<input type="file" id="f_ls" accept=".mscz,.mscx">'}</div>
     <h3>Melodie im Leadsheet</h3>${choice('n_mel', NEU.melodie, MELODIE)}
     <h3>Notenwerte</h3>${choice('n_tf', NEU.tf, TEMPO)}
     <p class="muted small">Viele Lieder lassen sich in zwei Tempi notieren (z.&nbsp;B. 65 oder 130 bpm). Bei sehr vielen Sechzehnteln „doppeltes Tempo“ wählen.</p>` : ''}
    <div id="sprachbox"></div>`;
  box.innerHTML = `<h1>Neuer Song</h1>${stepper(NEU_STEPS, s, 'neuGo')}<div class="stepbody">${body}</div>
    ${s === 2 ? mehrBtn('neuRender') : ''}
    ${navBar(s, 3, 'neuGo', `<button class="primary big" onclick="createProject()">Analysieren ▶</button>`)}
    ${s === 2 ? '<p class="muted small" style="text-align:right">Dauer: einige Minuten.</p>' : ''}`;
  document.querySelectorAll('input[name="n_abl"]').forEach(r => r.addEventListener('change', () => {
    $('#n_ls_box').style.display = radio('n_abl') === 'leadsheet' ? '' : 'none'; }));
  if (s === 2) sprachBox();
}
function searchLyrics() {
  const q = `${NEU.titel} ${NEU.autoren} Songtext`.trim();
  extern('https://www.google.com/search?q=' + encodeURIComponent(q));
}
async function createProject() {
  neuSammeln();
  if (!NEU.file) return neuGo(0);
  if (NEU.ablauf === 'leadsheet' && !NEU.leadsheet) return alert('Bitte das Leadsheet auswählen – oder einen anderen Ablauf.');
  const fd = new FormData();
  fd.append('audio', NEU.file); fd.append('titel', NEU.titel); fd.append('autoren', NEU.autoren); fd.append('text', NEU.text);
  fd.append('ablauf', NEU.ablauf); fd.append('melodie', NEU.melodie); fd.append('tempo_faktor', NEU.tf);
  if (NEU.ablauf === 'leadsheet' && NEU.leadsheet) fd.append('leadsheet', NEU.leadsheet);
  app.innerHTML = '<div class="card"><h2>Hochladen …</h2></div>';
  try {
    const r = await api('/api/projekte', {method: 'POST', body: fd});
    NEU = {step: 0, file: null, titel: '', autoren: '', text: '', ablauf: 'audio', melodie: 'erste', tf: 1, leadsheet: null};
    STEP = 0; openProject(r.id);
  } catch (e) { alert(e.message); showHome(); }
}
async function importMscz() {
  const f = $('#f_mscz').files[0]; if (!f) return;
  const fd = new FormData(); fd.append('datei', f);
  const r = await api('/api/projekte/import', {method: 'POST', body: fd});
  STEP = 0; openProject(r.id);
}

// Spracherkennung: optionales Modell (einmaliger Download), verankert Text und Melodie an den gesungenen Wörtern
let SPRACH_TIMER = null;
async function sprachBox(target = '#sprachbox') {
  const box = $(target); if (!box) return;
  let st; try { st = await api('/api/sprachmodell'); } catch (e) { return; }
  if (!st.verfuegbar) { box.innerHTML = ''; return; }
  if (st.bereit) { box.innerHTML = `<p class="muted small">✔ KI-Modelle eingerichtet – Text und Melodie werden an den gesungenen Wörtern ausgerichtet, Leadstimme und Gruppe sauber getrennt.</p>`; return; }
  if (st.laeuft) {
    box.innerHTML = `<div class="hint">KI-Modelle werden geladen … ${Math.round(st.fortschritt * 100)} %<div class="progress"><div style="width:${Math.round(st.fortschritt * 100)}%"></div></div></div>`;
    clearTimeout(SPRACH_TIMER); SPRACH_TIMER = setTimeout(() => sprachBox(target), 1500); return;
  }
  box.innerHTML = `<div class="hint"><b>Tipp:</b> Mit den KI-Modellen trifft Kenanja Melodie und Text deutlich besser: Spracherkennung (die Wörter
    werden dort angelegt, wo sie gesungen werden) und eine bessere Trennung von Leadstimme und Gruppe.
    Einmaliger Download, ca. 1,2 GB, läuft danach ohne Internet. Die Analyse dauert damit länger.
    ${st.fehler ? `<br><span class="danger-text">${esc(st.fehler)}</span>` : ''}
    <div style="margin-top:8px"><button class="small" onclick="sprachLaden('${target}')">KI-Modelle herunterladen</button></div></div>`;
}
async function sprachLaden(target) {
  try { await api('/api/sprachmodell', {method: 'POST'}); } catch (e) { return alert(e.message); }
  sprachBox(target);
}
function extern(url) { if (BRIDGE) BRIDGE.extern(url); else window.open(url, '_blank'); }
async function oeffnen(pfad) { try { await api('/api/oeffnen', {method: 'POST', body: {pfad}}); } catch (e) { alert(e.message); } }
async function ordnerWaehlen(id, titel) {
  if (!BRIDGE) return;
  BRIDGE.ordnerWaehlen(titel, $('#' + id).value, d => { if (d) $('#' + id).value = d; });
}
async function delProject(id) { if (confirm('Song wirklich löschen?')) { await api(pu(id), {method: 'DELETE'}); showHome(); } }

// ------------------------------------------------------------- Projekt ----
let LAST_ID = null;
async function openProject(id) {
  if (id !== LAST_ID) { STEP = 0; OUT_SEL = null; LAST_ID = id; }
  const d = await api(pu(id));
  if (d.job && d.job.status === 'analyse') { renderProgress(d); setTimeout(() => openProject(id), 1500); return; }
  if (!d.song) { app.innerHTML = `<div class="card"><h2>Fehler bei der Analyse</h2><p>${esc(d.meta.fehler || d.job?.fehler || '')}</p></div>`; return; }
  P = d; if (!SETTINGS) SETTINGS = await api('/api/einstellungen');
  render();
}
function renderProgress(d) {
  const j = d.job;
  app.innerHTML = `<div class="card"><h1>${esc(d.meta.titel)}</h1><p>${esc(j.schritt || 'Analyse')} …</p>
   <div class="progress"><div style="width:${Math.round((j.fortschritt || 0) * 100)}%"></div></div>
   <p class="muted small">Du kannst das Fenster offen lassen – das Ergebnis erscheint automatisch.</p></div>`;
}
async function openPdf(art) {
  if (!BRIDGE) return window.open(`${pu(P.id)}/vorschau/${art}?t=${Date.now()}`, '_blank');
  document.body.style.cursor = 'progress';
  try { await api(`${pu(P.id)}/vorschau/${art}?oeffnen=1`); } catch (e) { alert(e.message); }
  document.body.style.cursor = '';
}
async function saveSong(patch, rerender = true) {
  P.song = await api(`${pu(P.id)}/song`, {method: 'PUT', body: patch});
  if (P.song.id && P.song.id !== P.id) { P.id = P.song.id; P.pfad = P.pfad.replace(/[^/]+$/, P.id); }
  if (rerender) render();
}
function play(t0, t1) {
  const a = $('#player'); a.src = `${pu(P.id)}/audio`;
  a.currentTime = t0 || 0; a.play();
  if (t1) { const stop = () => { if (a.currentTime >= t1) { a.pause(); a.removeEventListener('timeupdate', stop); } }; a.addEventListener('timeupdate', stop); }
}

// ---- 1: Song & Text
function fmtTime(t) { t = Math.max(0, t); return Math.floor(t / 60) + ':' + String(Math.floor(t % 60)).padStart(2, '0'); }
// ------------------------------------------------------------- Projekt (Assistent) ----
let STEP = 0;
const STEPS = [['pruefen', 'Anhören & prüfen'], ['ablauf', 'Ablauf'], ['tonart', 'Tonart'], ['band', 'Band & Chor'], ['erzeugen', 'Erzeugen']];
const STEP_COLLECT = [collectPruefen, () => {}, () => {}, () => {}, () => {}];
let SNAP = '';   // Stand beim Laden – zum Erkennen ungespeicherter Änderungen
function render() {
  const s = P.song, rep = (P.meta.bericht || {});
  SNAP = JSON.stringify({sections: s.sections, form: s.form, title: s.title, subtitle: s.subtitle, version: s.version, authors: s.authors});
  app.innerHTML = `
   <div class="spread"><div><h1>${esc(s.title)}</h1>
    <div><span class="pill">Tonart ${esc(s.key)}${s.transpose ? ` → <b>${esc(s.target_key)}</b>` : ''}</span><span class="pill">Tempo ${Math.round(s.tempo)}</span>
     ${rep.takt ? `<span class="pill">${rep.takt}-Takt</span>` : ''}
     ${rep.spracherkennung === 'nicht eingerichtet' ? `<span class="pill warn" title="Unter Einstellungen die KI-Modelle herunterladen, danach neu analysieren">ohne Spracherkennung</span>` : ''}</div></div>
    <div class="row">${MEHR ? `<button class="small" onclick="openPdf('chordsheet')">Chord-Sheet ↗</button><button class="small" onclick="openPdf('chortext')">Chortext ↗</button>
     <button class="small" onclick="openPdf('gitarre')">Gitarre ↗</button>` : ''}<button class="small" onclick="oeffnen(P.pfad)" title="${esc(P.pfad)}">📁 Projektordner</button></div></div>
   ${stepper(STEPS, STEP, 'goStep')}
   <div id="tab"></div>
   ${navBar(STEP, STEPS.length, 'goStep')}`;
  [stepPruefen, stepAblauf, stepTonart, stepBand, stepErzeugen][STEP]();
}
// Weiterklicken speichert automatisch
async function goStep(i) {
  STEP_COLLECT[STEP]();
  const s = P.song, now = JSON.stringify({sections: s.sections, form: s.form, title: s.title, subtitle: s.subtitle, version: s.version, authors: s.authors});
  if (now !== SNAP) {
    const secs = s.sections.map(x => ({id: x.id, label: x.label, kind: x.kind, bar_chords: x.bar_chords, text: x.instrumental ? undefined : x.text}));
    try { await saveSong({sections: secs, form: s.form, title: s.title, subtitle: s.subtitle, version: s.version, authors: s.authors}, false); }
    catch (e) { return alert('Speichern hat nicht geklappt: ' + e.message); }
  }
  STEP = i; render(); window.scrollTo(0, 0);
}

// ---- 1: Anhören & prüfen
function stepPruefen() {
  const s = P.song, rep = P.meta.bericht || {};
  const warn = [rep.warnung, rep.ablauf_fehlt ? 'Diese Teile aus dem gewählten Ablauf wurden in der Aufnahme nicht gefunden: ' + rep.ablauf_fehlt.join(', ') : ''].filter(Boolean);
  $('#tab').innerHTML = `
  ${warn.map(w => `<div class="hint">${esc(w)}</div>`).join('')}
  <p class="muted">Hör in die Teile rein und prüfe den Text. ▶ spielt den Teil im Original.${MEHR ? ' Akkorde: ein Feld = ein Takt (<b>G G/F#</b> = Zählzeit 1 und 3, <b>G . Em7 D</b> = je Schlag, <b>%</b> = kein Wechsel).' : ''}</p>
  ${MEHR ? `<div class="card"><div class="grid3">
    <div><label>Titel</label><input type="text" id="s_title" value="${esc(s.title)}"></div>
    <div><label>Untertitel</label><input type="text" id="s_sub" value="${esc(s.subtitle)}"></div>
    <div><label>Version (Chorblatt)</label><input type="text" id="s_ver" value="${esc(s.version)}"></div></div>
    <label>Autoren (eine Zeile pro Person/Gruppe)</label><textarea id="s_auth" style="min-height:52px">${esc(s.authors)}</textarea></div>` : ''}
  ${s.sections.map(sec => `
   <div class="card sec ${sec.instrumental ? 'instr' : ''}" data-id="${sec.id}">
    <div class="spread"><div class="row">
      ${MEHR ? `<input type="text" class="sl" value="${esc(sec.label)}" style="width:170px;font-weight:600">
      <select class="sk" style="width:auto">${Object.entries(KINDS).map(([k, v]) => `<option value="${k}" ${k === sec.kind ? 'selected' : ''}>${v}</option>`).join('')}</select>`
      : `<b>${esc(sec.label)}</b>`}
      <span class="muted small">${sec.n_bars} Takte${sec.audio_start != null ? ' · ab ' + fmtTime(sec.audio_start) : ''}</span></div>
     <div class="row">${sec.audio_start != null ? `<button class="small" onclick="play(${sec.audio_start},${sec.audio_end || 0})">▶ anhören</button>` : ''}
      ${MEHR ? `<button class="small" onclick="copySection('${sec.id}')">Teil kopieren</button>` : ''}</div></div>
    ${MEHR ? `<div class="bars">${sec.bar_chords.map((c, k) => `<div><div class="barno">Takt ${k + 1}</div><input type="text" class="bc" value="${esc(c)}"></div>`).join('')}</div>`
      : `<div class="chords small">${esc(sec.bar_chords.join(' | '))}</div>`}
    ${sec.instrumental ? '' : `<textarea class="st" rows="${Math.max(2, sec.text.split('\n').length)}">${esc(sec.text)}</textarea>`}
   </div>`).join('')}
  ${MEHR ? `<div class="card"><h2 style="margin-top:0">Neu analysieren</h2>
   <p class="muted small">Wenn der Text fehlt oder vieles falsch zugeordnet ist, oder um Melodie/Notenwerte anders zu notieren.
   Die Spurtrennung ist schon erledigt, das geht jetzt schneller.</p>
   <label>Liedtext (in Sing-Reihenfolge)</label><textarea id="s_full" class="lyrics">${esc(P.liedtext)}</textarea>
   <h3>Melodie im Leadsheet</h3>${choice('r_mel', P.meta.melodie || 'erste', MELODIE)}
   <h3>Notenwerte</h3>${choice('r_tf', +(P.meta.tempo_faktor || 1), TEMPO)}
   <div class="row" style="margin-top:12px"><button class="primary" onclick="reanalyse()">Neu analysieren</button>
    <span class="muted small">Ablauf, Tonart und Besetzung bleiben erhalten, soweit möglich.</span></div></div>` : ''}
  ${mehrBtn('render')}`;
}
function collectPruefen() {
  const s = P.song;
  if ($('#s_title')) Object.assign(s, {title: $('#s_title').value, subtitle: $('#s_sub').value, version: $('#s_ver').value, authors: $('#s_auth').value});
  const old = Object.fromEntries(s.sections.map(x => [x.id, x.label]));
  document.querySelectorAll('.sec').forEach(el => {
    const sec = s.sections.find(x => x.id === el.dataset.id); if (!sec) return;
    if ($('.sl', el)) { sec.label = $('.sl', el).value; sec.kind = $('.sk', el).value; }
    if ($('.bc', el)) sec.bar_chords = [...el.querySelectorAll('.bc')].map(i => i.value);
    if ($('.st', el)) sec.text = $('.st', el).value;
  });
  // Abschnittsnamen auch im Ablauf nachziehen, wenn dort der alte Name stand
  s.form.forEach(f => { const sec = s.sections.find(x => x.id === f.section); if (sec && (f.label === old[f.section] || !f.label)) f.label = sec.label; });
}
async function copySection(id) { collectPruefen(); await goStep(STEP); P.song = await api(`${pu(P.id)}/abschnitt-kopieren/${id}`, {method: 'POST'}); render(); }
async function reanalyse() {
  await api(`${pu(P.id)}/neu-analysieren`, {method: 'POST', body: {text: $('#s_full').value, titel: P.song.title, autoren: P.song.authors,
    tempo_faktor: +(radio('r_tf') || 1), melodie: radio('r_mel') || 'erste'}});
  openProject(P.id);
}

// ---- 2: Ablauf
function stepAblauf() {
  const s = P.song, q = (s.notes || {}).ablauf_quelle || P.meta.ablauf || 'audio';
  const opts = s.sections.map(x => `<option value="${x.id}">${esc(x.label)}</option>`).join('');
  const hasText = ((P.meta.bericht || {}).textbloecke || []).length > 0;
  $('#tab').innerHTML = `
  <div class="card"><h3>Woher kommt der Ablauf?</h3>
   <div class="choices">${ABLAUF.map(([v, t, d]) => `<button class="choice ${v === q ? 'on' : ''}" ${v === 'text' && !hasText ? 'disabled title="Kein Liedtext eingefügt"' : ''}
     onclick="ablaufQuelle('${v}')"><b>${t}</b><span>${d}</span></button>`).join('')}</div>
   ${q === 'leadsheet' || MEHR ? `<div class="row" style="margin-top:8px"><span class="muted small">${P.leadsheet ? 'Leadsheet ist hinterlegt.' : 'Noch kein Leadsheet hinterlegt.'}</span>
     <input type="file" id="f_ls2" accept=".mscz,.mscx" style="width:auto"><button class="small" onclick="leadsheetHochladen()">${P.leadsheet ? 'Anderes Leadsheet nehmen' : 'Leadsheet hochladen'}</button></div>` : ''}
   <div id="abl_msg"></div></div>
  <div class="card"><h3>So spielt ihr den Song</h3>
   <p class="muted small" style="margin-top:0">Teile mit ↑ ↓ verschieben oder mit ✕ entfernen.${MEHR ? ' „Audio aus“ bestimmt, welcher Teil des Originals im Übe-Audio erklingt – z. B. der letzte Refrain mit dem ruhigen Audio vom ersten.' : ''}</p>
   <table>${MEHR ? '<tr><th>#</th><th>Bezeichnung</th><th>Teil</th><th>Wdh.</th><th>Audio aus</th><th></th></tr>' : ''}
   ${s.form.map((f, i) => `<tr>
     <td class="muted" style="width:30px">${i + 1}</td>
     ${MEHR ? `<td><input type="text" value="${esc(f.label)}" onchange="P.song.form[${i}].label=this.value"></td>
     <td><select onchange="P.song.form[${i}].section=this.value">${opts.replace(`value="${f.section}"`, `value="${f.section}" selected`)}</select></td>
     <td style="width:70px"><input type="number" min="1" max="8" value="${f.repeat}" onchange="P.song.form[${i}].repeat=+this.value"></td>
     <td><select onchange="P.song.form[${i}].audio_from=this.value||null"><option value="">(gleicher Teil)</option>${opts.replace(`value="${f.audio_from}"`, `value="${f.audio_from}" selected`)}</select></td>`
     : `<td><b>${esc(f.label)}</b>${f.repeat > 1 ? ` <span class="muted">(${f.repeat}×)</span>` : ''}${f.note ? ` <span class="muted small">${esc(f.note)}</span>` : ''}</td>`}
     <td><div class="row" style="justify-content:flex-end;flex-wrap:nowrap"><button class="small" onclick="moveForm(${i},-1)">↑</button><button class="small" onclick="moveForm(${i},1)">↓</button>
       ${MEHR ? `<button class="small" onclick="dupForm(${i})">doppeln</button>` : ''}<button class="small danger" onclick="delForm(${i})">✕</button></div></td></tr>`).join('')}
   </table>
   <div class="row" style="margin-top:12px"><select id="addsec" style="width:auto">${opts}</select><button onclick="addForm()">Teil anhängen</button></div></div>
  ${mehrBtn('render')}`;
}
async function ablaufQuelle(q) {
  if (q === 'leadsheet' && !P.leadsheet) { P.song.notes = {...(P.song.notes || {}), ablauf_quelle: 'leadsheet'}; stepAblauf();
    $('#abl_msg').innerHTML = '<p class="small">Bitte das Leadsheet (.mscz) auswählen und hochladen.</p>'; return; }
  try { const r = await api(`${pu(P.id)}/ablauf`, {method: 'POST', body: {quelle: q}}); ablaufFertig(r); } catch (e) { alert(e.message); }
}
async function leadsheetHochladen() {
  const f = $('#f_ls2').files[0]; if (!f) return alert('Bitte zuerst die Leadsheet-Datei (.mscz) auswählen.');
  const fd = new FormData(); fd.append('datei', f);
  try { const r = await api(`${pu(P.id)}/leadsheet`, {method: 'POST', body: fd}); P.leadsheet = true; ablaufFertig(r); } catch (e) { alert(e.message); }
}
function ablaufFertig(r) {
  P.song = r.song; render();
  if (r.fehlt && r.fehlt.length) $('#abl_msg').innerHTML = `<div class="hint">Nicht in der Aufnahme gefunden und daher weggelassen: ${r.fehlt.map(esc).join(', ')}</div>`;
}
function moveForm(i, d) { const f = P.song.form, j = i + d; if (j < 0 || j >= f.length) return; [f[i], f[j]] = [f[j], f[i]]; stepAblauf(); }
function dupForm(i) { P.song.form.splice(i + 1, 0, JSON.parse(JSON.stringify(P.song.form[i]))); stepAblauf(); }
function delForm(i) { P.song.form.splice(i, 1); stepAblauf(); }
function newItem(sec) { return {section: sec.id, label: sec.label, add: [], remove: [], note: '', singers: [], congregation: true, choir: 'sing', choir_cue: '', choir_dyn: '', strings: {}, repeat: 1, audio_from: null}; }
function addForm() { const id = $('#addsec').value; P.song.form.push(newItem(P.song.sections.find(s => s.id === id))); stepAblauf(); }

// ---- 3: Tonart
async function stepTonart() {
  const [profs, keys] = await Promise.all([api('/api/stimmprofile'), api(`${pu(P.id)}/tonart`)]);
  window.PROFS = profs;
  const singers = profs.filter(p => !p.is_congregation), u = keys.umfang || {};
  const best = keys.vorschlaege.slice(0, MEHR ? 6 : 3);
  $('#tab').innerHTML = `
  <div class="card"><h3>In welcher Tonart singt ihr?</h3>
   <p class="muted small" style="margin-top:0">Original: <b>${esc(P.song.key)}</b>${u.tiefster ? ` · Melodie von ${esc(u.tiefster)} bis ${esc(u.hoechster)} (in ${esc(P.song.target_key)})` : ''}.
   Die Vorschläge passen zu euren Stimmprofilen und zur Gemeinde.</p>
   ${keys.tauschen.length ? `<div class="hint" style="margin-bottom:8px">${keys.tauschen.map(esc).join('<br>')}</div>` : ''}
   ${best.map((k, i) => `<div class="keyopt ${i === 0 ? 'best' : ''}">
      <div><b>${esc(k.tonart_de)}</b> <span class="muted small">(${k.shift > 0 ? '+' : ''}${k.shift} Halbtöne · ${esc(k.gitarre)})${i === 0 ? ' · Empfehlung' : ''}</span>
       <div class="small">${k.hinweise.length ? k.hinweise.map(esc).join('<br>') : '<span class="plus">passt für alle</span>'}</div></div>
      <button class="${k.shift === P.song.transpose ? 'primary' : ''}" onclick="saveSong({transpose:${k.shift}})">${k.shift === P.song.transpose ? '✓ gewählt' : 'übernehmen'}</button></div>`).join('')}
   ${MEHR ? `<div class="row" style="margin-top:10px"><label style="margin:0">Von Hand:</label>
     <select style="width:auto" onchange="saveSong({transpose:+this.value})">${[...Array(13)].map((_, i) => i - 6).map(v => `<option value="${v}" ${v === P.song.transpose ? 'selected' : ''}>${v > 0 ? '+' : ''}${v}</option>`).join('')}</select></div>` : ''}
  </div>
  ${MEHR ? `<div class="card" id="spitzen"><h3>Spitzentöne prüfen</h3><p class="muted small">Lädt …</p></div>
  <div class="card"><h3>Tonumfang der Melodie (in ${esc(P.song.target_key)})</h3>
    ${u.tiefster ? `<p><b>${esc(u.tiefster)} – ${esc(u.hoechster)}</b> (${u.halbtoene} Halbtöne), Hauptlage <b>${esc(u.hauptlage)}</b></p>
    <table><tr><th>Höchste Töne</th><th>wie oft</th><th>Schläge</th><th>längster</th><th>wo</th></tr>
     ${u.hoechste.map(r => `<tr><td><b>${esc(r.ton)}</b></td><td>${r.anzahl}×</td><td>${r.schlaege}</td><td>${r.laengster}</td><td class="small">${esc(r.teile.join(', '))}</td></tr>`).join('')}
     <tr><th>Tiefste Töne</th><th></th><th></th><th></th><th></th></tr>
     ${u.tiefste.map(r => `<tr><td><b>${esc(r.ton)}</b></td><td>${r.anzahl}×</td><td>${r.schlaege}</td><td>${r.laengster}</td><td class="small">${esc(r.teile.join(', '))}</td></tr>`).join('')}
    </table>` : '<p class="muted">Keine gesungenen Noten gefunden.</p>'}</div>
  <div class="card"><h3>Wer singt was?</h3><table><tr><th>Teil</th>${singers.map(p => `<th>${esc(p.name)}</th>`).join('')}<th>Gemeinde</th></tr>
   ${P.song.form.map((f, r) => `<tr><td>${esc(f.label)}</td>${singers.map(p => `<td><input type="checkbox" ${f.singers.includes(p.id) ? 'checked' : ''}
      onchange="toggleSinger(${r},'${p.id}',this.checked)"></td>`).join('')}<td><input type="checkbox" ${f.congregation ? 'checked' : ''} onchange="P.song.form[${r}].congregation=this.checked"></td></tr>`).join('')}
   </table><div class="row" style="margin-top:10px"><button class="primary" onclick="saveSong({form:P.song.form})">Neu bewerten</button></div></div>
  <div class="card"><h3>Stimmprofile</h3><p class="muted small">Töne so, wie sie im Leadsheet stehen (deutsche Schreibweise: g, a, h, c', cis'', …). Männer singen das eine Oktave tiefer – das ist egal.</p>
   <table id="proftab"><tr><th>Name</th><th>bequem von</th><th>bis</th><th>geht noch runter bis</th><th>geht noch hoch bis</th><th>wird dünn ab</th><th></th></tr>
   ${profs.map((p, i) => `<tr data-i="${i}"><td><input type="text" class="pn" value="${esc(p.name)}" ${p.is_congregation ? 'readonly' : ''}></td>
     ${['comfort_low', 'comfort_high', 'limit_low', 'limit_high', 'weak_low'].map(k => `<td><input type="text" class="p_${k}" value="${esc(p[k])}"></td>`).join('')}
     <td>${p.is_congregation ? '' : `<button class="small danger" onclick="PROFS.splice(${i},1);saveProfiles(true)">✕</button>`}</td></tr>`).join('')}
   </table><div class="row" style="margin-top:10px"><button onclick="PROFS.push({id:'',name:'Neue Person',comfort_low:'a',comfort_high:\"c''\",limit_low:'g',limit_high:\"d''\",weak_low:'',note:'',is_congregation:false});saveProfiles(false)">Person hinzufügen</button>
   <button class="primary" onclick="saveProfiles(true)">Profile speichern</button></div></div>` : ''}
  ${mehrBtn('render')}`;
  if (MEHR) loadExtremes().catch(e => { $('#spitzen').innerHTML = '<h3>Spitzentöne prüfen</h3><p class="muted small">' + esc(e.message) + '</p>'; });
}

// ---- 4: Band & Chor
function lineupStates() {
  let st = new Set(); return P.song.form.map(f => { f.add.forEach(a => st.add(a)); f.remove.forEach(r => st.delete(r)); return new Set(st); });
}
function stepBand() {
  const insts = [...new Set([...(SETTINGS.besetzung || []), ...P.song.form.flatMap(f => [...f.add, ...f.remove])])];
  const states = lineupStates(), players = (SETTINGS.spieler || []);
  $('#tab').innerHTML = `
  <p class="muted">Haken = Instrument spielt in diesem Teil. Daraus entstehen die <span class="plus">+grünen</span> und <span class="minus">−roten</span> Einsätze im Chord-Sheet.</p>
  <div class="card" style="overflow:auto"><table class="lineup"><tr><th>Teil</th>${insts.map(i => `<th class="c">${esc(i)}</th>`).join('')}${MEHR ? '<th>Hinweis</th>' : ''}</tr>
   ${P.song.form.map((f, r) => `<tr><td><b>${esc(f.label)}</b><div class="small">${f.add.map(a => `<span class="plus">+${esc(a)}</span>`).join(' ')} ${f.remove.map(a => `<span class="minus">-${esc(a)}</span>`).join(' ')}</div></td>
     ${insts.map(i => `<td class="c"><input type="checkbox" data-r="${r}" data-i="${esc(i)}" ${states[r].has(i) ? 'checked' : ''} onchange="lineupChange()"></td>`).join('')}
     ${MEHR ? `<td><input type="text" value="${esc(f.note)}" placeholder="z. B. Gitarren-Solo" onchange="P.song.form[${r}].note=this.value"></td>` : ''}</tr>`).join('')}
  </table>${MEHR ? `<div class="row" style="margin-top:8px"><input type="text" id="newinst" placeholder="weiteres Instrument (Kürzel)" style="width:220px">
   <button onclick="SETTINGS.besetzung.push($('#newinst').value);stepBand()">hinzufügen</button></div>` : ''}</div>
  <div class="card"><h3>Chor</h3><table><tr><th>Teil</th><th>Chor</th>${MEHR ? '<th>Ausdruck (erscheint auf dem Chorblatt)</th><th>Farbe</th>' : ''}</tr>
   ${P.song.form.map((f, r) => `<tr><td>${esc(f.label)}</td>
     <td><select style="width:auto" onchange="P.song.form[${r}].choir=this.value"><option value="sing">singt</option><option value="solo" ${f.choir === 'solo' ? 'selected' : ''}>nicht mitsingen (Solo)</option><option value="tacet" ${f.choir === 'tacet' ? 'selected' : ''}>Pause</option></select></td>
     ${MEHR ? `<td><input type="text" value="${esc(f.choir_cue)}" placeholder="z. B. etwas lauter (dankbar)" onchange="P.song.form[${r}].choir_cue=this.value"></td>
     <td><select onchange="P.song.form[${r}].choir_dyn=this.value"><option value="">automatisch</option><option value="soft" ${f.choir_dyn === 'soft' ? 'selected' : ''}>rot (leiser)</option><option value="loud" ${f.choir_dyn === 'loud' ? 'selected' : ''}>grün (lauter)</option></select></td>` : ''}</tr>`).join('')}
  </table></div>
  ${MEHR ? `<div class="card"><h3>Streicher</h3><p class="muted small">„automatisch“: erst später einsetzen, im Refrain Liegetöne bzw. Gegenstimme, Bridge als Steigerung, ruhiger Schluss tacet.
   Spieler und Niveau stellst du unter „Erzeugen“ ein.</p>
   <table><tr><th>Teil</th>${players.map(p => `<th>${esc(p.name)}</th>`).join('')}</tr>
   ${P.song.form.map((f, r) => `<tr><td>${esc(f.label)}</td>${players.map(p => `<td><select onchange="P.song.form[${r}].strings['${esc(p.name)}']=this.value">
      ${Object.entries(ROLES).map(([k, v]) => `<option value="${k}" ${(f.strings[p.name] || 'auto') === k ? 'selected' : ''}>${v}</option>`).join('')}</select></td>`).join('')}</tr>`).join('')}
   </table></div>` : ''}
  ${mehrBtn('render')}`;
}
function lineupChange() {
  const n = P.song.form.length, sets = [...Array(n)].map(() => new Set());
  document.querySelectorAll('.lineup input[type=checkbox]').forEach(cb => { if (cb.checked) sets[+cb.dataset.r].add(cb.dataset.i); });
  let prev = new Set();
  P.song.form.forEach((f, r) => { f.add = [...sets[r]].filter(x => !prev.has(x)); f.remove = [...prev].filter(x => !sets[r].has(x)); prev = sets[r]; });
  stepBand();
}

// ---- 5: Erzeugen
const OUTS = [['chordsheet', 'Chord-Sheet (Band)', true], ['leadsheet', 'Leadsheet (MuseScore + PDF)', true], ['chortext', 'Chor-Textblatt', true],
  ['ueben', 'Übe-Audio (transponiert, auf euren Ablauf geschnitten)', true], ['partitur', 'Streicher-Partitur + Einzelstimmen', true],
  ['gitarre', 'Akustikgitarre mit Griffbildern', true], ['klavier', 'Klavier (Chord-Sheet mit Intro-Noten)', true],
  ['nebenstimmen', 'Nebenstimmen für Sänger', false], ['satb', 'Chorsatz SATB (Refrain/Choral)', false], ['uebespuren', 'Übe-Spuren aus MuseScore', false]];
let OUT_SEL = null;
function stepErzeugen() {
  if (!OUT_SEL) OUT_SEL = Object.fromEntries(OUTS.map(([k, , on]) => [k, on]));
  const pl = SETTINGS.spieler || [];
  const list = OUTS.filter(([k]) => OUT_SEL[k]).map(([, t]) => t.replace(/ \(.*/, ''));
  $('#tab').innerHTML = `
  <div class="card"><h3>Fertig zum Erzeugen</h3>
   <p>In <b>${esc(P.song.target_key)}</b> entsteht: ${list.map(esc).join(' · ')}.</p>
   ${MEHR ? `<div class="grid2">${OUTS.map(([k, t]) => `<label class="row" style="color:var(--ink)"><input type="checkbox" ${OUT_SEL[k] ? 'checked' : ''}
      onchange="OUT_SEL['${k}']=this.checked"> ${t}</label>`).join('')}</div>` : ''}
   <div class="row" style="margin-top:12px"><button class="primary big" onclick="runExport()">Jetzt erzeugen</button>
    <span class="muted small">Ziel: <a href="#" onclick="oeffnen(P.pfad + '/Export');return false">Export-Ordner im Projekt</a>${SETTINGS.bandordner ? ' + Kopie in den Band-Ordner' : ''}</span></div></div>
  ${MEHR ? `<div class="card"><h3>Einstellungen</h3>
    <label>Zusätzlich kopieren in (optional, z. B. Band-Ordner in der Cloud)</label>
    <div class="row" style="flex-wrap:nowrap"><input type="text" style="flex:1" id="o_bandordner" value="${esc(SETTINGS.bandordner || '')}" placeholder="leer = nur im Projekt">
     ${BRIDGE ? `<button class="small" onclick="ordnerWaehlen('o_bandordner','Band-Ordner wählen')">Wählen …</button>` : ''}</div>
    <div class="grid2"><div><label>Bandname (für „…-Ablauf.mp3“)</label><input type="text" id="o_band" value="${esc(SETTINGS.band)}"></div>
    <div><label>MP3-Qualität</label><select id="o_bitrate">${['128', '192', '256', '320', 'V0'].map(b => `<option ${String(SETTINGS.bitrate) === b ? 'selected' : ''} value="${b}">${b === 'V0' ? 'VBR V0 (variabel, höchste)' : b + ' kbit/s'}</option>`).join('')}</select></div></div>
    <label>Streicher (Name · Instrument · Niveau)</label>
    <div id="players">${pl.map((p, i) => playerRow(p, i)).join('')}</div>
    <button class="small" onclick="addPlayer()">+ Spieler</button></div>` : ''}
  <div id="exp"></div>
  ${mehrBtn('render')}`;
}
async function runExport() {
  const body = {...OUT_SEL};
  if ($('#o_band')) {
    body.bandordner = $('#o_bandordner').value.trim(); body.band = $('#o_band').value; body.bitrate = $('#o_bitrate').value;
    body.spieler = [...document.querySelectorAll('.pl')].map(r => ({name: $('.pl_n', r).value, instrument: $('.pl_i', r).value, level: $('.pl_l', r).value}));
    SETTINGS = await api('/api/einstellungen', {method: 'PUT', body: {bandordner: body.bandordner, band: body.band, bitrate: body.bitrate, spieler: body.spieler, besetzung: SETTINGS.besetzung}});
  } else {
    Object.assign(body, {bandordner: SETTINGS.bandordner || '', band: SETTINGS.band, bitrate: SETTINGS.bitrate, spieler: SETTINGS.spieler || []});
  }
  await goStep(STEP);   // Ungespeichertes vorher sichern
  await api(`${pu(P.id)}/erzeugen`, {method: 'POST', body});
  pollExport();
}

// Spitzentöne: die wenigen Töne, die über die Tonart entscheiden – anhören und bei Bedarf korrigieren
let XT = null, ACTX = null;
async function loadExtremes() {
  XT = await api(`${pu(P.id)}/spitzentoene`);
  const row = (g, gi, side) => `<div class="xt ${g.unsicher ? 'muted' : ''}" style="padding:6px 0;border-bottom:1px solid var(--line)">
    <div><b style="font-size:1.1em">${esc(g.ton)}</b> <span class="small">${g.anzahl}×${g.unsicher ? ' · zählt nicht (unsicher)' : ''}</span></div>
    <div class="small">${g.stellen.map((o, oi) => (oi === 6 ? `<details style="display:inline"><summary class="muted" style="display:inline;cursor:pointer">+ ${g.stellen.length - 6} weitere</summary>` : '') + `<span class="xstelle" style="${o.unsicher ? 'opacity:.55' : ''}">
      <span class="xl" title="${o.t0.toFixed(1)} s">${esc(o.teil)} T.${o.takt + 1} „${esc(o.text)}“</span>
      <button class="small" title="Leadstimme an dieser Stelle anhören" onclick="playSnip(${o.t0},${o.t1})">▶</button>
      <button class="small" title="erkannten Ton ${esc(o.ton)} vorspielen" onclick="playTone(${o.midi})">♪</button>
      <select class="small" onchange="fixTon('${side}',${gi},${oi},this.value)" title="korrigieren">
        <option value="">stimmt</option><option value="1">½ höher</option><option value="-1">½ tiefer</option>
        <option value="2">1 höher</option><option value="-2">1 tiefer</option>
        <option value="12">Oktave höher</option><option value="-12">Oktave tiefer</option>
        <option value="x" ${o.unsicher ? 'selected' : ''}>nicht werten</option></select></span>`).join('')}${g.stellen.length > 6 ? '</details>' : ''}</div></div>`;
  $('#spitzen').innerHTML = `<h3>Spitzentöne prüfen <span class="muted small">(im Original, Tonart ${esc(XT.tonart)} – so wie du es hörst)</span></h3>
   <p class="muted small" style="margin-top:0">Diese Töne entscheiden über die Zieltonart. ▶ spielt die getrennte Leadstimme an der Stelle, ♪ den erkannten Ton.
   Stimmt etwas nicht, hier korrigieren – die Vorschläge rechnen sofort neu. „nicht werten“ für Verzierungen, Kickser oder Ad-libs.</p>
   <div class="grid2"><div><h4 style="margin:4px 0">Höchste</h4>${XT.hoch.map((g, i) => row(g, i, 'hoch')).join('')}</div>
   <div><h4 style="margin:4px 0">Tiefste</h4>${XT.tief.map((g, i) => row(g, i, 'tief')).join('')}</div></div>`;
}
function playSnip(t0, t1) { const a = new Audio(`${pu(P.id)}/schnipsel?t0=${t0}&t1=${t1}`); a.play(); }
function playTone(midi) {
  ACTX = ACTX || new (window.AudioContext || window.webkitAudioContext)();
  const f = 440 * Math.pow(2, (midi - 69) / 12), t = ACTX.currentTime, g = ACTX.createGain();
  g.connect(ACTX.destination); g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(0.25, t + 0.02);
  g.gain.exponentialRampToValueAtTime(0.001, t + 1.4);
  [[1, 1], [2, 0.35], [3, 0.15]].forEach(([h, a]) => { const o = ACTX.createOscillator(), og = ACTX.createGain();
    o.frequency.value = f * h; og.gain.value = a; o.connect(og); og.connect(g); o.start(t); o.stop(t + 1.5); });
}
async function fixTon(side, gi, oi, v) {
  const o = XT[side][gi].stellen[oi];
  const body = {sid: o.sid, takt: o.takt, idx: o.idx};
  if (v === 'x') body.unsicher = true; else if (v === '') body.unsicher = false; else body.midi = o.midi + (+v);
  await api(`${pu(P.id)}/ton`, {method: 'PUT', body});
  const d = await api(pu(P.id)); P.song = d.song;
  stepTonart();
}

function toggleSinger(r, id, on) { const s = P.song.form[r].singers; if (on && !s.includes(id)) s.push(id); if (!on) P.song.form[r].singers = s.filter(x => x !== id); }
async function saveProfiles(store) {
  document.querySelectorAll('#proftab tr[data-i]').forEach(tr => { const p = PROFS[+tr.dataset.i]; if (!p) return; p.name = $('.pn', tr).value;
    ['comfort_low', 'comfort_high', 'limit_low', 'limit_high', 'weak_low'].forEach(k => p[k] = $('.p_' + k, tr).value.trim()); });
  if (store) { try { await api('/api/stimmprofile', {method: 'PUT', body: PROFS}); } catch (e) { return alert('Ton nicht erkannt: ' + e.message); } }
  stepTonart();
}

// ---- 5: Erzeugen
function playerRow(p, i) {
  return `<div class="row pl" style="margin:4px 0"><input type="text" class="pl_n" value="${esc(p.name)}" style="width:160px">
   <select class="pl_i">${['Violine', 'Bratsche', 'Violoncello'].map(x => `<option ${x === p.instrument ? 'selected' : ''}>${x}</option>`).join('')}</select>
   <select class="pl_l">${['leicht', 'mittel', 'schwer'].map(x => `<option ${x === p.level ? 'selected' : ''}>${x}</option>`).join('')}</select>
   <button class="small danger" onclick="this.parentElement.remove()">✕</button></div>`;
}
function addPlayer() { $('#players').insertAdjacentHTML('beforeend', playerRow({name: 'Violine 2', instrument: 'Violine', level: 'mittel'}, 0)); }
async function pollExport() {
  const j = await api(`${pu(P.id)}/erzeugen`), box = $('#exp'); if (!box) return;
  if (j.status === 'läuft') { box.innerHTML = `<div class="card"><h3>Wird erzeugt …</h3><div class="log">${esc((j.log || []).join('\n'))}</div></div>`; setTimeout(pollExport, 1500); return; }
  if (j.status === 'fehler') { box.innerHTML = `<div class="card"><h3>Fehler</h3><p>${esc(j.fehler)}</p></div>`; return; }
  const r = j.ergebnis || {};
  box.innerHTML = `<div class="card"><div class="spread"><h3>Fertig ✔</h3><button onclick="oeffnen(${esc(JSON.stringify(r.ordner))})">📁 Export-Ordner öffnen</button></div>
   <p class="muted small">${esc(r.ordner)}${r.kopie ? '<br>Kopie: ' + esc(r.kopie) : ''}</p>
   ${r.fehler && r.fehler.length ? `<div class="hint">${r.fehler.map(esc).join('<br>')}</div>` : ''}
   <div class="files">${Object.entries(r.dateien || {}).map(([k, fs]) => `<div style="margin:8px 0"><b>${esc(k)}</b>${fs.map(f => `<a href="#" onclick="oeffnen(${esc(JSON.stringify(f))});return false" title="öffnen">${esc(f.split('/').pop())}</a>`).join('')}</div>`).join('')}</div></div>`;
}

// ------------------------------------------------------------- Einstellungen ----
async function showSettings() {
  SETTINGS = await api('/api/einstellungen'); const g = SETTINGS.gefunden;
  app.innerHTML = `<div class="card"><h1>Einstellungen</h1>
   <div class="grid2"><div><label>Projektordner</label>
     <div class="row" style="flex-wrap:nowrap"><input type="text" style="flex:1" id="e_proj" value="${esc(SETTINGS.projektordner)}">${BRIDGE ? `<button class="small" onclick="ordnerWaehlen('e_proj','Projektordner wählen')">Wählen …</button>` : ''}</div>
     <div class="muted small">Jeder Song bekommt darin einen eigenen Ordner mit seinem Namen:<br>
     <code>Original/</code> (dein Audio) · <code>Analyse/</code> (Zwischenspeicher) · <code>Export/</code> (Blätter, Noten, MP3s) · <code>Vorschau/</code><br>
     Beim Ändern werden vorhandene Projekte mit verschoben.</div></div>
   <div><label>Band-Ordner (optional)</label>
     <div class="row" style="flex-wrap:nowrap"><input type="text" style="flex:1" id="e_band_dir" value="${esc(SETTINGS.bandordner || '')}" placeholder="leer = Export bleibt nur im Projekt">${BRIDGE ? `<button class="small" onclick="ordnerWaehlen('e_band_dir','Band-Ordner wählen')">Wählen …</button>` : ''}</div>
     <div class="muted small">Fertige Exporte werden zusätzlich dorthin kopiert, z. B. in den geteilten Cloud-Ordner der Band.</div></div></div>
   <div class="grid2"><div><label>Bandname</label><input type="text" id="e_band" value="${esc(SETTINGS.band)}"></div><div></div></div>
   <label>Band-Besetzung (Kürzel, mit Komma getrennt)</label><input type="text" id="e_inst" value="${esc((SETTINGS.besetzung || []).join(', '))}">
   <label>MuseScore-Programm (leer = automatisch suchen)</label><input type="text" id="e_ms" value="${esc(SETTINGS.musescore)}" placeholder="${esc(g.musescore)}">
   <p class="small">Gefunden: MuseScore: <b>${esc(g.musescore || 'nicht gefunden')}</b> · LibreOffice: <b>${esc(g.libreoffice || 'nicht gefunden')}</b> ·
   Rubber Band: <b>${esc(g.rubberband || 'nicht gefunden')}</b></p>
   <label>KI-Modelle (Spracherkennung, Stimmtrennung)</label><div id="sprachbox2"></div>
   <button class="primary" onclick="saveSettings()">Speichern</button></div>`;
  sprachBox('#sprachbox2');
}
async function saveSettings() {
  let r;
  try {
    r = await api('/api/einstellungen', {method: 'PUT', body: {projektordner: $('#e_proj').value, bandordner: $('#e_band_dir').value.trim(), band: $('#e_band').value,
      musescore: $('#e_ms').value, besetzung: $('#e_inst').value.split(',').map(s => s.trim()).filter(Boolean)}});
  } catch (e) { return alert(e.message); }
  if (r.verschoben) alert(r.verschoben + ' Projekt(e) in den neuen Projektordner verschoben.');
  showSettings();
}
showHome();
