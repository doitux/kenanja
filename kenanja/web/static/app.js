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
let TAB = 'song';
let SETTINGS = null;
const KINDS = {intro: 'Intro', verse: 'Strophe', prechorus: 'Pre-Chorus', chorus: 'Chorus', bridge: 'Bridge', interlude: 'Zwischenspiel',
  solo: 'Solo', outro: 'Outro', tag: 'Tag', choral: 'Choral', other: 'Sonstiges'};
const ROLES = {auto: 'automatisch', tacet: 'tacet', pad: 'Liegetöne', counter: 'Gegenstimme', build: 'Steigerung', hook: 'Solo/Hook',
  melody: 'Melodie', high: 'hoch/Finale', roots: 'Grundtöne', tenor: 'Tenorlage', pulse: 'Achtel-Puls'};

// ------------------------------------------------------------- Start ----
async function showHome() {
  P = null;
  const list = LIST = await api('/api/projekte');
  app.innerHTML = `
  <div class="grid2">
   <div class="card">
    <h1>Neuer Song</h1>
    <p class="muted small">MP3 (oder WAV) auswählen, Titel und Liedtext eintragen. Kenanja hört Takt, Tonart, Akkorde, Ablauf und Melodie heraus
    und legt den Text auf die Melodie. Danach kannst du alles prüfen und ändern.</p>
    <label>Audio-Datei</label><input type="file" id="f_audio" accept="audio/*,.mp3,.wav,.flac,.m4a">
    <div class="grid2"><div><label>Titel</label><input type="text" id="f_titel"></div>
    <div><label>Autoren (Text/Musik)</label><input type="text" id="f_autoren"></div></div>
    <div class="spread"><label>Liedtext – in Sing-Reihenfolge, Überschriften wie [Verse 1], [Chorus]</label>
      <button class="small" onclick="searchLyrics()">Text suchen ↗</button></div>
    <textarea id="f_text" class="lyrics" placeholder="[Verse 1]&#10;Erste Zeile …&#10;Zweite Zeile …&#10;&#10;[Chorus]&#10;…&#10;&#10;Wiederholungen bitte ausschreiben – oder nur die Überschrift erneut setzen (z. B. [Chorus]), dann wird der Text übernommen."></textarea>
    <div class="row" style="margin-top:12px"><button class="primary" onclick="createProject()">Analysieren</button>
      <span class="muted small">Dauer: einige Minuten (Gesang wird von der Begleitung getrennt).</span></div>
   </div>
   <div class="card">
    <h2 style="margin-top:0">Deine Songs</h2>
    ${list.length ? `<table>${list.map((p, i) => `<tr><td><a href="#" onclick="openProject(LIST[${i}].id);return false"><b>${esc(p.titel)}</b></a>
       <div class="muted small">${esc(p.autoren || '')}</div></td><td class="small">${p.status === 'analyse' ? '⏳ Analyse läuft' : p.status === 'fehler' ? '⚠️ Fehler' : '✔'}</td>
       <td><button class="small danger" onclick="delProject(LIST[${i}].id)">Löschen</button></td></tr>`).join('')}</table>` : '<p class="muted">Noch keine Songs.</p>'}
    <h2>Vorhandene Noten übernehmen</h2>
    <p class="muted small">Ein Leadsheet bzw. eine Partitur aus MuseScore (.mscz) einlesen – z. B. um daraus Chord-Sheet, Streicher und Chorblatt zu erzeugen
    oder ein in MuseScore korrigiertes Leadsheet wieder zurückzuholen.</p>
    <input type="file" id="f_mscz" accept=".mscz,.mscx,.json"> <button onclick="importMscz()">Importieren</button>
   </div>
  </div>`;
}
function searchLyrics() {
  const q = `${$('#f_titel').value} ${$('#f_autoren').value} Songtext`.trim();
  extern('https://www.google.com/search?q=' + encodeURIComponent(q));
}
function extern(url) { if (BRIDGE) BRIDGE.extern(url); else window.open(url, '_blank'); }
async function oeffnen(pfad) { try { await api('/api/oeffnen', {method: 'POST', body: {pfad}}); } catch (e) { alert(e.message); } }
async function ordnerWaehlen(id, titel) {
  if (!BRIDGE) return;
  BRIDGE.ordnerWaehlen(titel, $('#' + id).value, d => { if (d) $('#' + id).value = d; });
}
async function createProject() {
  const f = $('#f_audio').files[0];
  if (!f) return alert('Bitte eine Audio-Datei auswählen.');
  const fd = new FormData();
  fd.append('audio', f); fd.append('titel', $('#f_titel').value); fd.append('autoren', $('#f_autoren').value); fd.append('text', $('#f_text').value);
  app.innerHTML = '<div class="card"><h2>Hochladen …</h2></div>';
  const r = await api('/api/projekte', {method: 'POST', body: fd});
  openProject(r.id);
}
async function importMscz() {
  const f = $('#f_mscz').files[0]; if (!f) return;
  const fd = new FormData(); fd.append('datei', f);
  const r = await api('/api/projekte/import', {method: 'POST', body: fd});
  openProject(r.id);
}
async function delProject(id) { if (confirm('Song wirklich löschen?')) { await api(pu(id), {method: 'DELETE'}); showHome(); } }

