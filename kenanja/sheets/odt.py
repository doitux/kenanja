"""Kleiner, abhängigkeitsfreier ODT-Schreiber (LibreOffice Writer) mit genau den Mitteln,
die die Blätter brauchen: Absätze mit eigenen Tabstopps, farbige/hochgestellte Textstücke,
Bilder, 'zusammenhalten' über Seitenumbrüche, PDF-Export über LibreOffice.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

_METRICS = json.loads((Path(__file__).resolve().parent.parent / 'data' / 'fontmetrics.json').read_text(encoding='utf-8'))


def text_width_cm(text: str, size_pt: float, kind: str = 'sans') -> float:
    """Breite eines Texts in cm (Arial-/Liberation-Metriken). kind: sans, sans-bold, sans-italic, mono-bold."""
    w = _METRICS.get(kind, _METRICS['sans'])
    units = sum(w.get(ch, 556) for ch in text)
    return units / 1000 * size_pt * 2.54 / 72


def _xml(s: str) -> str:
    return escape(s, {'"': '&quot;'})


class Span:
    def __init__(self, text='', **props):
        self.text = text
        self.props = props  # bold, italic, color, sup, size, font, tab, image


class Paragraph:
    def __init__(self, doc, **props):
        self.doc = doc
        self.props = props
        self.spans: list[Span] = []

    def text(self, text, **props):
        if text:
            self.spans.append(Span(text, **props))
        return self

    def tab(self):
        self.spans.append(Span('', tab=True))
        return self

    def image(self, path, width_cm, height_cm, anchor='as-char'):
        name = self.doc.add_picture(path)
        self.spans.append(Span('', image=(name, width_cm, height_cm, anchor)))
        return self


class OdtDoc:
    def __init__(self, font='Arial', size=14, margins_cm=(1.5, 1.5, 1.5, 1.5), page=(21.0, 29.7),
                 lang='de', country='DE'):
        self.font, self.size = font, size
        self.margins = margins_cm   # top, right, bottom, left
        self.page = page
        self.lang, self.country = lang, country
        self.paras: list[Paragraph] = []
        self.pictures: dict[str, bytes] = {}
        self._pstyles: dict[str, str] = {}
        self._tstyles: dict[str, str] = {}
        self.footer_text = None

    @property
    def text_width(self) -> float:
        return self.page[0] - self.margins[1] - self.margins[3]

    def paragraph(self, **props) -> Paragraph:
        p = Paragraph(self, **props)
        self.paras.append(p)
        return p

    def add_picture(self, path) -> str:
        data = Path(path).read_bytes()
        name = f'Pictures/img{len(self.pictures) + 1}{Path(path).suffix.lower()}'
        self.pictures[name] = data
        return name

    # ---- Stile ----
    def _pstyle(self, props) -> str:
        key = json.dumps(props, sort_keys=True)
        if key in self._pstyles:
            return self._pstyles[key]
        name = f'P{len(self._pstyles) + 1}'
        para = []
        if props.get('align'):
            para.append(f'fo:text-align="{ {"left": "start", "right": "end", "center": "center", "justify": "justify"}[props["align"]] }"')
        para.append(f'fo:margin-top="{props.get("space_before", 0)}pt" fo:margin-bottom="{props.get("space_after", 0)}pt"')
        if props.get('indent_cm'):
            para.append(f'fo:margin-left="{props["indent_cm"]:.3f}cm"')
        if props.get('keep_next'):
            para.append('fo:keep-with-next="always"')
        if props.get('keep_together'):
            para.append('fo:keep-together="always"')
        if props.get('page_break_before'):
            para.append('fo:break-before="page"')
        if props.get('line_height'):
            para.append(f'fo:line-height="{props["line_height"]}"')
        tabs = ''
        if props.get('tabs'):
            tabs = '<style:tab-stops>' + ''.join(
                f'<style:tab-stop style:position="{x:.3f}cm"' + (f' style:type="{t}"' if t != 'left' else '') + '/>'
                for x, t in props['tabs']) + '</style:tab-stops>'
        else:
            tabs = '<style:tab-stops/>'
        text = self._text_props(props)
        self._pstyles[key] = name
        self._pdefs = getattr(self, '_pdefs', [])
        self._pdefs.append(
            f'<style:style style:name="{name}" style:family="paragraph" style:parent-style-name="Standard">'
            f'<style:paragraph-properties {" ".join(para)}>{tabs}</style:paragraph-properties>'
            f'<style:text-properties {text}/></style:style>')
        return name

    def _text_props(self, props) -> str:
        a = []
        if props.get('font'):
            a.append(f'fo:font-family="&apos;{props["font"]}&apos;"')
        if props.get('size'):
            s = props['size']
            a.append(f'fo:font-size="{s}pt" style:font-size-asian="{s}pt" style:font-size-complex="{s}pt"')
        if props.get('bold'):
            a.append('fo:font-weight="bold" style:font-weight-asian="bold" style:font-weight-complex="bold"')
        if props.get('italic'):
            a.append('fo:font-style="italic" style:font-style-asian="italic" style:font-style-complex="italic"')
        if props.get('color'):
            a.append(f'fo:color="{props["color"]}"')
        if props.get('sup'):
            a.append('style:text-position="super 58%"')
        return ' '.join(a)

    def _tstyle(self, props) -> str:
        props = {k: v for k, v in props.items() if k in ('bold', 'italic', 'color', 'sup', 'size', 'font') and v}
        if not props:
            return ''
        key = json.dumps(props, sort_keys=True)
        if key not in self._tstyles:
            name = f'T{len(self._tstyles) + 1}'
            self._tstyles[key] = name
            self._tdefs = getattr(self, '_tdefs', [])
            self._tdefs.append(f'<style:style style:name="{name}" style:family="text">'
                               f'<style:text-properties {self._text_props(props)}/></style:style>')
        return self._tstyles[key]

    # ---- Ausgabe ----
    def _body(self) -> str:
        out = []
        for p in self.paras:
            ps = self._pstyle(p.props)
            inner = []
            for sp in p.spans:
                if sp.props.get('tab'):
                    inner.append('<text:tab/>')
                    continue
                if sp.props.get('image'):
                    name, w, h, anchor = sp.props['image']
                    inner.append(
                        f'<draw:frame draw:style-name="fr1" text:anchor-type="{anchor}" svg:width="{w:.3f}cm" '
                        f'svg:height="{h:.3f}cm" draw:z-index="0"><draw:image xlink:href="{name}" '
                        f'xlink:type="simple" xlink:show="embed" xlink:actuate="onLoad"/></draw:frame>')
                    continue
                t = _xml(sp.text)
                # mehrere Leerzeichen erhalten
                import re
                t = re.sub(r'  +', lambda m: ' ' + f'<text:s text:c="{len(m.group(0)) - 1}"/>', t)
                if t.startswith(' '):
                    t = '<text:s/>' + t[1:]
                t = t.replace('\n', '<text:line-break/>').replace('\t', '<text:tab/>')
                ts = self._tstyle(sp.props)
                inner.append(f'<text:span text:style-name="{ts}">{t}</text:span>' if ts else t)
            out.append(f'<text:p text:style-name="{ps}">{"".join(inner)}</text:p>')
        return ''.join(out)

    def save(self, path) -> Path:
        path = Path(path)
        body = self._body()
        ns = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
              'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
              'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
              'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
              'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
              'xmlns:xlink="http://www.w3.org/1999/xlink" '
              'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" '
              'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
              'xmlns:loext="urn:org:documentfoundation:names:experimental:office:xmlns:loext:1.0" '
              'office:version="1.3"')
        fonts = (f'<office:font-face-decls><style:font-face style:name="{self.font}" svg:font-family="{self.font}" '
                 'style:font-family-generic="swiss" style:font-pitch="variable"/>'
                 '<style:font-face style:name="Courier New" svg:font-family="&apos;Courier New&apos;" '
                 'style:font-family-generic="modern" style:font-pitch="fixed"/></office:font-face-decls>')
        content = (f'<?xml version="1.0" encoding="UTF-8"?><office:document-content {ns}>{fonts}'
                   '<office:automatic-styles>'
                   '<style:style style:name="fr1" style:family="graphic"><style:graphic-properties '
                   'style:vertical-pos="middle" style:vertical-rel="text" style:wrap="none"/></style:style>'
                   + ''.join(getattr(self, '_pdefs', [])) + ''.join(getattr(self, '_tdefs', [])) +
                   f'</office:automatic-styles><office:body><office:text>{body}</office:text></office:body>'
                   '</office:document-content>')
        top, right, bottom, left = self.margins
        footer = ''
        if self.footer_text:
            footer = (f'<style:footer><text:p text:style-name="Footer">{_xml(self.footer_text)} – '
                      '<text:page-number text:select-page="current"/></text:p></style:footer>')
        styles = (f'<?xml version="1.0" encoding="UTF-8"?><office:document-styles {ns}>{fonts}'
                  '<office:styles>'
                  f'<style:default-style style:family="paragraph"><style:paragraph-properties style:tab-stop-distance="1.25cm"/>'
                  f'<style:text-properties style:font-name="{self.font}" fo:font-size="{self.size}pt" '
                  f'fo:language="{self.lang}" fo:country="{self.country}" style:font-size-asian="{self.size}pt" '
                  f'style:font-size-complex="{self.size}pt"/></style:default-style>'
                  '<style:style style:name="Standard" style:family="paragraph" style:class="text"/>'
                  '<style:style style:name="Footer" style:family="paragraph" style:parent-style-name="Standard">'
                  '<style:paragraph-properties fo:text-align="center"/><style:text-properties fo:font-size="9pt" '
                  'fo:color="#808080"/></style:style>'
                  '</office:styles><office:automatic-styles>'
                  f'<style:page-layout style:name="pm1"><style:page-layout-properties fo:page-width="{self.page[0]}cm" '
                  f'fo:page-height="{self.page[1]}cm" style:print-orientation="portrait" fo:margin-top="{top}cm" '
                  f'fo:margin-bottom="{bottom}cm" fo:margin-left="{left}cm" fo:margin-right="{right}cm"/>'
                  '<style:footer-style><style:header-footer-properties fo:min-height="0.6cm" fo:margin-top="0.3cm"/>'
                  '</style:footer-style></style:page-layout>'
                  '</office:automatic-styles><office:master-styles>'
                  f'<style:master-page style:name="Standard" style:page-layout-name="pm1">{footer}</style:master-page>'
                  '</office:master-styles></office:document-styles>')
        manifest = ('<?xml version="1.0" encoding="UTF-8"?><manifest:manifest '
                    'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.3">'
                    '<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>'
                    '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
                    '<manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>'
                    '<manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>'
                    + ''.join(f'<manifest:file-entry manifest:full-path="{n}" manifest:media-type="image/{"png" if n.endswith(".png") else "svg+xml"}"/>'
                              for n in self.pictures) +
                    '</manifest:manifest>')
        meta = (f'<?xml version="1.0" encoding="UTF-8"?><office:document-meta {ns}><office:meta>'
                '<meta:generator>Kenanja</meta:generator></office:meta></office:document-meta>')
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr(zipfile.ZipInfo('mimetype'), 'application/vnd.oasis.opendocument.text')
            for n, d in (('content.xml', content), ('styles.xml', styles), ('meta.xml', meta),
                         ('META-INF/manifest.xml', manifest)):
                z.writestr(n, d, compress_type=zipfile.ZIP_DEFLATED)
            for n, d in self.pictures.items():
                z.writestr(n, d)
        return path


def find_soffice() -> str | None:
    for c in ('soffice', 'libreoffice', '/usr/bin/soffice', '/usr/lib/libreoffice/program/soffice',
              '/opt/libreoffice/program/soffice'):
        p = shutil.which(c) or (c if Path(c).exists() else None)
        if p:
            return p
    # Flatpak
    if shutil.which('flatpak'):
        r = subprocess.run(['flatpak', 'info', 'org.libreoffice.LibreOffice'], capture_output=True)
        if r.returncode == 0:
            return 'flatpak run org.libreoffice.LibreOffice'
    return None


def to_pdf(odt_path, out_dir=None) -> Path | None:
    """ODT -> PDF über LibreOffice (headless). Gibt den PDF-Pfad zurück oder None."""
    soffice = find_soffice()
    if not soffice:
        return None
    odt_path = Path(odt_path)
    out_dir = Path(out_dir or odt_path.parent)
    with tempfile.TemporaryDirectory() as prof:
        cmd = soffice.split() + [f'-env:UserInstallation=file://{prof}', '--headless', '--convert-to', 'pdf',
                                 '--outdir', str(out_dir), str(odt_path)]
        subprocess.run(cmd, capture_output=True, timeout=180)
    pdf = out_dir / (odt_path.stem + '.pdf')
    return pdf if pdf.exists() else None
