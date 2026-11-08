"""Musiktheorie-Grundlagen: Tonnamen, Schreibweise (Quintenzirkel), Akkordsymbole, Transposition.

Tonhöhen werden als MIDI-Nummer gespeichert, die Schreibweise als TPC (tonal pitch class,
Position auf dem Quintenzirkel wie in MuseScore: C=14, G=15, F=13, F#=20, Bb=12 ...).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# --- Tonnamen / TPC -------------------------------------------------------------------------

_TPC_NAMES = ['Fbb', 'Cbb', 'Gbb', 'Dbb', 'Abb', 'Ebb', 'Bbb',
              'Fb', 'Cb', 'Gb', 'Db', 'Ab', 'Eb', 'Bb',
              'F', 'C', 'G', 'D', 'A', 'E', 'B',
              'F#', 'C#', 'G#', 'D#', 'A#', 'E#', 'B#',
              'F##', 'C##', 'G##', 'D##', 'A##', 'E##', 'B##']
STEP_PC = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}


def tpc_name(tpc: int) -> str:
    """TPC -> Name mit # und b (englische Schreibweise, B = h)."""
    return _TPC_NAMES[tpc + 1]


def name_tpc(name: str) -> int:
    name = normalize_accidentals(name)
    return _TPC_NAMES.index(name) - 1


def tpc_pc(tpc: int) -> int:
    return (tpc - 14) * 7 % 12


def tpc_step_alter(tpc: int) -> tuple[str, int]:
    n = tpc_name(tpc)
    return n[0], n.count('#') - n.count('b')


def normalize_accidentals(s: str) -> str:
    return s.replace('♯', '#').replace('♭', 'b').replace('♮', '')


def spell_pitch(pc_or_midi: int, key_tpc: int = 14) -> int:
    """Wählt die passende Schreibweise (TPC) für eine Tonhöhe im Kontext einer Dur-Tonart.

    key_tpc ist die TPC des Dur-Grundtons (Moll: parallele Dur-Tonart übergeben).
    Es wird die TPC gewählt, die der Mitte der Tonleiter (Grundton + 2 Quinten) am nächsten liegt.
    """
    pc = pc_or_midi % 12
    center = key_tpc + 2
    best = None
    for tpc in range(-1, 34):
        if tpc_pc(tpc) == pc:
            d = abs(tpc - center)
            if best is None or d < best[0]:
                best = (d, tpc)
    return best[1]


def midi_to_name(midi: int, tpc: Optional[int] = None) -> str:
    """MIDI + TPC -> z. B. 'F#4' (wissenschaftliche Oktavzählung, C4 = eingestrichenes c)."""
    if tpc is None:
        tpc = spell_pitch(midi)
    step, alter = tpc_step_alter(tpc)
    # Oktave aus dem Stammton bestimmen (wichtig für Cb, B#)
    octave = (midi - alter - STEP_PC[step]) // 12 - 1
    return tpc_name(tpc) + str(octave)


def helmholtz(midi: int, tpc: Optional[int] = None) -> str:
    """Deutsche Schreibweise: c' = C4, h = B3, cis'' = C#5, B = Bb2 ..."""
    if tpc is None:
        tpc = spell_pitch(midi)
    step, alter = tpc_step_alter(tpc)
    octave = (midi - alter - STEP_PC[step]) // 12 - 1
    base = 'h' if step == 'B' else step.lower()
    if alter == 0:
        s = base
    elif alter > 0:
        s = base + 'is' * alter
    else:
        if step == 'B':
            s = 'b' + ('es' * (-alter - 1))
        elif step in 'EA':
            s = base + 's' + ('es' * (-alter - 1))
        else:
            s = base + 'es' * (-alter)
    if octave >= 4:
        return s + "'" * (octave - 3)
    if octave == 3:
        return s
    s = s[0].upper() + s[1:]
    if octave == 2:
        return s
    return s + ',' * (2 - octave)


