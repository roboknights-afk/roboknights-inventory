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
