"""Stimmprofile, Tonumfang-Analyse und Tonart-Vorschlag.

* Ein Stimmprofil beschreibt, was eine Person (oder die Gemeinde) gut singen kann:
  Wohlfühlbereich, Grenztöne, die ausnahmsweise gehen, und eine Schwachstelle unten.
  Die Töne werden so angegeben, wie sie im Leadsheet (Violinschlüssel) stehen – Männer
  singen das bekanntlich eine Oktave tiefer, das spielt für den Vergleich keine Rolle.
* Die Analyse zählt für jeden gesungenen Ton, wie oft und wie lange er vorkommt.
* Der Tonart-Vorschlag probiert alle Verschiebungen (–6 … +6 Halbtöne) und bewertet sie für
  die eingetragene Rollenverteilung (wer singt welchen Teil, wo singt die Gemeinde mit).
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from fractions import Fraction as F
from pathlib import Path
from typing import Optional

from .model import Song, iter_sung_notes
from .theory import (best_key_for_shift, helmholtz, key_name, key_name_de, parse_helmholtz, spell_pitch,
                     tpc_pc)


@dataclass
class VoiceProfile:
    id: str
    name: str
    comfort_low: str = 'a'          # Wohlfühlbereich (klingend, deutsche Schreibweise)
    comfort_high: str = "c''"
    limit_low: str = 'g'            # geht ausnahmsweise
    limit_high: str = "cis''"
    weak_low: str = ''              # ab hier (und tiefer) wird es dünn, z. B. 'g'
    note: str = ''
    is_congregation: bool = False

    def m(self, attr) -> Optional[int]:
        v = getattr(self, attr)
        return parse_helmholtz(v) if v else None


CONGREGATION = VoiceProfile('gemeinde', 'Gemeinde', comfort_low='a', comfort_high="c''", limit_low='g',
                            limit_high="cis''", note='Spitzenton c\'\', cis\'\' nur ausnahmsweise',
                            is_congregation=True)


def config_dir() -> Path:
    base = os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')
    p = Path(base) / 'kenanja'
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_profiles(path=None) -> dict[str, VoiceProfile]:
    path = Path(path or config_dir() / 'stimmprofile.json')
    profiles = {CONGREGATION.id: CONGREGATION}
    if path.exists():
        for d in json.loads(path.read_text(encoding='utf-8')):
            vp = VoiceProfile(**d)
            profiles[vp.id] = vp
    return profiles


def save_profiles(profiles: dict[str, VoiceProfile], path=None) -> Path:
    path = Path(path or config_dir() / 'stimmprofile.json')
    path.write_text(json.dumps([asdict(p) for p in profiles.values() if not p.is_congregation],
                               ensure_ascii=False, indent=1), encoding='utf-8')
    return path


# ------------------------------------------------------------------ Tonumfang ----

@dataclass
class PitchStat:
    pitch: int
    tpc: Optional[int]
    count: int = 0
    beats: F = F(0)
    longest: F = F(0)
    sections: set = field(default_factory=set)

    @property
    def name(self) -> str:
        return helmholtz(self.pitch, self.tpc)


def ambitus(song: Song, shift: int = 0, section_filter=None) -> dict[int, PitchStat]:
    """Statistik der gesungenen Töne (optional verschoben)."""
    stats: dict[int, PitchStat] = {}
    old_tpc, _ = song.key_tpc()
    new_tpc = best_key_for_shift(old_tpc, shift)
    for item, sec, bi, n in iter_sung_notes(song, section_filter):
        p = n.pitch + shift
        tpc = (n.tpc + new_tpc - old_tpc) if n.tpc is not None else spell_pitch(p, new_tpc)
        st = stats.setdefault(p, PitchStat(p, tpc))
        if n.lyrics:
            st.count += 1
        st.beats += n.dur
        st.longest = max(st.longest, n.dur)
        st.sections.add(item.label or sec.label)
    return stats


def ambitus_report(song: Song, shift: int = 0, section_filter=None) -> dict:
    stats = ambitus(song, shift, section_filter)
    if not stats:
        return {}
    ps = sorted(stats)
    total = sum(s.beats for s in stats.values())
    acc, lo, hi = F(0), None, None
    for p in ps:
        acc += stats[p].beats
        if lo is None and acc >= total / 10:
            lo = p
        if hi is None and acc >= total * 9 / 10:
            hi = p

    def row(p):
        s = stats[p]
        return {'ton': s.name, 'midi': p, 'anzahl': s.count, 'schlaege': float(s.beats),
                'laengster': float(s.longest), 'teile': sorted(s.sections)}
    return {
        'tiefster': stats[ps[0]].name, 'hoechster': stats[ps[-1]].name, 'halbtoene': ps[-1] - ps[0],
        'hauptlage': f'{stats[lo].name} – {stats[hi].name}',
        'hoechste': [row(p) for p in ps[::-1][:3]],
        'tiefste': [row(p) for p in ps[:3]],
    }


# ------------------------------------------------------------------ Tonart-Vorschlag ----

GUITAR_KEYS = {'C', 'G', 'D', 'A', 'E', 'Am', 'Em', 'Dm'}


def guitar_hint(key: str) -> str:
    if key in GUITAR_KEYS:
        return 'liegt gut für Gitarre'
    minor = key.endswith('m')
    from .theory import key_tpc_from_name
    tpc, _ = key_tpc_from_name(key)
    for capo in range(1, 6):
        cand = key_name(best_key_for_shift(tpc, -capo), minor)
        if cand in GUITAR_KEYS:
            return f'Gitarre: Kapo {capo}, Griffe in {cand}'
    return 'für Gitarre eher unbequem'


@dataclass
class Issue:
    who: str
    section: str
    text: str
    weight: float


def _notes_by_role(song: Song, profiles: dict[str, VoiceProfile], default_singers=()):
    """Sammelt pro Profil die gesungenen Noten (ohne Verschiebung)."""
    roles = defaultdict(list)  # profil-id -> [(pitch, dur, is_syllable, section_label)]
    for item, sec, bi, n in iter_sung_notes(song):
        label = item.label or sec.label
        singers = item.singers or list(default_singers)
        for sid in singers:
            if sid in profiles:
                roles[sid].append((n.pitch, n.dur, bool(n.lyrics), label))
        if item.congregation and item.choir != 'solo' and 'gemeinde' in profiles:
            roles['gemeinde'].append((n.pitch, n.dur, bool(n.lyrics), label))
    return roles


def _best_octave(notes, vp: VoiceProfile, shift: int) -> int:
    lo, hi = vp.m('comfort_low'), vp.m('comfort_high')
    center = (lo + hi) / 2
    tot = sum(float(d) for _, d, _, _ in notes) or 1
    mean = sum((p + shift) * float(d) for p, d, _, _ in notes) / tot
    best = min((-24, -12, 0, 12), key=lambda o: abs(mean + o - center))
    return best


def evaluate_shift(song: Song, shift: int, profiles: dict[str, VoiceProfile], default_singers=()) -> dict:
    roles = _notes_by_role(song, profiles, default_singers)
    issues: list[Issue] = []
    score = 0.0
    for sid, notes in roles.items():
        vp = profiles[sid]
        if not notes:
            continue
        octv = 0  # Profile und Leadsheet in derselben (notierten) Lage
        cl, ch = vp.m('comfort_low'), vp.m('comfort_high')
        ll, lh = vp.m('limit_low') or cl - 2, vp.m('limit_high') or ch + 1
        wl = vp.m('weak_low')
        per = defaultdict(lambda: defaultdict(lambda: [0, F(0), F(0)]))   # pitch -> section -> [anzahl, schläge, längster]
        for p, d, syl, label in notes:
            q = p + shift + octv
            per[q][label][0] += 1 if syl else 0
            per[q][label][1] += d
            per[q][label][2] = max(per[q][label][2], d)
        for q, secs in per.items():
            cnt = sum(v[0] for v in secs.values()) or 1
            beats = float(sum(v[1] for v in secs.values()))
            longest = float(max(v[2] for v in secs.values()))
            where = ', '.join(sorted(secs))
            name = helmholtz(q, spell_pitch(q, best_key_for_shift(song.key_tpc()[0], shift)))
            if q > lh:
                w = 40 * (q - lh) * (cnt + beats)
                issues.append(Issue(vp.name, where, f"{name} {cnt}× – zu hoch (Grenze {vp.limit_high})", w))
            elif q > ch:
                w = 3 * (q - ch) * (cnt + 2 * beats + 2 * longest)
                kind = 'kurz' if longest <= 1 else f'bis {longest:g} Schläge gehalten'
                issues.append(Issue(vp.name, where, f"{name} {cnt}× ({kind}) – über dem Wohlfühlbereich", w))
            if q < ll:
                w = 30 * (ll - q) * (cnt + beats)
                issues.append(Issue(vp.name, where, f"{name} {cnt}× – zu tief (Grenze {vp.limit_low})", w))
            elif q < cl:
                w = 2 * (cl - q) * (cnt + beats)
                issues.append(Issue(vp.name, where, f"{name} {cnt}× – unter dem Wohlfühlbereich", w))
            if wl is not None and q <= wl and q >= ll:
                w = 2.5 * (cnt + beats) * (1 + wl - q)
                issues.append(Issue(vp.name, where, f"{name} {cnt}× – da wird es dünn", w))
        score += sum(i.weight for i in issues if i.who == vp.name)
    # leichte Bevorzugung kleiner Verschiebungen
    score += abs(shift) * 0.5
    old_tpc, minor = song.key_tpc()
    key = key_name(best_key_for_shift(old_tpc, shift), minor)
    return {'shift': shift, 'key': key, 'key_de': key_name_de(best_key_for_shift(old_tpc, shift), minor),
            'score': round(score, 1), 'issues': sorted(issues, key=lambda i: -i.weight),
            'guitar': guitar_hint(key)}


def suggest_keys(song: Song, profiles: dict[str, VoiceProfile], default_singers=(), top: int = 4) -> list[dict]:
    res = [evaluate_shift(song, s, profiles, default_singers) for s in range(-6, 7)]
    res.sort(key=lambda r: r['score'])
    return res[:top]


def suggest_swaps(song: Song, shift: int, profiles: dict[str, VoiceProfile]) -> list[str]:
    """Prüft für jeden Teil, ob eine andere Person ihn in dieser Tonart besser singen würde."""
    hints = []
    singers = [p for p in profiles.values() if not p.is_congregation]
    for k, item in enumerate(song.form):
        if not item.singers:
            continue
        sec = song.section(item.section)
        sub = Song(key=song.key, sections=song.sections, form=[item])

        def cost(sid):
            it2 = type(item)(**{**item.__dict__, 'singers': [sid], 'congregation': False})
            sub.form = [it2]
            return evaluate_shift(sub, shift, profiles)['score'] - abs(shift) * 0.5
        cur = sum(cost(s) for s in item.singers)
        best = min(singers, key=lambda vp: cost(vp.id)) if singers else None
        if best and best.id not in item.singers and cost(best.id) + 5 < cur:
            names = ', '.join(profiles[s].name for s in item.singers if s in profiles)
            hints.append(f"{item.label or sec.label}: liegt für {names} ungünstig – {best.name} würde besser passen.")
    return hints


def format_suggestions(results: list[dict]) -> str:
    out = []
    for r in results:
        head = f"{r['key_de']} ({'+' if r['shift'] > 0 else ''}{r['shift']} Halbtöne) – {r['guitar']}"
        out.append(head)
        if not r['issues']:
            out.append('   passt für alle')
        for i in r['issues'][:4]:
            out.append(f"   {i.who}: {i.text} – {i.section}")
    return '\n'.join(out)