def parse_helmholtz(s: str) -> int:
    """'cis''' -> 73, 'g' -> 55, 'A,' -> 33 ..."""
    s = s.strip()
    m = re.match(r"^([A-Ha-h])((?:is|es|s)*)([',]*)$", s)
    if not m:
        raise ValueError(f'Ton nicht erkannt: {s}')
    letter, acc, marks = m.groups()
    low = letter.lower()
    alter = acc.count('is') - acc.count('es') - (1 if acc == 's' else 0)
    if low == 'h':
        step = 'B'
    elif low == 'b' and acc == '':
        step, alter = 'B', -1
    else:
        step = low.upper()
    if letter.islower():
        octave = 3 + marks.count("'")
    else:
        octave = 2 - marks.count(',')
    return (octave + 1) * 12 + STEP_PC[step] + alter


# --- Tonarten -------------------------------------------------------------------------------

def key_tpc_from_name(key: str) -> tuple[int, bool]:
    """'G' -> (15, False); 'F#m' -> (tpc von A = 17, True) – liefert TPC der Dur-Parallele."""
    key = normalize_accidentals(key.strip())
    minor = key.endswith('m') and not key.endswith('maj')
    root = key[:-1] if minor else key
    tpc = name_tpc(root)
    return (tpc - 3 if minor else tpc), minor


def key_name(major_tpc: int, minor: bool = False) -> str:
    return tpc_name(major_tpc + 3) + 'm' if minor else tpc_name(major_tpc)


def key_name_de(major_tpc: int, minor: bool = False) -> str:
    """Deutscher Tonartname: 'G-Dur', 'fis-Moll', 'B-Dur', 'Es-Dur'."""
    tpc = major_tpc + 3 if minor else major_tpc
    s = helmholtz(60 + tpc_pc(tpc) - 0, tpc)  # Oktave egal
    s = s.rstrip("'").rstrip(',')
    s = s.lower()
    return (s + '-Moll') if minor else (s[0].upper() + s[1:] + '-Dur')


def keysig_sharps(major_tpc: int) -> int:
    """Anzahl Vorzeichen: + = Kreuze, - = Bs (C-Dur = 0, G = 1, F = -1)."""
    return major_tpc - 14


def transpose_tpc(tpc: int, semitones: int, old_key_tpc: int, new_key_tpc: int) -> int:
    """Verschiebt eine TPC so, wie sich die Tonart verschiebt (Schreibweise bleibt stimmig)."""
    return tpc + (new_key_tpc - old_key_tpc)


def best_key_for_shift(key_tpc: int, semitones: int) -> int:
    """Neue Dur-Tonart (TPC) nach Verschiebung um Halbtöne – mit möglichst wenig Vorzeichen."""
    pc = (tpc_pc(key_tpc) + semitones) % 12
    cands = [t for t in range(7, 22) if tpc_pc(t) == pc]  # Cb..C# (–7..+7 Vorzeichen)
    return min(cands, key=lambda t: (abs(t - 14), t > 14))  # bei Gleichstand (F#/Gb): b-Variante


# --- Akkordsymbole --------------------------------------------------------------------------

_CHORD_RE = re.compile(r'^([A-G](?:##|bb|#|b)?)([^/]*)(?:/([A-G](?:##|bb|#|b)?))?$')


