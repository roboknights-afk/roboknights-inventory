# RoboKnights-branded emails (2026-10-29, host's request: "professionalise
# all mails"). Every email the app and its scheduled jobs send still starts
# as the same plain-text body; build_message() wraps it in a branded HTML
# version (logo, gold accent bar, dark title band, footer) and keeps the
# plain text alongside it for mail apps that don't show HTML. So no caller
# has to change - they keep writing plain text.
#
# The plain text is turned into HTML by simple line rules, nothing clever:
#   blank line        -> new paragraph
#   "- item"          -> bullet list
#   "1. step"         -> numbered list (indented lines under it stay with it)
#   "====" / "----"   -> a divider
#   "a | b | c" lines -> a table, the first line its header row
#   "Label: https://" -> a gold button labelled "Label" (on a line of its own)
#   any other URL     -> a link
#   a closing "- RoboKnights" / "Regards, RoboKnights" -> the sign-off, and a
#   closing "This is an automated message." is dropped (the footer says it)
#
# No Streamlit import, like e2c_import.py, so the GitHub Actions scripts
# (send_due_reminders.py and friends) can use it too.

import os
import re
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

LOGO_PATH = os.path.join(os.path.dirname(__file__), "static", "email_logo.png")
LOGO_CID = "rk-logo"
DASHBOARD_LINK = "https://exun.co/rkdash"

GOLD = "#E8B33D"
DARK = "#242424"
TEXT = "#1F2937"
MUTED = "#6B7280"
PAGE = "#F3F4F6"
FONT = "'Segoe UI', Helvetica, Arial, sans-serif"

_URL = re.compile(r"https?://[^\s<>\"']+[^\s<>\"'.,;:!?)]")
_BUTTON_LINE = re.compile(r"^([^:]{1,40}):\s*(https?://\S+)$")
_BULLET = re.compile(r"^\s*[-•*]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_RULE = re.compile(r"^[=\-_*]{3,}$")
_SIGN_OFFS = {"- roboknights", "— roboknights", "-roboknights", "roboknights", "- team roboknights",
              "team roboknights"}
_GREETINGS = {"regards,", "thanks,", "thank you,", "best,", "best regards,"}
_AUTOMATED = re.compile(r"^\**this is an? automated message\.?\**$", re.I)


def _inline(text):
    # Escape, then turn URLs into links and **bold** into <strong>.
    out, pos = [], 0
    for m in _URL.finditer(text):
        out.append(escape(text[pos:m.start()]))
        url = m.group(0)
        out.append(f'<a href="{escape(url)}" style="color:#B7860B;text-decoration:underline;">{escape(url)}</a>')
        pos = m.end()
    out.append(escape(text[pos:]))
    html = "".join(out)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)


def _button(label, url):
    if label.lower() == "dashboard":
        label = "Open the dashboard"
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:6px 0 18px;"><tr>'
        f'<td style="background:{GOLD};border-radius:6px;">'
        f'<a href="{escape(url)}" style="display:inline-block;padding:11px 22px;font-family:{FONT};'
        f'font-size:15px;font-weight:600;color:{DARK};text-decoration:none;">{escape(label)} &rarr;</a>'
        f"</td></tr></table>"
    )


