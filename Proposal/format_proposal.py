# Keeps the user's cover page from MedVault_Proposal_backup_before_format.docx and rebuilds
# the 2 content pages : Times New Roman 12, line spacing 1.5, justified, black text
# (table header row : white text on navy).   Run:  py format_proposal.py
from pathlib import Path

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

HERE = Path(__file__).resolve().parent
SRC = HERE / "MedVault_Proposal_backup_before_format.docx"
OUT = HERE / "MedVault_Proposal.docx"

FONT = "Times New Roman"
SIZE = 12
BLACK = RGBColor(0, 0, 0)
WHITE = RGBColor(255, 255, 255)
NAVY_HEX, GRID_HEX = "1F3A5F", "808080"

doc = Document(SRC)
body = doc.element.body

# ---- keep the cover : everything up to and including the paragraph with the page break
children = list(body.iterchildren())
cut = None
for i, el in enumerate(children):
	if any(b.get(qn("w:type")) == "page" for b in el.iter(qn("w:br"))):
		cut = i
		break
if cut is None:
	raise SystemExit("cover page break not found")
for el in children[cut + 1:]:
	if el.tag != qn("w:sectPr"):
		body.remove(el)

sec = doc.sections[-1]
WIDTH = (sec.page_width - sec.left_margin - sec.right_margin) / 360000	# cm


# ---------------------------------------------------------------- helpers
def fmt(p, align=WD_ALIGN_PARAGRAPH.JUSTIFY, before=0, after=0, keep=False):
	f = p.paragraph_format
	f.line_spacing = 1.5
	f.space_before = Pt(before)
	f.space_after = Pt(after)
	f.keep_with_next = keep
	p.alignment = align
	return p


def runs(p, text, bold=False, color=BLACK):
	"""Text with **bold** parts, Times New Roman 12."""
	for i, part in enumerate(text.split("**")):
		if not part:
			continue
		r = p.add_run(part)
		r.font.name = FONT
		r._r.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), FONT)
		r.font.size = Pt(SIZE)
		r.font.color.rgb = color
		r.bold = bold or (i % 2 == 1)
	return p


def para(text, bold=False, before=0, after=0, keep=False, style=None):
	p = doc.add_paragraph(style=style)
	fmt(p, before=before, after=after, keep=keep)
	return runs(p, text, bold)


def heading(text):
	return para(text, bold=True, before=6, after=0, keep=True)


def shade(cell, hex_color):
	shd = OxmlElement("w:shd")
	shd.set(qn("w:val"), "clear")
	shd.set(qn("w:color"), "auto")
	shd.set(qn("w:fill"), hex_color)
	cell._tc.get_or_add_tcPr().append(shd)


def table(header, rows, widths):
	t = doc.add_table(rows=1, cols=len(header))
	t.alignment = WD_TABLE_ALIGNMENT.CENTER
	t.autofit = False
	tblPr = t._tbl.tblPr
	b = OxmlElement("w:tblBorders")
	for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
		e = OxmlElement(f"w:{edge}")
		e.set(qn("w:val"), "single")
		e.set(qn("w:sz"), "4")
		e.set(qn("w:color"), GRID_HEX)
		b.append(e)
	tblPr.append(b)
	lay = OxmlElement("w:tblLayout")
	lay.set(qn("w:type"), "fixed")
	tblPr.append(lay)
	mar = OxmlElement("w:tblCellMar")
	for k, v in (("top", 0), ("left", 80), ("bottom", 0), ("right", 80)):
		e = OxmlElement(f"w:{k}")
		e.set(qn("w:w"), str(v))
		e.set(qn("w:type"), "dxa")
		mar.append(e)
	tblPr.append(mar)

	def fill(cells, values, head=False):
		for c, v in zip(cells, values):
			c.text = ""
			p = c.paragraphs[0]
			fmt(p, align=WD_ALIGN_PARAGRAPH.LEFT, keep=head)	# justify stretches short cells
			runs(p, v, bold=head, color=WHITE if head else BLACK)
			c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
			if head:
				shade(c, NAVY_HEX)

	fill(t.rows[0].cells, header, head=True)
	trPr = t.rows[0]._tr.get_or_add_trPr()
	trPr.append(OxmlElement("w:tblHeader"))
	for r in rows:
		row = t.add_row()
		row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
		fill(row.cells, r)
	for row in t.rows:
		for i, w in enumerate(widths):
			row.cells[i].width = Cm(w)
	for i, gc in enumerate(t._tbl.tblGrid.findall(qn("w:gridCol"))):
		gc.set(qn("w:w"), str(int(widths[i] / 2.54 * 1440)))
	return t


