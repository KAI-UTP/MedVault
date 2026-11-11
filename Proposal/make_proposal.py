# Builds MedVault_Proposal.pdf (cover page + 2 pages).
# Edit GROUP_NAME / MEMBERS below, then run:  py make_proposal.py
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

GROUP_NAME = "VaultX"
MEMBERS = [
	("Chan Li Kai", "22010900"),
	("William Wong Xiao Kang", "22010943"),
]
COURSE = "RBB3023 Embedded and IoT Systems"
LECTURER = "Dr. Patrick Sebastian"
DATE = "November 2026"

OUT = Path(__file__).with_name("MedVault_Proposal.pdf")
LOGO = Path(__file__).with_name("utp_logo.png")

NAVY = colors.HexColor("#1F3A5F")
TEAL = colors.HexColor("#2A7F8E")
LIGHT = colors.HexColor("#EAF2F5")
GRID = colors.HexColor("#B8C7CF")

ss = getSampleStyleSheet()
body = ParagraphStyle("body", parent=ss["Normal"], fontName="Helvetica", fontSize=9.6,
	leading=12.4, spaceAfter=4)
bullet = ParagraphStyle("bullet", parent=body, leftIndent=11, bulletIndent=2, spaceAfter=1.5)
h1 = ParagraphStyle("h1", parent=body, fontName="Helvetica-Bold", fontSize=12.5, leading=15,
	textColor=NAVY, spaceBefore=7, spaceAfter=4)
cell = ParagraphStyle("cell", parent=body, fontSize=8.4, leading=10.2, spaceAfter=0)
cellb = ParagraphStyle("cellb", parent=cell, fontName="Helvetica-Bold")
head = ParagraphStyle("head", parent=cell, fontName="Helvetica-Bold", textColor=colors.white)
group_row = ParagraphStyle("grp", parent=cell, fontName="Helvetica-Bold", textColor=NAVY)


def P(text, style=body):
	return Paragraph(text, style)


def bullets(items):
	return [Paragraph(t, bullet, bulletText="•") for t in items]


