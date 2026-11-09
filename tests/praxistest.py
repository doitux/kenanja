#!/usr/bin/env python3
"""Praxistest: bedient Kenanja über dieselbe Schnittstelle wie die Oberfläche – vom Anlegen des Projekts
bis zum fertigen Export – mit frischem Home-Ordner (keine Zwischenspeicher, Modelle werden geladen).

    python3 tests/praxistest.py AUDIO TEXTDATEI [--app "/pfad/Kenanja.AppImage"] [--home DIR]

Ohne --app wird Kenanja aus dem Quellcode gestartet (python3 -m kenanja). Bricht bei jedem Fehler ab
und zeigt das Server-Protokoll.
"""
import argparse
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Client:
    def __init__(self, base):
        self.base = base

    def req(self, path, data=None, method=None, headers=None, raw=False, timeout=600):
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            raise AssertionError(f"{method or 'GET'} {path} -> {e.code}: {e.read()[:500]!r}")
        return body if raw else json.loads(body)

    def json(self, path, obj, method="POST"):
        return self.req(path, json.dumps(obj).encode(), method, {"Content-Type": "application/json"})


def step(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("text")
    ap.add_argument("--app", default="")
    ap.add_argument("--home", default="")
    ap.add_argument("--titel", default="Praxistest")
    a = ap.parse_args()
    home = Path(a.home or tempfile.mkdtemp(prefix="kenanja-praxis-"))
    home.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, HOME=str(home), APPIMAGE_EXTRACT_AND_RUN="1")
    env.pop("XDG_CONFIG_HOME", None)
    env.pop("XDG_CACHE_HOME", None)
    cmd = shlex.split(a.app) if a.app else [sys.executable, "-m", "kenanja"]
    if not a.app:
        env["PYTHONPATH"] = str(ROOT)
    port = free_port()
    log_path = home / "server.log"
    log = open(log_path, "w")
    srv = subprocess.Popen(cmd + ["web", "--kein-browser", "--port", str(port)], env=env, stdout=log,
                           stderr=subprocess.STDOUT, cwd=str(home))
    c = Client(f"http://127.0.0.1:{port}")
    ok = False
    t_start = time.time()
    try:
        for _ in range(180):
            try:
                c.req("/api/einstellungen")
                break
            except Exception:
                time.sleep(1)
        st = c.req("/api/einstellungen")
        step(f"Server läuft, Projektordner {st['projektordner']}")

        # 1. Projekt anlegen (wie das Formular „Neuer Song“)
        b = uuid.uuid4().hex
        audio = Path(a.audio)
        fields = {"titel": a.titel, "autoren": "Test Autor", "text": Path(a.text).read_text(encoding="utf-8")}
        body = b"".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                        for k, v in fields.items())
        body += (f'--{b}\r\nContent-Disposition: form-data; name="audio"; filename="{audio.name}"\r\n'
                 f'Content-Type: application/octet-stream\r\n\r\n').encode() + audio.read_bytes() + f"\r\n--{b}--\r\n".encode()
        pid = c.req("/api/projekte", body, "POST", {"Content-Type": f"multipart/form-data; boundary={b}"})["id"]
        q = "/api/projekte/" + urllib.parse.quote(pid)
        step(f"Projekt angelegt: {pid}")

        # 2. Analyse abwarten (wie die Fortschrittsanzeige)
        last = None
        while True:
            d = c.req(q)
            job = d.get("job") or {}
            stt = job.get("status") or d["meta"].get("status")
            msg = job.get("schritt") or job.get("meldung")
            if msg and msg != last:
                step(f"  Analyse: {msg}")
                last = msg
            if stt != "analyse":
                break
            time.sleep(3)
        assert stt == "fertig", f"Analyse fehlgeschlagen: {d['meta'].get('fehler') or job}"
        song = d["song"]
        rep = d["meta"].get("bericht") or {}
        step(f"Analyse fertig nach {time.time() - t_start:.0f} s: Tonart {song['key']}, Tempo {song['tempo']}, "
             f"Takt {rep.get('takt')}, {len(song['sections'])} Teile, Zeilen {rep.get('zeilen')}")
        if rep.get("warnung"):
            step(f"  Hinweis der Analyse: {rep['warnung']}")
        assert len(song["sections"]) >= 2, "zu wenige Formteile"
        assert song["form"], "kein Ablauf"
        sung = sum(1 for s in song["sections"] if s.get("text"))
        assert sung >= 2, "kein Text zugeordnet"

        # 3. Bearbeiten wie in Tab 1/2: Titel ändern (Ordner wird umbenannt), Ablauf speichern, Akkorde zurückschreiben
        r = c.json(q + "/song", {"title": a.titel + " (bearbeitet)", "form": song["form"],
                                  "sections": [{"id": s["id"], "label": s["label"], "kind": s["kind"],
                                                "bar_chords": s["bar_chords"], "text": s["text"]} for s in song["sections"]]},
                   "PUT")
        pid = r.get("id", pid)
        q = "/api/projekte/" + urllib.parse.quote(pid)
        step(f"Song gespeichert, Projekt heißt jetzt {pid}")
        sid = song["sections"][0]["id"]
        c.req(q + "/abschnitt-kopieren/" + urllib.parse.quote(sid), b"", "POST")
        d = c.req(q)
        assert len(d["song"]["sections"]) == len(song["sections"]) + 1, "Abschnitt kopieren hat nichts angelegt"
        step("Abschnitt kopiert")

        # 4. Vorschauen
        for art in ("chordsheet", "chortext", "gitarre"):
            pdf = c.req(f"{q}/vorschau/{art}", raw=True)
            assert pdf[:4] == b"%PDF", f"Vorschau {art} ist kein PDF"
            step(f"Vorschau {art}: {len(pdf) // 1024} KB")

        # 5. Stimmen & Tonart
        k = c.req(q + "/tonart")
        assert k["vorschlaege"], "keine Tonart-Vorschläge"
        u = k["umfang"]
        step(f"Umfang {u.get('tiefster')}–{u.get('hoechster')}, Vorschläge: "
             + ", ".join(v["tonart"] for v in k["vorschlaege"][:3]))
        x = c.req(q + "/spitzentoene")
        assert x["hoch"] and x["tief"], "keine Spitzentöne"
        o = x["hoch"][0]["stellen"][0]
        wav = c.req(f"{q}/schnipsel?t0={o['t0']}&t1={o['t1']}", raw=True)
        assert wav[:4] == b"RIFF" and len(wav) > 10000, "Schnipsel leer"
        c.json(q + "/ton", {"sid": o["sid"], "takt": o["takt"], "idx": o["idx"], "unsicher": True}, "PUT")
        c.json(q + "/ton", {"sid": o["sid"], "takt": o["takt"], "idx": o["idx"], "unsicher": False}, "PUT")
        step(f"Spitzentöne: hoch {[g['ton'] for g in x['hoch']]}, tief {[g['ton'] for g in x['tief']]}, Schnipsel {len(wav) // 1024} KB")
        best = k["vorschlaege"][0]["shift"]
        c.json(q + "/song", {"transpose": best}, "PUT")
        step(f"Zieltonart übernommen ({best:+d})")

        # 6. Alles erzeugen
        opts = {"chordsheet": True, "leadsheet": True, "partitur": True, "chortext": True, "gitarre": True,
                "klavier": True, "nebenstimmen": True, "satb": True, "ueben": True, "uebespuren": True,
                "bitrate": "256", "band": "Band"}
        c.json(q + "/erzeugen", opts)
        last = None
        while True:
            j = c.req(q + "/erzeugen")
            lg = (j.get("log") or [])[-1:] if isinstance(j.get("log"), list) else []
            if lg and lg[-1] != last:
                step(f"  Export: {lg[-1]}")
                last = lg[-1]
            if j["status"] != "läuft":
                break
            time.sleep(3)
        assert j["status"] == "fertig", f"Export fehlgeschlagen: {json.dumps(j, ensure_ascii=False)[:1500]}"
        res = j["ergebnis"]
        files = [f for v in res.get("dateien", {}).values() for f in v]
        step(f"Export fertig: {len(files)} Dateien in {res['ordner']}")
        for f in files:
            step(f"   {Path(f).name}  ({Path(f).stat().st_size // 1024} KB)")
        if res.get("fehler"):
            step(f"  Meldungen: {res['fehler']}")
        names = " ".join(Path(f).name for f in files)
        for need in ("Chordsheet.pdf", "Chortext", "Ablauf.mp3", ".mscz"):
            assert need in names, f"{need} fehlt im Export"
        ok = True
        step(f"ALLES OK in {time.time() - t_start:.0f} s")
    finally:
        srv.terminate()
        try:
            srv.wait(15)
        except subprocess.TimeoutExpired:
            srv.kill()
        log.close()
        text = log_path.read_text(errors="replace")
        if not ok or "Traceback" in text:
            print("----- Server-Protokoll -----")
            print(text[-6000:])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