// ------------------------------------------------------------- Projekt ----
async function openProject(id) {
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
function render() {
  const s = P.song, rep = (P.meta.bericht || {});
  app.innerHTML = `
   <div class="spread"><div><h1>${esc(s.title)}</h1>
    <div><span class="pill">Original: ${esc(s.key)}</span><span class="pill">Zieltonart: ${esc(s.target_key)} (${s.transpose > 0 ? '+' : ''}${s.transpose})</span>
     <span class="pill">Tempo ${Math.round(s.tempo)}</span>${rep.takt ? `<span class="pill">${rep.takt}-Takt</span>` : ''}
     ${rep.zeilen ? `<span class="pill">${esc(rep.zeilen)}</span>` : ''}</div></div>
    <div class="row"><button onclick="openPdf('chordsheet')">Chord-Sheet ↗</button><button onclick="openPdf('chortext')">Chortext ↗</button>
     <button onclick="openPdf('gitarre')">Gitarre ↗</button><button onclick="oeffnen(P.pfad)" title="${esc(P.pfad)}">📁 Projektordner</button></div></div>
   <div class="tabs">${[['song', '1 · Song & Text'], ['ablauf', '2 · Ablauf'], ['besetzung', '3 · Besetzung'], ['stimmen', '4 · Stimmen & Tonart'], ['erzeugen', '5 · Erzeugen']]
      .map(([k, t]) => `<button class="${TAB === k ? 'active' : ''}" onclick="TAB='${k}';render()">${t}</button>`).join('')}</div>
   <div id="tab"></div>`;
  ({song: tabSong, ablauf: tabAblauf, besetzung: tabBesetzung, stimmen: tabStimmen, erzeugen: tabErzeugen})[TAB]();
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
function tabSong() {
  const s = P.song;
  $('#tab').innerHTML = `
  <div class="card"><div class="grid3">
    <div><label>Titel</label><input type="text" id="s_title" value="${esc(s.title)}"></div>
    <div><label>Untertitel</label><input type="text" id="s_sub" value="${esc(s.subtitle)}"></div>
    <div><label>Version (Chorblatt)</label><input type="text" id="s_ver" value="${esc(s.version)}"></div></div>
    <label>Autoren (eine Zeile pro Person/Gruppe)</label><textarea id="s_auth" style="min-height:52px">${esc(s.authors)}</textarea>
    <div class="row" style="margin-top:10px"><button class="primary" onclick="saveHead()">Speichern</button></div></div>
  <div class="hint">Prüfe pro Teil die Akkorde (ein Feld = ein Takt; <b>G G/F#</b> = Zählzeit 1 und 3, <b>G . Em7 D</b> = je Schlag, <b>%</b> = kein Wechsel)
   und den Text (eine Zeile = eine Zeile im Chord-Sheet). Mit ▶ hörst du den Teil im Original.</div>
  ${s.sections.map((sec, i) => `
   <div class="card sec ${sec.instrumental ? 'instr' : ''}" data-id="${sec.id}">
    <div class="spread"><div class="row">
      <input type="text" class="sl" value="${esc(sec.label)}" style="width:170px;font-weight:600">
      <select class="sk">${Object.entries(KINDS).map(([k, v]) => `<option value="${k}" ${k === sec.kind ? 'selected' : ''}>${v}</option>`).join('')}</select>
      <span class="muted small">${sec.n_bars} Takte${sec.audio_start != null ? ' · ab ' + fmtTime(sec.audio_start) : ''}</span></div>
     <div class="row">${sec.audio_start != null ? `<button class="small" onclick="play(${sec.audio_start},${sec.audio_end || 0})">▶ anhören</button>` : ''}
      <button class="small" onclick="copySection('${sec.id}')">Teil kopieren</button></div></div>
    <div class="bars">${sec.bar_chords.map((c, k) => `<div><div class="barno">Takt ${k + 1}</div><input type="text" class="bc" value="${esc(c)}"></div>`).join('')}</div>
    ${sec.instrumental ? '' : `<label>Text dieses Teils</label><textarea class="st">${esc(sec.text)}</textarea>`}
   </div>`).join('')}
  <div class="row"><button class="primary" onclick="saveSections()">Akkorde & Text speichern</button></div>
  <div class="card" style="margin-top:20px"><h2 style="margin-top:0">Ganzen Liedtext neu zuordnen</h2>
   <p class="muted small">Wenn der Text noch fehlt oder vieles falsch zugeordnet ist: Text hier (in Sing-Reihenfolge) einfügen und neu analysieren.
   Die Spurtrennung ist schon erledigt, das geht jetzt schnell.</p>
   <textarea id="s_full" class="lyrics">${esc(P.liedtext)}</textarea>
   <div class="row"><button onclick="reanalyse()">Neu analysieren</button></div></div>`;
}
function fmtTime(t) { t = Math.max(0, t); return Math.floor(t / 60) + ':' + String(Math.floor(t % 60)).padStart(2, '0'); }
async function saveHead() {
  await saveSong({title: $('#s_title').value, subtitle: $('#s_sub').value, version: $('#s_ver').value, authors: $('#s_auth').value});
}
async function saveSections() {
  const secs = [...document.querySelectorAll('.sec')].map(el => ({
    id: el.dataset.id, label: $('.sl', el).value, kind: $('.sk', el).value,
    bar_chords: [...el.querySelectorAll('.bc')].map(i => i.value), text: $('.st', el) ? $('.st', el).value : undefined}));
  // Abschnittsnamen auch im Ablauf aktualisieren, wenn dort der alte Name stand
  const old = Object.fromEntries(P.song.sections.map(s => [s.id, s.label]));
  const form = P.song.form.map(f => { const n = secs.find(x => x.id === f.section); return n && (f.label === old[f.section] || !f.label) ? {...f, label: n.label} : f; });
  await saveSong({sections: secs, form});
}
async function copySection(id) { P.song = await api(`${pu(P.id)}/abschnitt-kopieren/${id}`, {method: 'POST'}); render(); }
async function reanalyse() {
  await api(`${pu(P.id)}/neu-analysieren`, {method: 'POST', body: {text: $('#s_full').value, titel: P.song.title, autoren: P.song.authors}});
  openProject(P.id);
}

// ---- 2: Ablauf
function tabAblauf() {
  const s = P.song, opts = s.sections.map(x => `<option value="${x.id}">${esc(x.label)}</option>`).join('');
  $('#tab').innerHTML = `
  <div class="hint">So spielt <b>ihr</b> den Song. Teile verschieben, doppeln, wiederholen oder entfernen. „Audio aus“ bestimmt, welcher Teil des Originals
   im Übe-Audio erklingt – z. B. Chorus 3 mit dem ruhigen Audio von Chorus 1 („wie am Anfang“).</div>
  <div class="card"><table><tr><th>#</th><th>Bezeichnung</th><th>Teil</th><th>Wdh.</th><th>Audio aus</th><th></th></tr>
   ${s.form.map((f, i) => `<tr>
     <td>${i + 1}</td>
     <td><input type="text" value="${esc(f.label)}" onchange="P.song.form[${i}].label=this.value"></td>
     <td><select onchange="P.song.form[${i}].section=this.value">${opts.replace(`value="${f.section}"`, `value="${f.section}" selected`)}</select></td>
     <td style="width:70px"><input type="number" min="1" max="8" value="${f.repeat}" onchange="P.song.form[${i}].repeat=+this.value"></td>
     <td><select onchange="P.song.form[${i}].audio_from=this.value||null"><option value="">(gleicher Teil)</option>${opts.replace(`value="${f.audio_from}"`, `value="${f.audio_from}" selected`)}</select></td>
     <td class="row"><button class="small" onclick="moveForm(${i},-1)">↑</button><button class="small" onclick="moveForm(${i},1)">↓</button>
       <button class="small" onclick="dupForm(${i})">doppeln</button><button class="small danger" onclick="delForm(${i})">✕</button></td></tr>`).join('')}
  </table>
  <div class="row" style="margin-top:12px"><select id="addsec">${opts}</select><button onclick="addForm()">Teil anhängen</button>
   <button class="primary" onclick="saveSong({form:P.song.form})">Ablauf speichern</button>
   <button onclick="resetForm()">Original-Ablauf</button></div></div>`;
}
function moveForm(i, d) { const f = P.song.form, j = i + d; if (j < 0 || j >= f.length) return; [f[i], f[j]] = [f[j], f[i]]; tabAblauf(); }
function dupForm(i) { P.song.form.splice(i + 1, 0, JSON.parse(JSON.stringify(P.song.form[i]))); tabAblauf(); }
function delForm(i) { P.song.form.splice(i, 1); tabAblauf(); }
function addForm() { const id = $('#addsec').value, sec = P.song.sections.find(s => s.id === id);
  P.song.form.push({section: id, label: sec.label, add: [], remove: [], note: '', singers: [], congregation: true, choir: 'sing', choir_cue: '', choir_dyn: '', strings: {}, repeat: 1, audio_from: null}); tabAblauf(); }
function resetForm() { P.song.form = P.song.sections.map(s => ({section: s.id, label: s.label, add: [], remove: [], note: '', singers: [], congregation: true,
  choir: 'sing', choir_cue: '', choir_dyn: '', strings: {}, repeat: 1, audio_from: null})); tabAblauf(); }

// ---- 3: Besetzung
function lineupStates() {
  let st = new Set(); return P.song.form.map(f => { f.add.forEach(a => st.add(a)); f.remove.forEach(r => st.delete(r)); return new Set(st); });
}
function tabBesetzung() {
  const insts = [...new Set([...(SETTINGS.besetzung || []), ...P.song.form.flatMap(f => [...f.add, ...f.remove])])];
  const states = lineupStates(), players = (SETTINGS.spieler || []);
  $('#tab').innerHTML = `
  <div class="hint">Haken = Instrument spielt in diesem Teil. Daraus entstehen automatisch die <span class="plus">+grünen</span> und
   <span class="minus">−roten</span> Angaben im Chord-Sheet. Darunter: Chor-Anweisungen (Farbcode im Chorblatt) und Streicherrollen.</div>
  <div class="card" style="overflow:auto"><table class="lineup"><tr><th>Teil</th>${insts.map(i => `<th class="c">${esc(i)}</th>`).join('')}<th>Hinweis</th></tr>
   ${P.song.form.map((f, r) => `<tr><td><b>${esc(f.label)}</b><div class="small">${f.add.map(a => `<span class="plus">+${esc(a)}</span>`).join(' ')} ${f.remove.map(a => `<span class="minus">-${esc(a)}</span>`).join(' ')}</div></td>
     ${insts.map(i => `<td class="c"><input type="checkbox" data-r="${r}" data-i="${esc(i)}" ${states[r].has(i) ? 'checked' : ''} onchange="lineupChange()"></td>`).join('')}
     <td><input type="text" value="${esc(f.note)}" placeholder="z. B. LEGit-Solo" onchange="P.song.form[${r}].note=this.value"></td></tr>`).join('')}
  </table><div class="row" style="margin-top:8px"><input type="text" id="newinst" placeholder="weiteres Instrument (Kürzel)" style="width:220px">
   <button onclick="SETTINGS.besetzung.push($('#newinst').value);tabBesetzung()">hinzufügen</button></div></div>
  <div class="card"><h3>Chor</h3><table><tr><th>Teil</th><th>Chor</th><th>Ausdruck (erscheint auf dem Chorblatt)</th><th>Farbe</th></tr>
   ${P.song.form.map((f, r) => `<tr><td>${esc(f.label)}</td>
     <td><select onchange="P.song.form[${r}].choir=this.value"><option value="sing">singt</option><option value="solo" ${f.choir === 'solo' ? 'selected' : ''}>nicht mitsingen (Solo)</option><option value="tacet" ${f.choir === 'tacet' ? 'selected' : ''}>Pause</option></select></td>
     <td><input type="text" value="${esc(f.choir_cue)}" placeholder="z. B. etwas lauter (dankbar)" onchange="P.song.form[${r}].choir_cue=this.value"></td>
     <td><select onchange="P.song.form[${r}].choir_dyn=this.value"><option value="">automatisch</option><option value="soft" ${f.choir_dyn === 'soft' ? 'selected' : ''}>rot (leiser)</option><option value="loud" ${f.choir_dyn === 'loud' ? 'selected' : ''}>grün (lauter)</option></select></td></tr>`).join('')}
  </table></div>
  <div class="card"><h3>Streicher</h3><p class="muted small">„automatisch“ folgt deinem Stil: erst später einsetzen, im Chorus Liegetöne bzw. Gegenstimme, Bridge als Steigerung, ruhiger Schluss tacet.
   Spieler und Niveau stellst du unter „Erzeugen“ bzw. in den Einstellungen ein.</p>
   <table><tr><th>Teil</th>${players.map(p => `<th>${esc(p.name)}</th>`).join('')}</tr>
   ${P.song.form.map((f, r) => `<tr><td>${esc(f.label)}</td>${players.map(p => `<td><select onchange="P.song.form[${r}].strings['${esc(p.name)}']=this.value">
      ${Object.entries(ROLES).map(([k, v]) => `<option value="${k}" ${(f.strings[p.name] || 'auto') === k ? 'selected' : ''}>${v}</option>`).join('')}</select></td>`).join('')}</tr>`).join('')}
   </table></div>
  <div class="row"><button class="primary" onclick="saveSong({form:P.song.form})">Besetzung speichern</button></div>`;
}
function lineupChange() {
  const n = P.song.form.length, sets = [...Array(n)].map(() => new Set());
  document.querySelectorAll('.lineup input[type=checkbox]').forEach(cb => { if (cb.checked) sets[+cb.dataset.r].add(cb.dataset.i); });
  let prev = new Set();
  P.song.form.forEach((f, r) => { f.add = [...sets[r]].filter(x => !prev.has(x)); f.remove = [...prev].filter(x => !sets[r].has(x)); prev = sets[r]; });
  tabBesetzung();
}

// ---- 4: Stimmen & Tonart
async function tabStimmen() {
  const [profs, keys] = await Promise.all([api('/api/stimmprofile'), api(`${pu(P.id)}/tonart`)]);
  window.PROFS = profs;
  const singers = profs.filter(p => !p.is_congregation), u = keys.umfang || {};
  $('#tab').innerHTML = `
  <div class="card" id="spitzen"><h3>Spitzentöne prüfen</h3><p class="muted small">Lädt …</p></div>
  <div class="grid2">
   <div class="card"><h3>Tonumfang der Melodie (in ${esc(P.song.target_key)})</h3>
    ${u.tiefster ? `<p><b>${esc(u.tiefster)} – ${esc(u.hoechster)}</b> (${u.halbtoene} Halbtöne), Hauptlage <b>${esc(u.hauptlage)}</b></p>
    <table><tr><th>Höchste Töne</th><th>wie oft</th><th>Schläge</th><th>längster</th><th>wo</th></tr>
     ${u.hoechste.map(r => `<tr><td><b>${esc(r.ton)}</b></td><td>${r.anzahl}×</td><td>${r.schlaege}</td><td>${r.laengster}</td><td class="small">${esc(r.teile.join(', '))}</td></tr>`).join('')}
     <tr><th>Tiefste Töne</th><th></th><th></th><th></th><th></th></tr>
     ${u.tiefste.map(r => `<tr><td><b>${esc(r.ton)}</b></td><td>${r.anzahl}×</td><td>${r.schlaege}</td><td>${r.laengster}</td><td class="small">${esc(r.teile.join(', '))}</td></tr>`).join('')}
    </table>` : '<p class="muted">Keine gesungenen Noten gefunden.</p>'}</div>
   <div class="card"><h3>Tonart-Vorschläge</h3>
    ${keys.tauschen.length ? `<div class="hint" style="margin-bottom:8px">${keys.tauschen.map(esc).join('<br>')}</div>` : ''}
    ${keys.vorschlaege.slice(0, 5).map((k, i) => `<div class="keyopt ${i === 0 ? 'best' : ''}" style="padding:8px;border-bottom:1px solid var(--line)">
      <div><b>${esc(k.tonart_de)}</b> <span class="muted small">(${k.shift > 0 ? '+' : ''}${k.shift} Halbtöne · ${esc(k.gitarre)})</span>
       <div class="small">${k.hinweise.length ? k.hinweise.map(esc).join('<br>') : '<span class="plus">passt für alle</span>'}</div></div>
      <button class="small ${k.shift === P.song.transpose ? 'primary' : ''}" onclick="saveSong({transpose:${k.shift}})">${k.shift === P.song.transpose ? 'gewählt' : 'übernehmen'}</button></div>`).join('')}
    <div class="row" style="margin-top:10px"><label style="margin:0">Von Hand:</label>
     <select onchange="saveSong({transpose:+this.value})">${[...Array(13)].map((_, i) => i - 6).map(v => `<option value="${v}" ${v === P.song.transpose ? 'selected' : ''}>${v > 0 ? '+' : ''}${v}</option>`).join('')}</select></div>
   </div></div>
  <div class="card"><h3>Wer singt was?</h3><table><tr><th>Teil</th>${singers.map(p => `<th>${esc(p.name)}</th>`).join('')}<th>Gemeinde</th></tr>
   ${P.song.form.map((f, r) => `<tr><td>${esc(f.label)}</td>${singers.map(p => `<td><input type="checkbox" ${f.singers.includes(p.id) ? 'checked' : ''}
      onchange="toggleSinger(${r},'${p.id}',this.checked)"></td>`).join('')}<td><input type="checkbox" ${f.congregation ? 'checked' : ''} onchange="P.song.form[${r}].congregation=this.checked"></td></tr>`).join('')}
   </table><div class="row" style="margin-top:10px"><button class="primary" onclick="saveSong({form:P.song.form})">Speichern & neu bewerten</button></div></div>
  <div class="card"><h3>Stimmprofile</h3><p class="muted small">Töne so, wie sie im Leadsheet stehen (deutsche Schreibweise: g, a, h, c', cis'', …). Männer singen das eine Oktave tiefer – das ist egal.</p>
   <table id="proftab"><tr><th>Name</th><th>bequem von</th><th>bis</th><th>geht noch runter bis</th><th>geht noch hoch bis</th><th>wird dünn ab</th><th></th></tr>
   ${profs.map((p, i) => `<tr data-i="${i}"><td><input type="text" class="pn" value="${esc(p.name)}" ${p.is_congregation ? 'readonly' : ''}></td>
     ${['comfort_low', 'comfort_high', 'limit_low', 'limit_high', 'weak_low'].map(k => `<td><input type="text" class="p_${k}" value="${esc(p[k])}"></td>`).join('')}
     <td>${p.is_congregation ? '' : `<button class="small danger" onclick="PROFS.splice(${i},1);saveProfiles(true)">✕</button>`}</td></tr>`).join('')}
   </table><div class="row" style="margin-top:10px"><button onclick="PROFS.push({id:'',name:'Neue Person',comfort_low:'a',comfort_high:\"c''\",limit_low:'g',limit_high:\"d''\",weak_low:'',note:'',is_congregation:false});saveProfiles(false)">Person hinzufügen</button>
   <button class="primary" onclick="saveProfiles(true)">Profile speichern</button></div></div>`;
  loadExtremes().catch(e => { $('#spitzen').innerHTML = '<h3>Spitzentöne prüfen</h3><p class="muted small">' + esc(e.message) + '</p>'; });
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
  tabStimmen();
}

function toggleSinger(r, id, on) { const s = P.song.form[r].singers; if (on && !s.includes(id)) s.push(id); if (!on) P.song.form[r].singers = s.filter(x => x !== id); }
async function saveProfiles(store) {
  document.querySelectorAll('#proftab tr[data-i]').forEach(tr => { const p = PROFS[+tr.dataset.i]; if (!p) return; p.name = $('.pn', tr).value;
    ['comfort_low', 'comfort_high', 'limit_low', 'limit_high', 'weak_low'].forEach(k => p[k] = $('.p_' + k, tr).value.trim()); });
  if (store) { try { await api('/api/stimmprofile', {method: 'PUT', body: PROFS}); } catch (e) { return alert('Ton nicht erkannt: ' + e.message); } }
  tabStimmen();
}

// ---- 5: Erzeugen
function tabErzeugen() {
  const pl = SETTINGS.spieler || [];
  const opt = (k, t, on = true) => `<label class="row" style="color:var(--ink)"><input type="checkbox" id="o_${k}" ${on ? 'checked' : ''}> ${t}</label>`;
  $('#tab').innerHTML = `
  <div class="grid2"><div class="card"><h3>Was soll entstehen?</h3>
    ${opt('chordsheet', 'Chord-Sheet (Band-Partitur)')}${opt('leadsheet', 'Leadsheet (MuseScore + PDF)')}${opt('partitur', 'Partitur mit Streicherstimmen + Einzelstimmen')}
    ${opt('chortext', 'Chor-Textblatt (eine Seite)')}${opt('gitarre', 'Akustikgitarre (A-GIT, mit Griffbildern)')}${opt('klavier', 'Klavier (Chord-Sheet mit Intro-Noten)')}
    ${opt('nebenstimmen', 'Nebenstimmen für Sänger (über/unter der Melodie)', false)}${opt('satb', 'Chorsatz SATB (Refrain/Choral)', false)}
    ${opt('ueben', 'Übe-Audio: Original transponiert + auf euren Ablauf geschnitten')}${opt('uebespuren', 'Übe-Spuren aus MuseScore', false)}
   </div><div class="card"><h3>Einstellungen</h3>
    <p class="small" style="margin-top:0">Ziel: <a href="#" onclick="oeffnen(P.pfad + '/Export');return false">${esc(P.pfad)}/Export</a></p>
    <label>Zusätzlich kopieren in (optional, z. B. Band-Ordner in der Cloud)</label>
    <div class="row" style="flex-wrap:nowrap"><input type="text" style="flex:1" id="o_bandordner" value="${esc(SETTINGS.bandordner || '')}" placeholder="leer = nur im Projekt">
     ${BRIDGE ? `<button class="small" onclick="ordnerWaehlen('o_bandordner','Band-Ordner wählen')">Wählen …</button>` : ''}</div>
    <div class="grid2"><div><label>Bandname (für „…-Ablauf.mp3“)</label><input type="text" id="o_band" value="${esc(SETTINGS.band)}"></div>
    <div><label>MP3-Qualität</label><select id="o_bitrate">${['128', '192', '256', '320', 'V0'].map(b => `<option ${String(SETTINGS.bitrate) === b ? 'selected' : ''} value="${b}">${b === 'V0' ? 'VBR V0 (variabel, höchste)' : b + ' kbit/s'}</option>`).join('')}</select></div></div>
    <label>Streicher (Name · Instrument · Niveau)</label>
    <div id="players">${pl.map((p, i) => playerRow(p, i)).join('')}</div>
    <button class="small" onclick="addPlayer()">+ Spieler</button>
   </div></div>
  <div class="row"><button class="primary" onclick="runExport()">Jetzt erzeugen</button></div>
  <div id="exp"></div>`;
}
function playerRow(p, i) {
  return `<div class="row pl" style="margin:4px 0"><input type="text" class="pl_n" value="${esc(p.name)}" style="width:160px">
   <select class="pl_i">${['Violine', 'Bratsche', 'Violoncello'].map(x => `<option ${x === p.instrument ? 'selected' : ''}>${x}</option>`).join('')}</select>
   <select class="pl_l">${['leicht', 'mittel', 'schwer'].map(x => `<option ${x === p.level ? 'selected' : ''}>${x}</option>`).join('')}</select>
   <button class="small danger" onclick="this.parentElement.remove()">✕</button></div>`;
}
function addPlayer() { $('#players').insertAdjacentHTML('beforeend', playerRow({name: 'Violine 2', instrument: 'Violine', level: 'mittel'}, 0)); }
async function runExport() {
  const ids = ['chordsheet', 'leadsheet', 'partitur', 'chortext', 'gitarre', 'klavier', 'nebenstimmen', 'satb', 'ueben', 'uebespuren'];
  const body = Object.fromEntries(ids.map(k => [k, $('#o_' + k).checked]));
  body.bandordner = $('#o_bandordner').value.trim(); body.band = $('#o_band').value; body.bitrate = $('#o_bitrate').value;
  body.spieler = [...document.querySelectorAll('.pl')].map(r => ({name: $('.pl_n', r).value, instrument: $('.pl_i', r).value, level: $('.pl_l', r).value}));
  SETTINGS = await api('/api/einstellungen', {method: 'PUT', body: {bandordner: body.bandordner, band: body.band, bitrate: body.bitrate, spieler: body.spieler, besetzung: SETTINGS.besetzung}});
  await api(`${pu(P.id)}/erzeugen`, {method: 'POST', body});
  pollExport();
}
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
   <button class="primary" onclick="saveSettings()">Speichern</button></div>`;
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
