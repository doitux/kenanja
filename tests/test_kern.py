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
