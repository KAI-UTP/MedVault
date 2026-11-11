# Builds MedVault_Proposal.docx (Word version of the proposal: cover page + 2 pages).
# Same text as make_proposal.py.  Run:  py make_proposal_docx.py
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

GROUP_NAME = "VaultX"
MEMBERS = [
	("Chan Li Kai", "22010900"),
	("William Wong Xiao Kang", "22010943"),
]
COURSE = "RBB3023 Embedded and IoT Systems"
LECTURER = "Dr. Patrick Sebastian"
DATE = "November 2026"

HERE = Path(__file__).resolve().parent
OUT = HERE / "MedVault_Proposal.docx"
LOGO = HERE / "utp_logo.png"

NAVY = RGBColor(0x1F, 0x3A, 0x5F)
TEAL = RGBColor(0x2A, 0x7F, 0x8E)
GREY = RGBColor(0x55, 0x55, 0x55)
NAVY_HEX, LIGHT_HEX, GRID_HEX = "1F3A5F", "EAF2F5", "B8C7CF"
FONT = "Arial"

doc = Document()


# ---------------------------------------------------------------- helpers
def shade(cell, hex_color):
	tcPr = cell._tc.get_or_add_tcPr()
	shd = OxmlElement("w:shd")
	shd.set(qn("w:val"), "clear")
	shd.set(qn("w:color"), "auto")
	shd.set(qn("w:fill"), hex_color)
	tcPr.append(shd)


def borders(table, color=GRID_HEX, size=4, inside=True):
	tblPr = table._tbl.tblPr
	b = OxmlElement("w:tblBorders")
	edges = ["top", "left", "bottom", "right"] + (["insideH", "insideV"] if inside else [])
	for edge in ["top", "left", "bottom", "right", "insideH", "insideV"]:
		e = OxmlElement(f"w:{edge}")
		if edge in edges:
			e.set(qn("w:val"), "single")
			e.set(qn("w:sz"), str(size))
			e.set(qn("w:color"), color)
		else:
			e.set(qn("w:val"), "nil")
		b.append(e)
	tblPr.append(b)


def cell_margins(table, top=50, bottom=50, left=90, right=90):
	tblPr = table._tbl.tblPr
	m = OxmlElement("w:tblCellMar")
	for k, v in (("top", top), ("left", left), ("bottom", bottom), ("right", right)):
		e = OxmlElement(f"w:{k}")
		e.set(qn("w:w"), str(v))
		e.set(qn("w:type"), "dxa")
		m.append(e)
	tblPr.append(m)


def fixed_widths(table, widths_cm):
	table.autofit = False
	tblPr = table._tbl.tblPr
	lay = OxmlElement("w:tblLayout")
	lay.set(qn("w:type"), "fixed")
	tblPr.append(lay)
	for row in table.rows:
		for i, w in enumerate(widths_cm):
			row.cells[i].width = Cm(w)
	grid = table._tbl.tblGrid
	for i, gc in enumerate(grid.findall(qn("w:gridCol"))):
		gc.set(qn("w:w"), str(int(widths_cm[i] / 2.54 * 1440)))


def row_flags(row, header=False, keep=True):
	trPr = row._tr.get_or_add_trPr()
	if header:
		h = OxmlElement("w:tblHeader")
		trPr.append(h)
	if keep:
		c = OxmlElement("w:cantSplit")
		trPr.append(c)


def para(container, text="", size=10, bold=False, color=None, align=None, before=0, after=4,
	italic=False, keep_next=False):
	p = container.add_paragraph()
	fmt = p.paragraph_format
	fmt.space_before = Pt(before)
	fmt.space_after = Pt(after)
	fmt.keep_with_next = keep_next
	if align:
		p.alignment = align
	if text:
		add_runs(p, text, size, bold, color, italic)
	return p


