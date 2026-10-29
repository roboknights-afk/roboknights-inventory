# Fills in the school's "Consent for Participation in an inter-school event"
# form (static/consent_form_template.docx, the exact file the school/Exun
# supplied) for one student and one competition. Its own module with no
# Streamlit import, like e2c_import.py, so it can be tested without the app.
#
# No python-docx / LibreOffice: a .docx is a zip, and the form's blanks are
# runs of underscores inside ONE paragraph of word/document.xml, so this
# swaps those underscores for the real values (underlined, so they still
# read as filled-in blanks) and leaves every other byte of the file alone —
# the guardian's name, signature, relation, mobile, email and date of
# consent are never touched, they're the parent's to write.

import io
import os
import re
import zipfile
from xml.sax.saxutils import escape

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "static", "consent_form_template.docx")

_BLANK = re.compile(r"_{3,}")
_RUN = re.compile(r"<w:r[ >].*?</w:r>", re.S)
_TEXT = re.compile(r"<w:t(?: [^>]*)?>(.*?)</w:t>", re.S)
_RPR = re.compile(r"<w:rPr>.*?</w:rPr>", re.S)

# The form's blanks, in reading order. If the school ever edits the
# template and this count changes, building fails loudly instead of
# silently putting a name in the wrong blank.
_EXPECTED_BLANKS = 9


def _underlined(rpr):
    # <w:u> has a fixed position inside <w:rPr> (before <w:rtl>); Word is
    # forgiving about order but this keeps the file schema-valid.
    if "<w:u " in rpr:
        return rpr
    if "<w:rtl" in rpr:
        return rpr.replace("<w:rtl", '<w:u w:val="single"/><w:rtl', 1)
    return rpr.replace("</w:rPr>", '<w:u w:val="single"/></w:rPr>', 1)


def _run(rpr, text):
    return f'<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def build_consent_docx(student_name, admission_no, grade, section, competition, day, date_text, venue=""):
    # venue falls back to the form's own printed blank when the competition
    # has none on record.
    values = [
        student_name, admission_no, grade, section,
        competition,   # "participate in ____ ____ (name of competition)": first blank...
        "",            # ...second blank dropped, the name goes in one
        venue or None, # venue (None keeps the printed blank)
        day, date_text,
    ]
    with zipfile.ZipFile(TEMPLATE_PATH) as src:
        xml = src.read("word/document.xml").decode("utf-8")
        para_match = next(
            m for m in re.finditer(r"<w:p[ >].*?</w:p>", xml, re.S) if "hereby permit my ward" in m.group(0)
        )
        para = para_match.group(0)

        if len(_BLANK.findall("".join(_TEXT.findall(para)))) != _EXPECTED_BLANKS:
            raise ValueError("The consent form template changed: its blanks no longer match.")

        counter = iter(values)
        index, venue_filled = [0], [False]

        def rebuild(run_match):
            run = run_match.group(0)
            texts = _TEXT.findall(run)
            if len(texts) != 1 or not _BLANK.search(texts[0]):
                return run
            rpr_match = _RPR.search(run)
            rpr = rpr_match.group(0) if rpr_match else ""
            plain_rpr = rpr
            filled_rpr = _underlined(rpr) if rpr else '<w:rPr><w:u w:val="single"/></w:rPr>'
            out, pos, text = [], 0, texts[0]
            for blank in _BLANK.finditer(text):
                if blank.start() > pos:
                    gap = text[pos:blank.start()]
                    if venue_filled[0]:
                        gap = re.sub(r" {3,}", " ", gap)
                        venue_filled[0] = False
                    out.append(_run(plain_rpr, gap))
                value = next(counter)
                venue_filled[0] = index[0] == 6 and bool(value)
                index[0] += 1
                if value is None:
                    out.append(_run(plain_rpr, blank.group(0)))
                elif value != "":
                    out.append(_run(filled_rpr, f" {value} "))
                pos = blank.end()
            if pos < len(text):
                out.append(_run(plain_rpr, text[pos:]))
            return "".join(out)

        new_para = _RUN.sub(rebuild, para)
        new_xml = xml.replace(para, new_para, 1)

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                data = new_xml.encode("utf-8") if item.filename == "word/document.xml" else src.read(item.filename)
                dst.writestr(item, data)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Intra-school "stay back" form (2026-10-28). Same school template, same look,
# with two paragraphs reworded: the title, and the "hereby permit my ward to
# participate in ..." paragraph, which becomes "... to stay back in school
# after school hours on <day> <date> for <reason> till <time> (tentative
# time)". A bold note under it says the time is tentative and that no mobile
# phones are allowed. The transport paragraph is reworded too (the original
# talks about "the team returns", which is about a trip). Every other
# paragraph (conduct, liability, the guardian's signature block) is left
# exactly as the school wrote it.
# ---------------------------------------------------------------------------

STAYBACK_TITLE = "Consent for Staying Back in School After School Hours"
STAYBACK_NOTE = (
    "I understand that this is only a tentative time and my ward might be delayed beyond it. "
    "I also understand that mobile phones are not allowed, and my ward will not bring one."
)
STAYBACK_TRANSPORT = (
    "I shall make all necessary transport arrangements for my ward, as my ward will be staying back "
    "in school after the school buses have left (after normal school hours are over at 1:40 p.m.)."
)
_PARA = re.compile(r"<w:p[ >].*?</w:p>", re.S)