@dataclass
class ChordSymbol:
    root: Optional[int]          # TPC, None bei N.C.
    quality: str = ''            # alles zwischen Grundton und Bass: 'm7', 'sus4', 'add9', '2' ...
    bass: Optional[int] = None   # TPC des Basstons

    @classmethod
    def parse(cls, text: str) -> 'ChordSymbol':
        t = normalize_accidentals(text.strip()).replace(' ', '')
        if t[:1] == 'H':  # deutsche Schreibweise
            t = 'B' + t[1:]
        t = re.sub(r'/H', '/B', t)
        if t.upper() in ('N.C.', 'NC', 'N.C', 'N.C.:'):
            return cls(None, 'N.C.')
        m = _CHORD_RE.match(t)
        if not m:
            raise ValueError(f'Akkord nicht erkannt: {text}')
        root, q, bass = m.groups()
        # Deutsche Schreibweise 'H' wird oben vom Regex nicht erkannt -> vorher ersetzen
        return cls(name_tpc(root), q, name_tpc(bass) if bass else None)

    @property
    def is_nc(self) -> bool:
        return self.root is None

    @property
    def minor(self) -> bool:
        q = self.quality
        return q.startswith('m') and not q.startswith('maj')

    def text(self, unicode_acc: bool = False) -> str:
        if self.is_nc:
            return 'N.C.'
        s = tpc_name(self.root) + self.quality + ('/' + tpc_name(self.bass) if self.bass is not None else '')
        return _uni(s) if unicode_acc else s

    def parts_for_sheet(self) -> list[tuple[str, bool]]:
        """Zerlegt für den Satz im Chord-Sheet: [(text, hochgestellt?)].

        Grundton und 'm' normal, Erweiterungen hochgestellt, Bass normal: Em⁷, Dˢᵘˢ⁴, C², Am⁷/G.
        """
        if self.is_nc:
            return [('N.C.', False)]
        out = [(_uni(tpc_name(self.root)), False)]
        q = self.quality
        if self.minor and not q.startswith('maj'):
            out.append(('m', False))
            q = q[1:]
        q = {'sus24': '2sus4', 'sus2sus4': '2sus4'}.get(q, q)
        if q:
            out.append((_uni(q), True))
        if self.bass is not None:
            out.append(('/' + _uni(tpc_name(self.bass)), False))
        return out

    def pitch_classes(self) -> dict[int, str]:
        """Akkordtöne als {Tonklasse: Funktion} – Funktion 'R','3','5','7','9','4','6'."""
        if self.is_nc:
            return {}
        r = tpc_pc(self.root)
        q = self.quality
        tones = {0: 'R'}
        minor = self.minor
        if 'dim' in q or 'o' == q[:1]:
            tones[3] = '3'; tones[6] = '5'
        elif 'aug' in q or q.startswith('+'):
            tones[4] = '3'; tones[8] = '5'
        else:
            if 'sus' in q:
                if '2' in q and '4' in q:
                    tones[2] = '9'; tones[5] = '4'
                elif '2' in q:
                    tones[2] = '9'
                else:
                    tones[5] = '4'
            else:
                tones[3 if minor else 4] = '3'
            if 'b5' in q:
                tones[6] = '5'
            else:
                tones[7] = '5'
        if 'maj7' in q or 'maj9' in q or 'Δ' in q:
            tones[11] = '7'
        elif re.search(r'(?<!add)(7|9|11|13)', q) and 'maj' not in q:
            tones[10] = '7'
        if '6' in q:
            tones[9] = '6'
        if 'add9' in q or 'add2' in q or re.fullmatch(r'2', q) or '9' in q or q.endswith('2'):
            tones[2] = '9'
        if 'add4' in q or '11' in q:
            tones[5] = '4'
        res = {(r + k) % 12: v for k, v in tones.items()}
        if self.bass is not None:
            res.setdefault(tpc_pc(self.bass), 'B')
        return res

    def bass_pc(self) -> Optional[int]:
        if self.is_nc:
            return None
        return tpc_pc(self.bass if self.bass is not None else self.root)

    def root_pc(self) -> Optional[int]:
        return None if self.is_nc else tpc_pc(self.root)

    def transposed(self, delta_tpc: int) -> 'ChordSymbol':
        if self.is_nc:
            return self
        return ChordSymbol(self.root + delta_tpc, self.quality,
                           self.bass + delta_tpc if self.bass is not None else None)

    def simplified_for_guitar(self, keep_slash: bool = False) -> 'ChordSymbol':
        """Vereinfachung wie auf den A-GIT-Blättern: Slash-Bass weg, sus4 -> 4, add9 -> 2."""
        if self.is_nc:
            return self
        q = self.quality
        q = {'sus4': '4', 'sus2': '2', 'sus': '4', 'sus24': '4', 'add9': '2', 'add2': '2'}.get(q, q)
        if q in ('6', '69', '6/9'):
            q = ''
        return ChordSymbol(self.root, q, self.bass if keep_slash else None)

    def __str__(self) -> str:
        return self.text()


def _uni(s: str) -> str:
    """# und b durch ♯ und ♭ ersetzen – aber nur als Vorzeichen (nicht im Wort 'sus' o. ä.)."""
    out = []
    for i, ch in enumerate(s):
        prev = s[i - 1] if i else ''
        if ch == '#':
            out.append('♯')
        elif ch == 'b' and prev and prev in 'ABCDEFG♭b' and (i + 1 >= len(s) or s[i + 1] not in 'aeiou'):
            out.append('♭')
        else:
            out.append(ch)
    return ''.join(out)


def chord_display(symbol: str) -> str:
    try:
        return ChordSymbol.parse(symbol).text(unicode_acc=True)
    except ValueError:
        return symbol