def add_runs(p, text, size=10, bold=False, color=None, italic=False):
	"""Text with **bold** parts."""
	parts = text.split("**")
	for i, part in enumerate(parts):
		if not part:
			continue
		r = p.add_run(part)
		r.font.name = FONT
		r.font.size = Pt(size)
		r.bold = bold or (i % 2 == 1)
		r.italic = italic
		if color is not None:
			r.font.color.rgb = color
	return p


def cell_text(cell, text, size=8.5, bold=False, color=None, align=None, italic=False):
	cell.text = ""
	p = cell.paragraphs[0]
	p.paragraph_format.space_after = Pt(0)
	p.paragraph_format.space_before = Pt(0)
	if align:
		p.alignment = align
	add_runs(p, text, size, bold, color, italic)
	cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
	return p


def heading(text):
	p = para(doc, text, size=13, bold=True, color=NAVY, before=8, after=4, keep_next=True)
	return p


# ---------------------------------------------------------------- page setup
sec = doc.sections[0]
sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
sec.left_margin = sec.right_margin = Cm(1.9)
sec.top_margin, sec.bottom_margin = Cm(1.6), Cm(1.6)
sec.footer_distance = Cm(0.8)
sec.different_first_page_header_footer = True
pg = OxmlElement("w:pgNumType")
pg.set(qn("w:start"), "0")			# cover = page 0, content pages = 1, 2
sec._sectPr.append(pg)
WIDTH = 21.0 - 3.8

normal = doc.styles["Normal"]
normal.font.name = FONT
normal.font.size = Pt(10)
normal.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)

# footer on content pages : left text, right "Page N"
fp = sec.footer.paragraphs[0]
add_runs(fp, f"MedVault Project Proposal  |  {COURSE}", size=7.5, color=GREY)
r = fp.add_run()
ptab = OxmlElement("w:ptab")			# jump to the right margin
ptab.set(qn("w:relativeTo"), "margin")
ptab.set(qn("w:alignment"), "right")
ptab.set(qn("w:leader"), "none")
r._r.append(ptab)
add_runs(fp, "Page ", size=7.5, color=GREY)
r = fp.add_run()
r.font.size = Pt(7.5)
r.font.color.rgb = GREY
for tag, txt in (("begin", None), (None, "PAGE"), ("end", None)):
	if tag:
		e = OxmlElement("w:fldChar")
		e.set(qn("w:fldCharType"), tag)
	else:
		e = OxmlElement("w:instrText")
		e.set(qn("xml:space"), "preserve")
		e.text = txt
	r._r.append(e)

# ---------------------------------------------------------------- cover page
para(doc, after=0, before=6)
p = para(doc, align=WD_ALIGN_PARAGRAPH.CENTER, after=14)
p.add_run().add_picture(str(LOGO), height=Cm(5.0))
para(doc, COURSE, size=11, align=WD_ALIGN_PARAGRAPH.CENTER, after=2)
para(doc, "Project Proposal", size=12.5, bold=True, color=NAVY, align=WD_ALIGN_PARAGRAPH.CENTER, after=0)
para(doc, "MedVault", size=32, bold=True, color=NAVY, align=WD_ALIGN_PARAGRAPH.CENTER, before=42, after=6)
para(doc, "Smart Two-Person-Authorised Cabinet for Controlled Drugs and Vaccines", size=13.5,
	color=TEAL, align=WD_ALIGN_PARAGRAPH.CENTER, after=0)
para(doc, "with Encrypted Board-to-Board Approval and Camera-Verified Audit Trail", size=13.5,
	color=TEAL, align=WD_ALIGN_PARAGRAPH.CENTER, after=46)

t = doc.add_table(rows=2 + len(MEMBERS), cols=2)
t.alignment = WD_TABLE_ALIGNMENT.CENTER
borders(t)
cell_margins(t, 90, 90, 130, 130)
fixed_widths(t, [8.5, 5.0])
cell_text(t.cell(0, 0), "Group name", 10.5, bold=True)
cell_text(t.cell(0, 1), GROUP_NAME, 10.5, bold=True, color=NAVY)
shade(t.cell(0, 0), LIGHT_HEX)
shade(t.cell(0, 1), LIGHT_HEX)
for i, h in enumerate(("Member name", "Student ID")):
	cell_text(t.cell(1, i), h, 9, bold=True, color=RGBColor(255, 255, 255))
	shade(t.cell(1, i), NAVY_HEX)
