"""Grundlegende Tests (ohne Audio/MuseScore): python3 -m pytest tests"""
from fractions import Fraction as F

from kenanja.audio.analysis import parse_lyrics, split_by_repetition
from kenanja.model import Bar, ChordEvent, FormItem, Note, Section, Song
from kenanja.theory import ChordSymbol, helmholtz, key_name_de, parse_helmholtz


def test_akkorde():
    c = ChordSymbol.parse('G/F#')
    assert c.text(True) == 'G/F♯'
    assert ChordSymbol.parse('Dsus4').simplified_for_guitar().text() == 'D4'
    assert ChordSymbol.parse('Am7/G').parts_for_sheet() == [('A', False), ('m', False), ('7', True), ('/G', False)]
    assert ChordSymbol.parse('H7').text() == 'B7'
    assert ChordSymbol.parse('Bb').transposed(2).text() == 'C'


def test_toene():
    assert helmholtz(73) == "cis''"
    assert parse_helmholtz("cis''") == 73
    assert parse_helmholtz('g') == 55
    assert key_name_de(15) == 'G-Dur'
    assert key_name_de(17, True) == 'fis-Moll'


def _song():
    bars = []
    for sym in ('G', 'C', 'D', 'G'):
        b = Bar(chords=[ChordEvent(F(0), sym)])
        b.melody = [Note(F(0), F(2), 67, lyrics={0: ('Hal', 'begin')}), Note(F(2), F(2), 71, lyrics={0: ('lo', 'end')})]
        bars.append(b)
    s = Song(title='Test', key='G', sections=[Section('V1', 'Verse 1', 'verse', bars)])
    s.default_form()
    return s


def test_transponieren_und_speichern(tmp_path):
    s = _song()
    s.transpose = -2
    t = s.transposed()
    assert t.key == 'F'
    assert t.sections[0].bars[0].chords[0].symbol == 'F'
    assert t.sections[0].bars[0].melody[0].pitch == 65
    p = tmp_path / 'x.json'
    s.save(p)
    assert Song.load(p).sections[0].bars[1].chords[0].symbol == 'C'


def test_liedtext():
    blocks = parse_lyrics('[Verse]\nA\nB\n(Ladies)\nC\n\n[Chorus]\nD\nE x2\n[Chorus]\n'
                          'You might also like\n[X](https://genius.com/x)')
    assert [b.label for b in blocks] == ['Verse', 'Chorus', 'Chorus']
    assert blocks[0].cues == [(2, 'Ladies')]
    assert blocks[1].lines == ['D', 'E', 'E']
    assert blocks[2].lines == ['D', 'E', 'E']
    lines = ['a', 'b', 'c', 'd', 'x', 'y', 'z', 'w', 'a', 'b', 'c', 'd', 'x', 'y', 'z', 'w']
    assert len(split_by_repetition(lines)) >= 2


def test_zeilen_zuordnung_mehr_text_als_noten():
    """Mehr Textzeilen als Noten (z. B. kurzer Ausschnitt mit ganzem Liedtext) darf nicht abstürzen."""
    from kenanja.audio.analysis import VNote, align_lines
    notes = [VNote(i * 0.5, i * 0.5 + 0.4, 60 + i % 3) for i in range(6)]
    res = align_lines([5, 6, 7, 8, 5, 6], notes)
    assert len(res) == 6
    assert all(r is None or (0 <= r[0] < r[1] <= len(notes)) for r in res)


def test_gemeinde_angleichen_nur_bei_mehrheit():
    from kenanja.audio.gemeinde import LNote, unify_repeats
    mk = lambda ps: [LNote(16 + 2 * k, 18 + 2 * k, float(p), p) for k, p in enumerate(ps)]
    lines = [mk([62, 64, 65]), mk([62, 64, 65]), mk([62, 63, 65]), mk([60, 61, 62])]
    unify_repeats(lines, [0, 0, 0, 0], texts=['a b c', 'a b c', 'a b c', 'x y z'])
    assert [n.pitch for n in lines[2]] == [62, 64, 65]      # Mehrheit (2 von 3) gewinnt
    assert [n.pitch for n in lines[3]] == [60, 61, 62]      # anderer Text bleibt unberührt


def test_spracherkennung_woerter_verankern():
    """Erkannte Wörter (mit Ad-libs und einem Hörfehler) werden den richtigen Textwörtern zugeordnet."""
    from kenanja.audio import asr as A
    from kenanja.audio.analysis import parse_lyrics, text_syllables
    blocks = parse_lyrics('[Verse]\nDer Morgen kommt ganz leise\nund alles wird hell\n\n[Chorus]\nSinge laut')
    syls = text_syllables(blocks, 'de')
    found = [(1.0, 1.3, 'der'), (1.3, 1.9, 'Morgen'), (2.0, 2.4, 'kommt'), (2.4, 2.8, 'kanz'), (2.9, 3.6, 'leise'),
             (4.0, 4.5, 'yeah'), (5.0, 5.2, 'und'), (5.2, 5.6, 'alles'), (5.6, 5.9, 'wird'), (6.0, 6.5, 'hell'),
             (9.0, 9.6, 'singe'), (9.7, 10.2, 'laut')]
    t = A.syllable_times(syls, A.merge(found, [(5.25, 5.6, 'alles')]))
    words = {s.text.lower().strip(',.'): tt for s, tt in zip(syls, t) if s.syllabic in ('single', 'begin')}
    assert words['der'] == 1.0 and words['mor'] == 1.3 and words['al'] in (5.2, 5.25) and words['hell'] == 6.0
    assert words['sin'] == 9.0 and words['laut'] == 9.7
    # ein Wort der Aufnahme wird nicht zweimal verwendet
    used = [x for x in t if x is not None]
    assert len(used) == len(set(used))
