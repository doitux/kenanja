"""Das Song-Datenmodell von Kenanja.

Ein Song besteht aus
  * Abschnitten (Section): jedes Vorkommen im Original (Verse 1, Chorus 1, Chorus 2 ...)
    mit Takten, Akkorden, Melodie und Text, plus Zeitstempeln im Original-Audio;
  * dem Ablauf (form): der Reihenfolge, in der IHR den Song spielt. Ein Eintrag verweist auf
    einen Abschnitt – auch mehrfach – und trägt Besetzung, Sänger, Chor- und Streicheranweisungen.

Zeiten innerhalb eines Takts sind Brüche in Viertelnoten (Fraction).
Alles lässt sich als song.json speichern und wieder laden.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field, asdict
from fractions import Fraction
from pathlib import Path
from typing import Optional

from .theory import ChordSymbol, best_key_for_shift, key_tpc_from_name, key_name, spell_pitch, tpc_pc

SECTION_KINDS = ['intro', 'verse', 'prechorus', 'chorus', 'bridge', 'interlude', 'solo', 'outro',
                 'tag', 'choral', 'ending', 'other']

# übliche Kürzel auf Chord-Sheets
BAND_INSTRUMENTS = ['Keys', 'Synth', 'Piano', 'AGit', 'EGit', 'LEGit', 'REGit', 'Bass', 'Drums',
                    'LVoc', 'BVoc', '2Voc', 'Strings', 'Violin', 'Cello', 'Orgel']


@dataclass
class Note:
    onset: Fraction                  # Viertel ab Taktanfang
    dur: Fraction                    # Viertel
    pitch: Optional[int] = None      # MIDI, None = Pause
    tpc: Optional[int] = None        # Schreibweise
    lyrics: dict = field(default_factory=dict)   # {Strophe(int): (Silbe, syllabic)}
    tie: bool = False                # an nächste Note gebunden
    extra: list = field(default_factory=list)    # weitere Töne (Doppelgriffe): [(pitch, tpc)]
    unsicher: bool = False           # Erkennung zweifelhaft (z. B. kurzer Ausreißer) – zählt kaum für die Tonart

    @property
    def is_rest(self) -> bool:
        return self.pitch is None

    def syllable(self, verse: int = 0) -> Optional[tuple[str, str]]:
        return self.lyrics.get(verse) or self.lyrics.get(0)


@dataclass
class ChordEvent:
    onset: Fraction
    symbol: str

    @property
    def chord(self) -> ChordSymbol:
        return ChordSymbol.parse(self.symbol)


@dataclass
class Bar:
    num: int = 4
    den: int = 4
    chords: list[ChordEvent] = field(default_factory=list)
    melody: list[Note] = field(default_factory=list)
    parts: dict = field(default_factory=dict)    # weitere Stimmen: {'Violine': [Note...], ...}
    texts: list = field(default_factory=list)    # Spielanweisungen im Takt
    audio_start: Optional[float] = None          # Sekunden im Original
    key: Optional[int] = None                    # Tonartwechsel ab diesem Takt (Vorzeichen: +Kreuze/-Bs)

    @property
    def length(self) -> Fraction:
        return Fraction(self.num * 4, self.den)


@dataclass
class Section:
    id: str
    label: str
    kind: str = 'other'
    bars: list[Bar] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)   # Liedtextzeilen (für Chord-/Textblätter)
    line_starts: list = field(default_factory=list)  # Silben-Nr. (lyrics.syllables) des Zeilenanfangs
    audio_start: Optional[float] = None
    audio_end: Optional[float] = None
    verse: int = 0                                  # welche Liedtext-Ebene (Strophe) die Noten tragen
    pickup: int = 0          # so viele Noten am Ende des vorigen Abschnitts gehören als Auftakt hierher
    spill: int = 0           # so viele Noten am Anfang gehören noch zum vorigen Abschnitt (Überhang)

    @property
    def is_instrumental(self) -> bool:
        return not any(n.lyrics for b in self.bars for n in b.melody)


@dataclass
class FormItem:
    section: str                       # Section.id
    label: str = ''                    # Anzeige im Ablauf, z. B. 'Chorus 3'
    add: list[str] = field(default_factory=list)      # +Instrumente
    remove: list[str] = field(default_factory=list)   # -Instrumente
    note: str = ''                     # z. B. 'LEGit-Solo', 'Solo-Violin'
    singers: list[str] = field(default_factory=list)  # Stimmprofil-IDs
    congregation: bool = True          # Gemeinde singt mit
    choir: str = 'sing'                # sing | solo (nicht mitsingen) | tacet
    choir_cue: str = ''                # Ausdruck, z. B. 'etwas lauter (dankbar)'
    choir_dyn: str = ''                # soft | loud | '' -> Farbe rot/grün
    strings: dict = field(default_factory=dict)       # {'Violine': 'play'|'tacet'|'solo'|'pad'|'counter'}
    repeat: int = 1                    # wie oft hintereinander
    audio_from: Optional[str] = None   # Abschnitt, dessen Audio verwendet wird (Standard: section)


@dataclass
class Song:
    title: str = ''
    subtitle: str = ''
    authors: str = ''
    copyright: str = ''
    version: str = '1.0'
    key: str = 'C'                     # Originaltonart, z. B. 'G', 'F#m'
    transpose: int = 0                 # Halbtöne gegenüber dem Original
    tempo: float = 80.0
    sections: list[Section] = field(default_factory=list)
    form: list[FormItem] = field(default_factory=list)
    lineup: list[str] = field(default_factory=list)   # Besetzung insgesamt
    audio: Optional[str] = None
    notes: dict = field(default_factory=dict)         # freie Zusatzinfos

    # ---- Zugriff ----
    def section(self, sid: str) -> Section:
        for s in self.sections:
            if s.id == sid:
                return s
        raise KeyError(sid)

    def key_tpc(self) -> tuple[int, bool]:
        return key_tpc_from_name(self.key)

    def target_key(self) -> str:
        tpc, minor = self.key_tpc()
        return key_name(best_key_for_shift(tpc, self.transpose), minor)

    def ordered_form(self) -> list[tuple[FormItem, Section]]:
        out = []
        for item in self.form:
            for _ in range(max(1, item.repeat)):
                out.append((item, self.section(item.section)))
        return out

    def default_form(self) -> None:
        self.form = [FormItem(section=s.id, label=s.label) for s in self.sections]

    # ---- Transposition ----
    def transposed(self, semitones: Optional[int] = None) -> 'Song':
        """Kopie in der Zieltonart (semitones gegenüber Original; Standard: self.transpose)."""
        st = self.transpose if semitones is None else semitones
        new = copy.deepcopy(self)
        if st == 0:
            new.transpose = 0
            return new
        old_tpc, minor = self.key_tpc()
        new_tpc = best_key_for_shift(old_tpc, st)
        d = new_tpc - old_tpc
        for sec in new.sections:
            for bar in sec.bars:
                if bar.key is not None:
                    k2 = bar.key + d
                    bar.key = k2 - 12 if k2 > 7 else (k2 + 12 if k2 < -7 else k2)
                for ce in bar.chords:
                    try:
                        ce.symbol = ChordSymbol.parse(ce.symbol).transposed(d).text()
                    except ValueError:
                        pass
                for notes in [bar.melody] + list(bar.parts.values()):
                    for n in notes:
                        if n.pitch is not None:
                            n.pitch += st
                            n.tpc = (n.tpc + d) if n.tpc is not None else spell_pitch(n.pitch, new_tpc)
                        n.extra = [(p + st, (t + d) if t is not None else None) for p, t in n.extra]
        new.key = key_name(new_tpc, minor)
        new.transpose = 0
        new.notes = dict(new.notes, original_key=self.key, transposed_by=st)
        return new

    # ---- Speichern / Laden ----
    def to_json(self) -> str:
        return json.dumps(_encode(asdict(self)), ensure_ascii=False, indent=1)

    def save(self, path) -> None:
        Path(path).write_text(self.to_json(), encoding='utf-8')

    @classmethod
    def from_json(cls, text: str) -> 'Song':
        d = json.loads(text)
        secs = []
        for s in d.pop('sections', []):
            bars = []
            for b in s.pop('bars', []):
                b['chords'] = [ChordEvent(Fraction(c['onset']), c['symbol']) for c in b.get('chords', [])]
                b['melody'] = [_note(n) for n in b.get('melody', [])]
                b['parts'] = {k: [_note(n) for n in v] for k, v in b.get('parts', {}).items()}
                bars.append(Bar(**b))
            s['line_starts'] = [int(x) for x in s.get('line_starts', [])]
            secs.append(Section(bars=bars, **s))
        form = [FormItem(**f) for f in d.pop('form', [])]
        return cls(sections=secs, form=form, **d)

    @classmethod
    def load(cls, path) -> 'Song':
        return cls.from_json(Path(path).read_text(encoding='utf-8'))


def _note(n: dict) -> Note:
    n = dict(n)
    n['onset'] = Fraction(n['onset'])
    n['dur'] = Fraction(n['dur'])
    n['lyrics'] = {int(k): tuple(v) for k, v in n.get('lyrics', {}).items()}
    n['extra'] = [tuple(x) for x in n.get('extra', [])]
    return Note(**n)


def _encode(o):
    if isinstance(o, Fraction):
        return str(o)
    if isinstance(o, dict):
        return {str(k): _encode(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_encode(v) for v in o]
    return o


# ---- Hilfen für Auswertungen ----

def iter_sung_notes(song: Song, section_filter=None):
    """Alle gesungenen Melodienoten (mit Text oder als Haltebogen-Fortsetzung) im Ablauf.

    Liefert (FormItem, Section, Bar-Index, Note, Strophe).
    """
    for item, sec in song.ordered_form():
        if section_filter and not section_filter(item, sec):
            continue
        tied = None      # Tonhöhe, die per Haltebogen weiterklingt
        for bi, bar in enumerate(sec.bars):
            for n in bar.melody:
                if n.pitch is None:
                    tied = None
                    continue
                if n.lyrics or tied == n.pitch:
                    yield item, sec, bi, n
                    tied = n.pitch if n.tie else None
                else:
                    tied = None


def chord_at(bar: Bar, onset: Fraction, prev: Optional[str] = None) -> Optional[str]:
    cur = prev
    for c in bar.chords:
        if c.onset <= onset:
            cur = c.symbol
    return cur


def lineup_states(song: 'Song', start: Optional[set] = None) -> list[set]:
    """Welche Instrumente spielen in welchem Ablauf-Eintrag? (aus den +/- Angaben)"""
    state = set(start or ())
    out = []
    for item in song.form:
        state |= set(item.add)
        state -= set(item.remove)
        for _ in range(max(1, item.repeat)):
            out.append(set(state))
    return out


def plays(states_entry: set, instrument: str) -> bool:
    """Spielt ein Instrument (oder ein Oberbegriff wie 'Git' für AGit/EGit)?"""
    inst = instrument.lower()
    for s in states_entry:
        sl = s.lower()
        if sl == inst or (inst == 'agit' and sl in ('git', 'agit1', 'agit2', 'agit')):
            return True
    return False