for r_i, (name, sid) in enumerate(MEMBERS, start=2):
	cell_text(t.cell(r_i, 0), name, 10.5)
	cell_text(t.cell(r_i, 1), sid, 10.5)

para(doc, after=0, before=60)
para(doc, "Lecturer: " + LECTURER, size=11, align=WD_ALIGN_PARAGRAPH.CENTER, after=2)
para(doc, "Hardware: Nu-LB-NUC140  |  NXP mbed LPC1768 + App Board  |  Raspberry Pi 3", size=11,
	align=WD_ALIGN_PARAGRAPH.CENTER, after=2)
p = para(doc, DATE, size=11, align=WD_ALIGN_PARAGRAPH.CENTER, after=0)
p.add_run().add_break(WD_BREAK.PAGE)

# ---------------------------------------------------------------- 1. description
heading("1. Project Description")
para(doc,
	"Hospitals must store controlled drugs (e.g. morphine) and vaccines under strict rules: "
	"two staff must authorise every access (**two-person rule**), every opening must be recorded "
	"(**audit trail**), vaccines must stay at **2–8 °C** (cold chain), and any forced entry "
	"must raise an alarm. Commercial systems are expensive. **MedVault** delivers these functions with "
	"two learning boards and a Raspberry Pi 3. The **NUC140** is the cabinet controller (nurse login, "
	"sensors, lock); the **LPC1768** is the supervisor's approval unit. The cabinet opens only when the "
	"nurse's PIN is correct **and** the supervisor approves with a joystick press plus a secret tilt "
	"gesture. The two boards exchange **XTEA-encrypted challenge-response messages with a rolling "
	"counter**, so a recorded unlock message cannot be replayed. The Raspberry Pi 3 is the hospital "
	"gateway: it logs every event with a **webcam photo**, serves a **web app** over WiFi and sends "
	"**Telegram alerts** to the phone.", size=9.6, after=6)
for p in doc.paragraphs[-1:]:
	p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

# block diagram as a small editable table : box, link, box, link, box, link, phone
dg = doc.add_table(rows=2, cols=7)
dg.alignment = WD_TABLE_ALIGNMENT.CENTER
borders(dg, inside=False, size=0, color="FFFFFF")
tblPr = dg._tbl.tblPr
for e in tblPr.findall(qn("w:tblBorders")):
	tblPr.remove(e)