def body_to_html(body):
    lines = body.replace("\r\n", "\n").strip("\n").split("\n")
    sign_off = False
    def trim():
        while lines and not lines[-1].strip():
            lines.pop()

    trim()
    if lines and _AUTOMATED.match(lines[-1].strip()):
        lines.pop()
        trim()
    if lines and lines[-1].strip().lower() in _SIGN_OFFS:
        lines.pop()
        sign_off = True
        if lines and lines[-1].strip().lower() in _GREETINGS:
            lines.pop()
        trim()

    p_style = f"margin:0 0 16px;font-family:{FONT};font-size:15px;line-height:1.6;color:{TEXT};"
    li_style = f"margin:0 0 6px;font-family:{FONT};font-size:15px;line-height:1.6;color:{TEXT};"
    blocks, para, items, table = [], [], [], []
    list_tag, list_start = None, 1

    def flush_para():
        if para:
            blocks.append(f'<p style="{p_style}">' + "<br>".join(_inline(x) for x in para) + "</p>")
            para.clear()

    def flush_list():
        nonlocal list_tag
        if items:
            # start= keeps "2." a 2 when blank lines split a numbered list.
            start = f' start="{list_start}"' if list_tag == "ol" and list_start != 1 else ""
            blocks.append(
                f'<{list_tag}{start} style="margin:0 0 16px;padding-left:22px;">'
                + "".join(f'<li style="{li_style}">' + "<br>".join(_inline(x) for x in item) + "</li>"
                          for item in items)
                + f"</{list_tag}>"
            )
            items.clear()
        list_tag = None

    def flush_table():
        if table:
            cell = f"padding:7px 10px;border-bottom:1px solid #E5E7EB;font-family:{FONT};font-size:13px;line-height:1.4;"
            head = "".join(
                f'<th align="left" style="{cell}background:{DARK};color:{GOLD};font-weight:600;">{_inline(c)}</th>'
                for c in table[0]
            )
            body_rows = "".join(
                f'<tr style="background:{"#FAFAFA" if i % 2 else "#FFFFFF"};">'
                + "".join(f'<td style="{cell}color:{TEXT};">{_inline(c)}</td>' for c in row) + "</tr>"
                for i, row in enumerate(table[1:])
            )
            blocks.append(
                '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                'style="border-collapse:collapse;margin:0 0 18px;border:1px solid #E5E7EB;">'
                f"<tr>{head}</tr>{body_rows}</table>"
            )
            table.clear()

    for line in lines:
        stripped = line.strip()
        if " | " in stripped:
            flush_para()
            flush_list()
            table.append([c.strip() for c in stripped.split(" | ")])
            continue
        flush_table()
        bullet, numbered, button = _BULLET.match(line), _NUMBERED.match(line), _BUTTON_LINE.match(stripped)
        if not stripped:
            flush_para()
            if not items:
                flush_list()
        elif _RULE.match(stripped):
            flush_para()
            flush_list()
            blocks.append('<hr style="border:0;border-top:1px solid #E5E7EB;margin:8px 0 20px;">')
        elif items and line[:1].isspace() and not (bullet or numbered):
            items[-1].append(stripped)
        elif button and "://" not in button.group(1):
            flush_para()
            flush_list()
            blocks.append(_button(button.group(1).strip(), button.group(2)))
        elif bullet or numbered:
            flush_para()
            tag = "ul" if bullet else "ol"
            if list_tag and list_tag != tag:
                flush_list()
            if not items and numbered:
                list_start = int(numbered.group(1))
            list_tag = tag
            items.append([(bullet or numbered).group(bullet and 1 or 2)])
        else:
            flush_list()
            para.append(stripped)
    flush_para()
    flush_list()
    flush_table()

    if sign_off:
        blocks.append(
            f'<p style="margin:8px 0 0;font-family:{FONT};font-size:15px;line-height:1.6;color:{TEXT};">'
            f'Regards,<br><strong>Team RoboKnights</strong></p>'
        )
    return "".join(blocks)


def render_html(subject, body):
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light"><title>{escape(subject)}</title></head>
<body style="margin:0;padding:0;background:{PAGE};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{PAGE};">
<tr><td align="center" style="padding:28px 12px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
  style="max-width:600px;background:#FFFFFF;border-radius:10px;overflow:hidden;border:1px solid #E5E7EB;">
  <tr><td align="center" style="padding:26px 24px 20px;background:#FFFFFF;">
    <img src="cid:{LOGO_CID}" width="260" alt="RoboKnights" style="display:block;width:260px;max-width:80%;height:auto;border:0;">
  </td></tr>
  <tr><td style="height:4px;line-height:4px;font-size:0;background:{GOLD};">&nbsp;</td></tr>
  <tr><td style="padding:22px 32px;background:{DARK};">
    <div style="font-family:{FONT};font-size:11px;letter-spacing:2px;font-weight:700;color:{GOLD};">ROBOKNIGHTS</div>
    <div style="margin-top:6px;font-family:{FONT};font-size:20px;line-height:1.35;font-weight:600;color:#FFFFFF;">{escape(subject)}</div>
  </td></tr>
  <tr><td style="padding:28px 32px 18px;">{body_to_html(body)}</td></tr>
  <tr><td style="padding:18px 32px 22px;background:#FAFAFA;border-top:1px solid #E5E7EB;">
    <div style="font-family:{FONT};font-size:12px;line-height:1.6;color:{MUTED};">
      <strong style="color:{TEXT};">RoboKnights</strong> &middot;
      <a href="{DASHBOARD_LINK}" style="color:{MUTED};">exun.co/rkdash</a><br>
      This is an automated message from the RoboKnights dashboard.
    </div>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""


def build_message(sender, to_email, subject, body, cc=(), attachments=()):
    # The finished email: plain text + branded HTML (with the logo inline),
    # plus any (filename, bytes) attachments.
    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(body, "plain", "utf-8"))
    alternative.attach(MIMEText(render_html(subject, body), "html", "utf-8"))

    related = MIMEMultipart("related")
    related.attach(alternative)
    with open(LOGO_PATH, "rb") as f:
        logo = MIMEImage(f.read(), "png")
    logo.add_header("Content-ID", f"<{LOGO_CID}>")
    logo.add_header("Content-Disposition", "inline", filename="roboknights.png")
    related.attach(logo)

    if attachments:
        msg = MIMEMultipart("mixed")
        msg.attach(related)
        for filename, data in attachments:
            part = MIMEApplication(data)
            part.add_header("Content-Disposition", "attachment", filename=filename)
            msg.attach(part)
    else:
        msg = related
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email
    if cc:
        msg["Cc"] = ", ".join(cc)
    return msg