def _para_parts(para, keep_id=True):
    # A paragraph's opening tag, its <w:pPr>, and the formatting of its first
    # run with text, so a rewritten paragraph keeps the template's font,
    # size and spacing. keep_id=False drops Word's paragraph id, for a copy
    # added as a NEW paragraph (two paragraphs must not share an id).
    open_tag = para[:para.index(">") + 1]
    if not keep_id:
        open_tag = re.sub(r' w14:paraId="[^"]*"', "", open_tag)
    ppr = re.search(r"<w:pPr>.*?</w:pPr>", para, re.S)
    run = next(r for r in _RUN.findall(para) if _TEXT.search(r))
    rpr = _RPR.search(run)
    return open_tag, ppr.group(0) if ppr else "", rpr.group(0) if rpr else "<w:rPr></w:rPr>"


def _paragraph(open_tag, ppr, rpr, pieces):
    # pieces: (text, filled) pairs. Filled values are underlined, like the
    # blanks they replace on the inter-school form.
    runs = "".join(_run(_underlined(rpr) if filled else rpr, text) for text, filled in pieces)
    return f"{open_tag}{ppr}{runs}</w:p>"


def _bold(rpr):
    # <w:b> goes right after <w:rFonts> to keep the run's properties in order.
    return re.sub(r"(<w:rFonts[^>]*/>)", r"\1<w:b/><w:bCs/>", rpr, count=1)


# The guardian table's rows, by their printed label, that a stay-back form
# fills in once the guardian has given their details online (2026-10-29).
# "Signature of Guardian" is never here: that's always left to sign by hand.
GUARDIAN_ROWS = {
    "name": "Name of Guardian",
    "relation": "Relation to Student",
    "phone": "Mobile Number",
    "email": "Email ID",
    "date": "Date of consent",
}
_ROW = re.compile(r"<w:tr[ >].*?</w:tr>", re.S)
_CELL = re.compile(r"<w:tc>.*?</w:tc>", re.S)


def _fill_guardian_table(xml, guardian):
    # guardian: GUARDIAN_ROWS keys -> text. Each value goes into the empty
    # right-hand cell of its row, in the label's own font.
    for row in _ROW.findall(xml):
        cells = _CELL.findall(row)
        if len(cells) != 2:
            continue
        label = "".join(_TEXT.findall(cells[0])).strip()
        key = next((k for k, text in GUARDIAN_ROWS.items() if text == label), None)
        if not key or not guardian.get(key) or _TEXT.search(cells[1]):
            continue
        label_run = next(r for r in _RUN.findall(cells[0]) if _TEXT.search(r))
        rpr_match = _RPR.search(label_run)
        first_para = _PARA.search(cells[1]).group(0)
        filled_para = first_para[:-len("</w:p>")] + _run(rpr_match.group(0) if rpr_match else "", guardian[key]) + "</w:p>"
        new_row = row.replace(cells[1], cells[1].replace(first_para, filled_para, 1), 1)
        xml = xml.replace(row, new_row, 1)
    return xml


def build_stayback_docx(student_name, admission_no, grade, section, day, date_text, reason, until_text,
                        guardian=None):
    # guardian (optional): what they gave online, keyed like GUARDIAN_ROWS,
    # printed into the table so only their signature is left blank.
    def value(text):
        # A missing detail stays a printed blank for the guardian to fill in.
        return (f" {text} ", True) if text else (" ______________ ", False)

    with zipfile.ZipFile(TEMPLATE_PATH) as src:
        xml = src.read("word/document.xml").decode("utf-8")
        paras = _PARA.findall(xml)
        title = next(p for p in paras if "inter-school event" in p)
        body = next(p for p in paras if "hereby permit my ward" in p)
        transport = next(p for p in paras if "transport arrangements" in p)

        new_title = _paragraph(*_para_parts(title), [(STAYBACK_TITLE, False)])
        open_tag, ppr, rpr = _para_parts(body)
        new_body = _paragraph(open_tag, ppr, rpr, [
            ("I, guardian of", False), value(student_name),
            ("Adm. No.", False), value(admission_no),
            ("of Class", False), value(grade),
            ("section", False), value(section),
            ("hereby permit my ward to stay back in school after school hours on", False),
            value(day), value(date_text),
            ("for", False), value(reason),
            ("till", False), value(until_text),
            ("(tentative time).", False),
        ])
        copy_tag, _, _ = _para_parts(body, keep_id=False)
        spacer = f"<w:p>{ppr}</w:p>"
        note = _paragraph(copy_tag, ppr, _bold(rpr), [(STAYBACK_NOTE, False)])

        new_transport = _paragraph(*_para_parts(transport), [(STAYBACK_TRANSPORT, False)])

        new_xml = (
            xml.replace(title, new_title, 1)
            .replace(body, new_body + spacer + note, 1)
            .replace(transport, new_transport, 1)
        )
        if guardian:
            new_xml = _fill_guardian_table(new_xml, guardian)

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                data = new_xml.encode("utf-8") if item.filename == "word/document.xml" else src.read(item.filename)
                dst.writestr(item, data)
    return buffer.getvalue()
