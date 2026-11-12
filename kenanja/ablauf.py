"""Ablauf (Reihenfolge der Teile) aus verschiedenen Quellen: wie in der Aufnahme gehört, wie im eingefügten
Liedtext oder wie in einem vorhandenen Leadsheet."""
from __future__ import annotations

from typing import Iterable

from .model import FormItem, Song

QUELLEN = ('audio', 'text', 'leadsheet')


def _key(label: str) -> tuple[str, str]:
    from .importers.musescore import classify_label
    kind, nice, num = classify_label(label or '')
    if kind == 'other':
        nice = (label or '').strip().lower()
        # Zahl am Ende abtrennen ('Teil 2')
        parts = nice.rsplit(' ', 1)
        if len(parts) == 2 and parts[1].isdigit():
            nice, num = parts
    return f'{kind}:{nice.lower()}', num


def form_from_labels(song: Song, labels: Iterable[str], frame: bool = True) -> tuple[list[FormItem], list[str]]:
    """Ordnet jede Bezeichnung (z. B. 'Verse 2', 'Chorus', 'Bridge') einem erkannten Teil zu.
    Bevorzugt den Teil mit gleicher Nummer, sonst das n-te Vorkommen derselben Art, sonst das erste.
    frame: Instrumentales Intro/Outro der Aufnahme bleibt vorn/hinten erhalten, wenn die Quelle keins nennt.
    Rückgabe: (Ablauf, nicht zuordenbare Bezeichnungen)."""
    secs = song.sections
    keys = [_key(s.label) for s in secs]
    seen: dict[str, int] = {}
    form, missing = [], []
    for lab in labels:
        lab = (lab or '').strip()
        if not lab:
            continue
        base, num = _key(lab)
        cands = [i for i, (b, _) in enumerate(keys) if b == base]
        if not cands:
            missing.append(lab)
            continue
        n = seen.get(base, 0)
        seen[base] = n + 1
        pick = None
        if num:
            pick = next((i for i in cands if keys[i][1] == num), None)
        if pick is None:
            pick = cands[n] if n < len(cands) else cands[0]
        form.append(FormItem(section=secs[pick].id, label=lab))
    if frame and secs and form:
        if secs[0].is_instrumental and form[0].section != secs[0].id:
            form.insert(0, FormItem(section=secs[0].id, label=secs[0].label))
        if len(secs) > 1 and secs[-1].is_instrumental and form[-1].section != secs[-1].id:
            form.append(FormItem(section=secs[-1].id, label=secs[-1].label))
    return form, missing


def text_labels(song: Song) -> list[str]:
    return list(song.notes.get('analyse', {}).get('textbloecke') or [])


def leadsheet_labels(path) -> list[str]:
    """Teile in der Reihenfolge des Leadsheets (MuseScore), Wiederholungen ausgeschrieben."""
    from .importers.musescore import import_musescore
    other = import_musescore(path)
    return [f.label or other.section(f.section).label for f in other.form for _ in range(max(1, f.repeat))]


def apply_source(song: Song, quelle: str, labels: list[str] | None = None) -> list[str]:
    """Setzt song.form nach der gewählten Quelle. Gibt nicht zuordenbare Teile zurück."""
    if quelle == 'audio' or not song.sections:
        song.default_form()
        song.notes['ablauf_quelle'] = 'audio'
        return []
    if quelle == 'text':
        labels = text_labels(song)
    form, missing = form_from_labels(song, labels or [])
    if not form:
        song.default_form()
        song.notes['ablauf_quelle'] = 'audio'
        return missing or list(labels or [])
    song.form = form
    song.notes['ablauf_quelle'] = quelle
    if quelle == 'leadsheet':
        song.notes['leadsheet_ablauf'] = list(labels or [])
    return missing