cell_margins(dg, 30, 30, 40, 40)
box_w, link_w, phone_w = 3.35, 1.55, 2.35
fixed_widths(dg, [box_w, link_w, box_w, link_w, box_w, link_w, phone_w])
boxes = [
	("CABINET CONTROLLER", "Nu-LB-NUC140", "keypad, LCD, sensors, lock"),
	("SUPERVISOR UNIT", "LPC1768 + App Board", "approval, XTEA, RTOS"),
	("HOSPITAL GATEWAY", "Raspberry Pi 3 + webcam", "log, photos, web app"),
	("Phone / PC", "web app, Telegram", ""),
]
links = ["UART + XTEA", "USB serial", "WiFi"]
for col in range(7):
	top, bot = dg.cell(0, col), dg.cell(1, col)
	top.merge(bot)
	c = dg.cell(0, col)
	c.text = ""
	if col % 2 == 0:
		t1, t2, t3 = boxes[col // 2]
		p = c.paragraphs[0]
		p.alignment = WD_ALIGN_PARAGRAPH.CENTER
		p.paragraph_format.space_after = Pt(1)
		add_runs(p, t1, 7.5, bold=True, color=NAVY)
		p2 = c.add_paragraph()
		p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
		p2.paragraph_format.space_after = Pt(1)
		add_runs(p2, t2, 7.5)
		if t3:
			p3 = c.add_paragraph()
			p3.alignment = WD_ALIGN_PARAGRAPH.CENTER
			p3.paragraph_format.space_after = Pt(0)
			add_runs(p3, t3, 6.8, italic=True, color=GREY)
		shade(c, LIGHT_HEX if col < 6 else "FFFFFF")
		tcPr = c._tc.get_or_add_tcPr()
		tb = OxmlElement("w:tcBorders")
		for edge in ("top", "left", "bottom", "right"):
			e = OxmlElement(f"w:{edge}")
			e.set(qn("w:val"), "single")
			e.set(qn("w:sz"), "8")
			e.set(qn("w:color"), NAVY_HEX)
			tb.append(e)
		tcPr.append(tb)
	else:
		p = c.paragraphs[0]
		p.alignment = WD_ALIGN_PARAGRAPH.CENTER
		p.paragraph_format.space_after = Pt(0)
		add_runs(p, links[col // 2], 6.8, color=TEAL)
		p2 = c.add_paragraph()
		p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
		p2.paragraph_format.space_after = Pt(0)
		add_runs(p2, "◄───►", 9, bold=True, color=TEAL)
	c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER

para(doc, "Deliverables", size=9.6, bold=True, before=6, after=2, keep_next=True)
for text in [
	"**Secure access:** staff ID + PIN on the cabinet, remote approval (joystick + tilt gesture), "
	"servo lock with 30 s auto-relock, 3-wrong-PIN lockout.",
	"**Cold-chain monitoring:** two temperature sensors (thermistor on NUC140, LM75B on LPC1768) "
	"with an adjustable alarm limit.",
	"**Tamper detection:** light sensor (door forced while locked), accelerometer (cabinet moved), "
	"replay-attack detection; any tamper locks the cabinet down.",
	"**Real-time OS:** LPC1768 runs Mbed OS 6 threads (link, security, sensors, UI, gateway) "
	"using queues, mutexes, a semaphore and event flags. NUC140 uses hardware timers and a watchdog.",
	"**Web app & audit trail:** live temperatures, door state, event log with photos, alarm list, "
	"remote lockdown button; Telegram message with photo for every alarm.",
	"**Demonstration:** normal access, cold-chain alarm, break-in with photo alert, remote lockdown, "
	"and a live replay attack being blocked.",
]:
	p = doc.add_paragraph(style="List Bullet")
	p.paragraph_format.space_after = Pt(1)
	p.paragraph_format.left_indent = Cm(0.6)
	add_runs(p, text, 9.6)


# ---------------------------------------------------------------- I/O tables
def keep_together(t):
	# every row except the last keeps with the next one : the table does not split
	for row in t.rows[:-1]:
		for c in row.cells:
			for p in c.paragraphs:
				p.paragraph_format.keep_with_next = True


def io_table(title_col, last_col, rows):
	widths = [0.75, 3.6, 3.4, WIDTH - 7.75]
	t = doc.add_table(rows=1, cols=4)
	borders(t)
	cell_margins(t, 30, 30, 80, 80)
	for i, h in enumerate(("#", title_col, "Board / pin", last_col)):
		cell_text(t.rows[0].cells[i], h, 8.5, bold=True, color=RGBColor(255, 255, 255))
		shade(t.rows[0].cells[i], NAVY_HEX)
	row_flags(t.rows[0], header=True)
	n = 0
	for r in rows:
		row = t.add_row()
		row_flags(row)
		if isinstance(r, str):
			c = row.cells[0].merge(row.cells[3])
			cell_text(c, r, 8.5, bold=True, color=NAVY)
			shade(c, LIGHT_HEX)
		else:
			n += 1
			cell_text(row.cells[0], str(n), 8.5)
			cell_text(row.cells[1], r[0], 8.5, bold=True)
			cell_text(row.cells[2], r[1], 8.5)
			cell_text(row.cells[3], r[2], 8.5)
	fixed_widths(t, widths)
	return t


heading("2. Inputs and What They Control")
io_table("Input", "What it controls", [
	"Cabinet Controller: Nu-LB-NUC140",
	("3x3 keypad", "GPIO GPA0–5", "Staff ID and PIN entry, drawer selection (A = drugs, B = vaccines), menu navigation"),
	("SW_INT push button", "GPIO GPB15 (interrupt)", "Cancels a login / request; acknowledges an alarm"),
	("Thermistor (NTC 10k)", "ADC6 (GPA6)", "Cabinet temperature; triggers the cold-chain alarm when above the limit"),
	("LDR light sensor", "ADC5 (GPA5)", "Detects the door opening; light while LOCKED = forced entry alarm"),
	("VR1 potentiometer", "ADC7 (GPA7)", "Sets the temperature alarm limit (2.0–40.0 °C, vaccines 8.0 °C)"),
	("UART0 receive", "GPB0 (RX0)", "Encrypted approval replies and remote commands (lockdown / clear)"),
	"Supervisor Unit: LPC1768 + mbed App Board",
	("5-way joystick", "GPIO p12–p16", "Approve (centre) or reject (down) a request; scroll request details"),
	("MMA7660 accelerometer", "I2C p28 / p27", "Secret tilt gesture as second approval factor; shaking/moving = tamper alarm"),
	("LM75B temperature sensor", "I2C p28 / p27", "Backup cabinet temperature (redundant cold-chain check)"),
	("POT1 / POT2", "ADC p19 / p20", "Gesture sensitivity / tamper (movement) threshold"),
	("UART3 / USB receive", "p10 / USB serial", "Requests and status from the cabinet; commands from the gateway"),
	"Hospital Gateway: Raspberry Pi 3",
	("USB webcam", "USB", "Captures a photo for every access and alarm event"),
	("Web app buttons", "WiFi (browser)", "Remote lockdown, clear alarm, change temperature limit"),
])

heading("3. Outputs and What They Indicate")
out_t = io_table("Output", "What it shows / indicates", [
	"Cabinet Controller: Nu-LB-NUC140",
	("LCD 128x64", "SPI3", "Login prompts, drawer menu, \"Waiting approval\", status, alarm messages (e.g. ATTACK BLOCKED)"),
	("4-digit 7-segment", "GPC4–7, GPE0–7", "Live temperature (°C); door-open countdown; lockout countdown"),
	("SG90 servo lock", "GPA15 (Timer0/1 pulse)", "Physically locks / unlocks the cabinet door"),
	("RGB LED", "GPA12–14", "Green = locked & normal, blue = waiting approval, red = alarm"),
	("4 LEDs", "GPC12–15", "LOCKED, OPEN, ALARM, LINK OK indicators"),
	("UART0 transmit", "GPB1 (TX0)", "Access requests, random challenge, status every 1 s"),
	"Supervisor Unit: LPC1768 + mbed App Board",
	("LCD 128x32", "SPI p5–p11", "Request details (nurse ID, drawer), gesture progress, both temperatures, alarms"),
	("RGB LED", "p23–p25", "Request pending (blue), approved (green), rejected / tamper (red)"),
	("LED1–LED4", "on-board", "Link activity, RTOS thread heartbeat, gesture step progress"),
	("USB serial transmit", "USB 115200", "Forwards all events and status to the gateway"),
	"Hospital Gateway: Raspberry Pi 3",
	("Web app (dashboard)", "WiFi", "Live temperature graph, door/lock state, audit log with photos, alarm list"),
	("Telegram alert", "Internet", "Instant phone message with photo for alarms (tamper, cold chain, attack)"),
	("Audit database", "SQLite on Pi", "Permanent record of who opened, when, approved by whom (CSV export)"),
])
keep_together(out_t)

doc.core_properties.title = "MedVault Project Proposal"
doc.core_properties.author = "Chan Li Kai, William Wong Xiao Kang"
doc.save(OUT)
print("wrote", OUT)