# ---------------------------------------------------------------- content
heading("1. Project Description")
para("Hospitals keep controlled drugs (e.g. morphine) and vaccines under strict rules: two staff must "
	"approve every access (**two-person rule**), every opening must be recorded (**audit trail**), vaccines "
	"must stay at **2–8 °C** (cold chain) and any forced entry must raise an alarm. **MedVault** "
	"delivers these functions with two learning boards and a Raspberry Pi 3. The **NUC140** is the cabinet "
	"controller (nurse login, sensors, servo lock) and the **LPC1768** is the supervisor's approval unit. "
	"The cabinet opens only when the nurse's PIN is correct **and** the supervisor approves with a joystick "
	"press plus a secret tilt gesture. The boards exchange **XTEA-encrypted challenge-response messages with "
	"a rolling counter**, so a recorded unlock message cannot be replayed. The Raspberry Pi 3 is the hospital "
	"gateway: it logs every event with a **webcam photo**, serves a **web app** over WiFi and sends "
	"**Telegram alerts** to the phone.")
para("**System:** NUC140 cabinet ↔ (UART, encrypted) ↔ LPC1768 supervisor ↔ (USB) ↔ "
	"Raspberry Pi 3 gateway ↔ (WiFi) ↔ phone / PC.", before=3)

para("Deliverables", bold=True, before=3, keep=True)
for text in [
	"**Secure access:** staff ID + PIN, supervisor approval (joystick + tilt gesture), servo lock with "
	"30 s auto-relock and 3-wrong-PIN lockout.",
	"**Cold-chain monitoring:** thermistor (NUC140) and LM75B (LPC1768) with an adjustable alarm limit.",
	"**Tamper detection:** light sensor (door forced), accelerometer (cabinet moved) and replay-attack "
	"detection, each triggering a lockdown.",
	"**Real-time OS:** Mbed OS 6 threads with queues, mutex, semaphore and event flags on the LPC1768; "
	"hardware timers and a watchdog on the NUC140.",
	"**Web app and audit trail:** live temperatures, door state, event log with photos, remote lockdown "
	"and Telegram alerts.",
]:
	p = doc.add_paragraph(style="List Bullet")
	fmt(p)
	p.paragraph_format.left_indent = Cm(0.63)
	p.paragraph_format.first_line_indent = Cm(-0.63)
	runs(p, text)

W = [0.9, 3.8, 4.6, WIDTH - 9.3]

heading("2. Inputs and What They Control")
table(["No", "Input", "Board / pin", "What it controls"], [
	["1", "3x3 keypad", "NUC140 GPA0–5", "Staff ID, PIN and drawer selection"],
	["2", "SW_INT button", "NUC140 GPB15", "Cancels a request, acknowledges an alarm"],
	["3", "Thermistor", "NUC140 ADC6", "Cabinet temperature for the cold-chain alarm"],
	["4", "LDR light sensor", "NUC140 ADC5", "Door open; light while locked = forced entry"],
	["5", "VR1 potentiometer", "NUC140 ADC7", "Temperature alarm limit (2–40 °C)"],
	["6", "5-way joystick", "LPC1768 p12–p16", "Approve (centre) or reject (down) a request"],
	["7", "MMA7660 accelerometer", "LPC1768 I2C", "Tilt-code approval; shaking = tamper alarm"],
	["8", "LM75B sensor", "LPC1768 I2C", "Backup temperature reading"],
	["9", "POT1 / POT2", "LPC1768 p19 / p20", "Tilt and tamper sensitivity"],
	["10", "UART receive", "NUC140 GPB0, LPC1768 p10", "Encrypted requests, approvals and commands"],
	["11", "USB webcam", "Raspberry Pi 3", "Photo for every access and alarm event"],
	["12", "Web app buttons", "Raspberry Pi 3 (WiFi)", "Remote lockdown, clear alarm, set limit"],
], W)

heading("3. Outputs and What They Indicate")
table(["No", "Output", "Board / pin", "What it shows / indicates"], [
	["1", "LCD 128x64", "NUC140 SPI3", "Login prompts, status and alarm messages"],
	["2", "4-digit 7-segment", "NUC140 GPC4–7 / GPE0–7", "Temperature, door and lockout countdowns"],
	["3", "SG90 servo lock", "NUC140 GPA15", "Locks / unlocks the cabinet door"],
	["4", "RGB LED + 4 LEDs", "NUC140 GPA12–14 / GPC12–15", "Locked, waiting, open, alarm and link status"],
	["5", "LCD 128x32", "LPC1768 SPI", "Request details, tilt progress, temperatures"],
	["6", "RGB + LED1–4", "LPC1768 p23–p25", "Pending, approved, rejected, RTOS heartbeat"],
	["7", "Web app dashboard", "Raspberry Pi 3 (WiFi)", "Live graph, door state, audit log with photos"],
	["8", "Telegram alert", "Raspberry Pi 3", "Phone message with photo for every alarm"],
	["9", "Audit database", "Raspberry Pi 3 (SQLite)", "Who opened, when and who approved"],
], W)

doc.save(OUT)
print("wrote", OUT)