def io_table(title_col, rows, widths):
	# rows: list of (group header string) or (a, b, c, d) tuples
	data = [[P(h, head) for h in ("#", title_col, "Board / pin", "What it controls" if title_col == "Input" else "What it shows / indicates")]]
	style = [
		("BACKGROUND", (0, 0), (-1, 0), NAVY),
		("GRID", (0, 0), (-1, -1), 0.4, GRID),
		("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
		("TOPPADDING", (0, 0), (-1, -1), 2.2),
		("BOTTOMPADDING", (0, 0), (-1, -1), 2.2),
		("LEFTPADDING", (0, 0), (-1, -1), 4),
		("RIGHTPADDING", (0, 0), (-1, -1), 4),
	]
	n = 0
	for r in rows:
		if isinstance(r, str):
			data.append([P(r, group_row), "", "", ""])
			i = len(data) - 1
			style += [("SPAN", (0, i), (-1, i)), ("BACKGROUND", (0, i), (-1, i), LIGHT)]
		else:
			n += 1
			data.append([P(str(n), cell), P(r[0], cellb), P(r[1], cell), P(r[2], cell)])
	t = Table(data, colWidths=widths, repeatRows=1)
	t.setStyle(TableStyle(style))
	return t


def block_diagram(width):
	h = 2.35 * cm
	d = Drawing(width, h)
	boxes = [
		("CABINET CONTROLLER", "Nu-LB-NUC140", "keypad, LCD, sensors, lock"),
		("SUPERVISOR UNIT", "LPC1768 + App Board", "approval, XTEA, RTOS"),
		("HOSPITAL GATEWAY", "Raspberry Pi 3 + webcam", "log, photos, web app"),
	]
	links = ["UART + XTEA", "USB serial"]
	phone_w = 2.1 * cm
	gap = 1.6 * cm
	bw = (width - phone_w - 3 * gap) / 3
	y, bh = 0.25 * cm, 1.75 * cm

	def arrow(x1, x2, ym, label, both=True):
		d.add(Line(x1 + 2, ym, x2 - 2, ym, strokeColor=TEAL, strokeWidth=1.3))
		d.add(Polygon([x2 - 2, ym, x2 - 7, ym + 3, x2 - 7, ym - 3], fillColor=TEAL, strokeColor=TEAL))
		if both:
			d.add(Polygon([x1 + 2, ym, x1 + 7, ym + 3, x1 + 7, ym - 3], fillColor=TEAL, strokeColor=TEAL))
		d.add(String((x1 + x2) / 2, ym + 5, label, fontName="Helvetica", fontSize=6.6,
			fillColor=TEAL, textAnchor="middle"))

	x = 0
	for i, (t1, t2, t3) in enumerate(boxes):
		d.add(Rect(x, y, bw, bh, rx=4, ry=4, fillColor=LIGHT, strokeColor=NAVY, strokeWidth=1))
		cx = x + bw / 2
		d.add(String(cx, y + bh - 0.5 * cm, t1, fontName="Helvetica-Bold", fontSize=7.2, fillColor=NAVY, textAnchor="middle"))
		d.add(String(cx, y + bh - 0.95 * cm, t2, fontName="Helvetica", fontSize=7.4, fillColor=colors.black, textAnchor="middle"))
		d.add(String(cx, y + bh - 1.38 * cm, t3, fontName="Helvetica-Oblique", fontSize=6.6, fillColor=colors.HexColor("#555555"), textAnchor="middle"))
		if i < 2:
			arrow(x + bw, x + bw + gap, y + bh / 2, links[i])
		x += bw + gap
	arrow(x - gap, x, y + bh / 2, "WiFi")
	d.add(Rect(x, y + 0.15 * cm, phone_w, bh - 0.3 * cm, rx=6, ry=6, fillColor=colors.white, strokeColor=NAVY, strokeWidth=1))
	d.add(String(x + phone_w / 2, y + bh / 2 + 2, "Phone / PC", fontName="Helvetica-Bold", fontSize=7.4, fillColor=NAVY, textAnchor="middle"))
	d.add(String(x + phone_w / 2, y + bh / 2 - 8, "web app, Telegram", fontName="Helvetica", fontSize=5.9, fillColor=colors.black, textAnchor="middle"))
	return d


def cover(story, width):
	title = ParagraphStyle("t", parent=body, fontName="Helvetica-Bold", fontSize=30, leading=36, alignment=TA_CENTER, textColor=NAVY)
	sub = ParagraphStyle("s", parent=body, fontName="Helvetica", fontSize=13.5, leading=18, alignment=TA_CENTER, textColor=TEAL)
	small = ParagraphStyle("c", parent=body, fontSize=11, leading=15, alignment=TA_CENTER)
	story += [
		Spacer(1, 0.6 * cm),
		Image(str(LOGO), width=2.6 * cm * 402 / 816 * 2, height=5.2 * cm),
		Spacer(1, 0.7 * cm),
		P(COURSE, small),
		P("Project Proposal", ParagraphStyle("pp", parent=small, fontName="Helvetica-Bold", fontSize=12.5, textColor=NAVY)),
		Spacer(1, 1.5 * cm),
		P("MedVault", title),
		Spacer(1, 0.3 * cm),
		P("Smart Two-Person-Authorised Cabinet for Controlled Drugs and Vaccines<br/>"
		  "with Encrypted Board-to-Board Approval and Camera-Verified Audit Trail", sub),
		Spacer(1, 1.8 * cm),
	]
	rows = [[P("Group name", ParagraphStyle("g1", parent=cellb, fontSize=10.5, leading=13)), P(GROUP_NAME, ParagraphStyle("g2", parent=cellb, fontSize=10.5, leading=13, textColor=NAVY))]]
	rows.append([P("Member name", head), P("Student ID", head)])
	for name, sid in MEMBERS:
		rows.append([P(name, ParagraphStyle("m", parent=cell, fontSize=10.5, leading=13)),
			P(sid, ParagraphStyle("m2", parent=cell, fontSize=10.5, leading=13))])
	t = Table(rows, colWidths=[8.5 * cm, 5 * cm])
	t.setStyle(TableStyle([
		("BACKGROUND", (0, 0), (-1, 0), LIGHT),
		("BACKGROUND", (0, 1), (-1, 1), NAVY),
		("GRID", (0, 0), (-1, -1), 0.5, GRID),
		("TOPPADDING", (0, 0), (-1, -1), 5),
		("BOTTOMPADDING", (0, 0), (-1, -1), 5),
		("LEFTPADDING", (0, 0), (-1, -1), 7),
	]))
	story += [t, Spacer(1, 2.2 * cm),
		P("Lecturer: " + LECTURER, small),
		P("Hardware: Nu-LB-NUC140 &nbsp;|&nbsp; NXP mbed LPC1768 + App Board &nbsp;|&nbsp; Raspberry Pi 3", small),
		P(DATE, small),
		PageBreak()]


def content(story, width):
	story.append(P("1. Project Description", h1))
	story.append(P(
		"Hospitals must store controlled drugs (e.g. morphine) and vaccines under strict rules: "
		"two staff must authorise every access (<b>two-person rule</b>), every opening must be recorded "
		"(<b>audit trail</b>), vaccines must stay at <b>2&ndash;8 &deg;C</b> (cold chain), and any forced entry "
		"must raise an alarm. Commercial systems are expensive. <b>MedVault</b> delivers these functions with "
		"two learning boards and a Raspberry Pi 3. The <b>NUC140</b> is the cabinet controller (nurse login, "
		"sensors, lock); the <b>LPC1768</b> is the supervisor's approval unit. The cabinet opens only when the "
		"nurse's PIN is correct <b>and</b> the supervisor approves with a joystick press plus a secret tilt "
		"gesture. The two boards exchange <b>XTEA-encrypted challenge-response messages with a rolling "
		"counter</b>, so a recorded unlock message cannot be replayed. The Raspberry Pi 3 is the hospital "
		"gateway: it logs every event with a <b>webcam photo</b>, serves a <b>web app</b> over WiFi and sends "
		"<b>Telegram alerts</b> to the phone."))
	story.append(block_diagram(width))
	story.append(Spacer(1, 3))
	story.append(P("<b>Deliverables</b>", body))
	story += bullets([
		"<b>Secure access:</b> staff ID + PIN on the cabinet, remote approval (joystick + tilt gesture), "
		"servo lock with 30 s auto-relock, 3-wrong-PIN lockout.",
		"<b>Cold-chain monitoring:</b> two temperature sensors (thermistor on NUC140, LM75B on LPC1768) "
		"with an adjustable alarm limit.",
		"<b>Tamper detection:</b> light sensor (door forced while locked), accelerometer (cabinet moved), "
		"replay-attack detection; any tamper locks the cabinet down.",
		"<b>Real-time OS:</b> LPC1768 runs Mbed OS 6 threads (link, security, sensors, UI, gateway) "
		"using queues, mutexes, a semaphore and event flags. NUC140 uses hardware timers and a watchdog.",
		"<b>Web app &amp; audit trail:</b> live temperatures, door state, event log with photos, alarm list, "
		"remote lockdown button; Telegram message with photo for every alarm.",
		"<b>Demonstration:</b> normal access, cold-chain alarm, break-in with photo alert, remote lockdown, "
		"and a live replay attack being blocked.",
	])

	w = [0.75 * cm, 3.6 * cm, 3.4 * cm, width - 7.75 * cm]
	story.append(P("2. Inputs and What They Control", h1))
	story.append(io_table("Input", [
		"Cabinet Controller: Nu-LB-NUC140",
		("3x3 keypad", "GPIO GPA0&ndash;5", "Staff ID and PIN entry, drawer selection (A = drugs, B = vaccines), menu navigation"),
		("SW_INT push button", "GPIO GPB15 (interrupt)", "Cancels a login / request; acknowledges an alarm"),
		("Thermistor (NTC 10k)", "ADC6 (GPA6)", "Cabinet temperature; triggers the cold-chain alarm when above the limit"),
		("LDR light sensor", "ADC5 (GPA5)", "Detects the door opening; light while LOCKED = forced entry alarm"),
		("VR1 potentiometer", "ADC7 (GPA7)", "Sets the temperature alarm limit (2.0&ndash;40.0 &deg;C, vaccines 8.0 &deg;C)"),
		("UART0 receive", "GPB0 (RX0)", "Encrypted approval replies and remote commands (lockdown / clear)"),
		"Supervisor Unit: LPC1768 + mbed App Board",
		("5-way joystick", "GPIO p12&ndash;p16", "Approve (centre) or reject (down) a request; scroll request details"),
		("MMA7660 accelerometer", "I2C p28 / p27", "Secret tilt gesture as second approval factor; shaking/moving = tamper alarm"),
		("LM75B temperature sensor", "I2C p28 / p27", "Backup cabinet temperature (redundant cold-chain check)"),
		("POT1 / POT2", "ADC p19 / p20", "Gesture sensitivity / tamper (movement) threshold"),
		("UART3 / USB receive", "p10 / USB serial", "Requests and status from the cabinet; commands from the gateway"),
		"Hospital Gateway: Raspberry Pi 3",
		("USB webcam", "USB", "Captures a photo for every access and alarm event"),
		("Web app buttons", "WiFi (browser)", "Remote lockdown, clear alarm, change temperature limit"),
	], w))

	story.append(KeepTogether([P("3. Outputs and What They Indicate", h1), io_table("Output", [
		"Cabinet Controller: Nu-LB-NUC140",
		("LCD 128x64", "SPI3", "Login prompts, drawer menu, &quot;Waiting approval&quot;, status, alarm messages (e.g. ATTACK BLOCKED)"),
		("4-digit 7-segment", "GPC4&ndash;7, GPE0&ndash;7", "Live temperature (&deg;C); door-open countdown; lockout countdown"),
		("SG90 servo lock", "GPA15 (Timer0/1 pulse)", "Physically locks / unlocks the cabinet door"),
		("RGB LED", "GPA12&ndash;14", "Green = locked &amp; normal, blue = waiting approval, red = alarm"),
		("4 LEDs", "GPC12&ndash;15", "LOCKED, OPEN, ALARM, LINK OK indicators"),
		("UART0 transmit", "GPB1 (TX0)", "Access requests, random challenge, status every 1 s"),
		"Supervisor Unit: LPC1768 + mbed App Board",
		("LCD 128x32", "SPI p5&ndash;p11", "Request details (nurse ID, drawer), gesture progress, both temperatures, alarms"),
		("RGB LED", "p23&ndash;p25", "Request pending (blue), approved (green), rejected / tamper (red)"),
		("LED1&ndash;LED4", "on-board", "Link activity, RTOS thread heartbeat, gesture step progress"),
		("USB serial transmit", "USB 115200", "Forwards all events and status to the gateway"),
		"Hospital Gateway: Raspberry Pi 3",
		("Web app (dashboard)", "WiFi", "Live temperature graph, door/lock state, audit log with photos, alarm list"),
		("Telegram alert", "Internet", "Instant phone message with photo for alarms (tamper, cold chain, attack)"),
		("Audit database", "SQLite on Pi", "Permanent record of who opened, when, approved by whom (CSV export)"),
	], w)]))


def footer(canvas, doc):
	if doc.page == 1:
		return
	canvas.saveState()
	canvas.setFont("Helvetica", 7.5)
	canvas.setFillColor(colors.HexColor("#777777"))
	canvas.drawString(2 * cm, 1.1 * cm, "MedVault Project Proposal  |  " + COURSE)
	canvas.drawRightString(A4[0] - 2 * cm, 1.1 * cm, "Page %d" % (doc.page - 1))
	canvas.restoreState()


def main():
	doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=1.9 * cm, rightMargin=1.9 * cm,
		topMargin=1.6 * cm, bottomMargin=1.7 * cm, title="MedVault Project Proposal",
		author="Chan Li Kai")
	width = A4[0] - doc.leftMargin - doc.rightMargin
	story = []
	cover(story, width)
	content(story, width)
	doc.build(story, onFirstPage=footer, onLaterPages=footer)
	print("wrote", OUT)


if __name__ == "__main__":
	main()
