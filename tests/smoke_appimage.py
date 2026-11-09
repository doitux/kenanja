#!/usr/bin/env python3
"""Ende-zu-Ende-Test einer gebauten AppImage (nur Standardbibliothek + ffmpeg nötig).

    python3 tests/smoke_appimage.py dist/Kenanja-0.2.0-x86_64.AppImage [--gui]

Erzeugt ein kurzes Musikstück, startet die AppImage als Server, legt ein Projekt an (Analyse mit den echten
Modellen), erzeugt Chord-Sheet, Chorblatt, Gitarrenblatt und Übe-MP3 und prüft die Ordnerstruktur.
Mit --gui öffnet es zusätzlich das Fenster (braucht einen Bildschirm, z. B. xvfb-run) und macht ein Bildschirmfoto.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


def make_song(path):
    # 32 Takte bei 90 BPM: G – D – Em – C als Dreiklänge mit Bass, darüber eine einfache „Gesangs“-Linie
    chords = [(196.0, 246.9, 293.7, 98.0), (146.8, 185.0, 220.0, 73.4), (164.8, 196.0, 246.9, 82.4),
              (130.8, 164.8, 196.0, 65.4)]
    bar = 4 * 60 / 90
    parts = []
    for k, (a, b, c, bass) in enumerate(chords):
        t0 = k * bar
        on = f"between(mod(t,{4 * bar}),{t0},{t0 + bar})"
        parts.append(f"{on}*(0.18*sin(2*PI*{a}*t)+0.15*sin(2*PI*{b}*t)+0.15*sin(2*PI*{c}*t)+0.3*sin(2*PI*{bass}*t))")
    beat = f"0.4*sin(2*PI*55*t)*exp(-30*mod(t,{60 / 90}))"
    expr = "+".join(parts) + "+" + beat
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"aevalsrc='{expr}|{expr}':s=44100:d={8 * 4 * bar}", "-c:a", "libmp3lame", "-b:a", "192k",
                    str(path)], check=True)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    app = Path(sys.argv[1]).resolve()
    work = Path(tempfile.mkdtemp(prefix="kenanja-smoke-"))
    env = dict(os.environ, APPIMAGE_EXTRACT_AND_RUN="1", HOME=str(work / "home"))
    (work / "home").mkdir()
    out = subprocess.run([str(app), "--version"], env=env, capture_output=True, text=True, timeout=180)
    print("version:", out.stdout.strip(), out.stderr.strip()[-300:])
    assert out.returncode == 0 and "Kenanja" in out.stdout
    out = subprocess.run([str(app), "pruefen"], env=env, capture_output=True, text=True, timeout=900)
    print(out.stdout)
    assert "btc_large_voca" in out.stdout and ".onnx" in out.stdout, out.stderr[-2000:]
    assert "Spracherkennung: verfügbar" in out.stdout, out.stdout
    out = subprocess.run([str(app), "sprachmodell", "--test"], env=env, capture_output=True, text=True, timeout=1200)
    print(out.stdout[-500:], out.stderr[-1500:])
    assert "Test ok" in out.stdout

    song = work / "probe.mp3"
    make_song(song)
    port = free_port()
    log = open(work / "server.log", "w")
    p = subprocess.Popen([str(app), "web", "--kein-browser", "--port", str(port)], env=env, stdout=log,
                         stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"

    def req(path, data=None, method=None, headers=None):
        r = urllib.request.Request(base + path, data=data, method=method, headers=headers or {})
        with urllib.request.urlopen(r, timeout=300) as resp:
            return json.loads(resp.read())

    try:
        for _ in range(120):
            try:
                req("/api/einstellungen")
                break
            except Exception:
                time.sleep(1)
        b = uuid.uuid4().hex
        fields = {"titel": "Probe: Lied/1", "autoren": "Test", "text": ""}
        body = b"".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                        for k, v in fields.items())
        body += (f'--{b}\r\nContent-Disposition: form-data; name="audio"; filename="probe.mp3"\r\n'
                 f'Content-Type: audio/mpeg\r\n\r\n').encode() + song.read_bytes() + f"\r\n--{b}--\r\n".encode()
        pid = req("/api/projekte", body, "POST", {"Content-Type": f"multipart/form-data; boundary={b}"})["id"]
        print("Projekt:", pid)
        q = "/api/projekte/" + urllib.parse.quote(pid)
        t0 = time.time()
        while True:
            d = req(q)
            st = (d.get("job") or {}).get("status") or d["meta"].get("status")
            if st != "analyse":
                break
            assert time.time() - t0 < 1200, "Analyse dauert zu lange"
            time.sleep(3)
        assert st == "fertig", d["meta"].get("fehler")
        print("Bericht:", json.dumps(d["meta"].get("bericht"), ensure_ascii=False)[:400])
        chords = [c for s in d["song"]["sections"] for c in s.get("bar_chords", [])]
        print("Akkorde:", chords[:12])
        opts = {"chordsheet": True, "chortext": True, "gitarre": True, "ueben": True, "leadsheet": False,
                "partitur": False, "klavier": False, "bitrate": "192"}
        req(q + "/erzeugen", json.dumps(opts).encode(), "POST", {"Content-Type": "application/json"})
        while True:
            j = req(q + "/erzeugen")
            if j["status"] != "läuft":
                break
            time.sleep(2)
        assert j["status"] == "fertig", j
        res = j["ergebnis"]
        print("Export:", json.dumps(res, ensure_ascii=False)[:600])
        folder = Path(res["ordner"])
        assert folder.name == "Export" and folder.parent.name == pid, folder
        assert (folder.parent / "Original" / "probe.mp3").exists()
        assert any(folder.glob("*Chordsheet.odt")) and any(folder.glob("*Ablauf.mp3")), list(folder.iterdir())
    finally:
        p.terminate()
        p.wait(10)
        log.close()
        print((work / "server.log").read_text()[-3000:])

    if "--gui" in sys.argv:
        shot = os.environ.get("SMOKE_SCREENSHOT") or str(work / "window.png")
        genv = dict(env, KENANJA_SCREENSHOT=shot, KENANJA_SCREENSHOT_MS="9000",
                    KENANJA_JS=f"openProject({json.dumps(pid)}); 1")
        r = subprocess.run([str(app)], env=genv, capture_output=True, text=True, timeout=240)
        print(r.stdout[-1500:], r.stderr[-1500:])
        assert Path(shot).exists() and Path(shot).stat().st_size > 20000, "kein Bildschirmfoto vom Fenster"
        print("Fenster-Bildschirmfoto:", shot)
    print("OK")


if __name__ == "__main__":
    main()
