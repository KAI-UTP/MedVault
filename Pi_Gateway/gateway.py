#!/usr/bin/env python3
"""
MedVault : Hospital Gateway (Raspberry Pi 3)          RBB3023 Project, group VaultX

  - reads the supervisor unit (LPC1768) over USB serial 115200
  - keeps the audit trail in SQLite (medvault.db)
  - takes a webcam photo for access and alarm events (photos/)
  - sends Telegram alerts with the photo
  - serves the MedVault web app over WiFi (http://<pi-ip>:8080)
  - can push commands back (LOCKDOWN / CLEAR / LIMIT) and run the replay test

Run :  python3 gateway.py            (real boards, port found automatically)
       python3 gateway.py --sim      (no boards : simulated cabinet, for testing the web app)
Settings in config.json (made on the first run).
"""
import argparse
import csv
import io
import json
import mimetypes
import queue
import random
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
PHOTOS = HERE / "photos"
CONFIG = HERE / "config.json"

DEFAULT_CONFIG = {
	"web_port": 8080,
	"password": "medvault",
	"serial_port": "auto",
	"camera_index": 0,
	"camera_enabled": True,
	"telegram_token": "",
	"telegram_chat_id": "",
	"notify_access": False,
	"cabinet_name": "Cabinet 1 - Ward 3",
	"staff": {"1357": "Aina", "2468": "William", "3579": "Li Kai", "9999": "Admin"},
}

CAB_STATES = ["IDLE", "LOGIN", "PIN", "DRAWER", "WAIT", "OPEN", "LOCKOUT", "LOCKDOWN", "DIAG"]
ALARM_BITS = [
	(0x01, "COLD", "Cold chain: temperature above limit"),
	(0x02, "FORCED", "Door forced open while locked"),
	(0x04, "MOVED", "Cabinet moved / shaken"),
	(0x08, "PINLOCK", "PIN lockout (3 wrong PINs)"),
	(0x10, "ATTACK", "Attack blocked (bad / replayed message)"),
	(0x20, "LOCKDOWN", "Cabinet in LOCKDOWN"),
	(0x40, "AJAR", "Door left open"),
	(0x80, "NOLINK", "Cabinet lost the supervisor link"),
]
ATTACK_REASON = {1: "replayed / wrong challenge", 2: "old counter", 3: "wrong key / corrupted"}
LOCKDOWN_REASON = {1: "door forced", 2: "cabinet moved", 3: "remote command"}

# name : (severity, take photo, telegram alert)
EVENTS = {
	"BOOT": ("info", False, False),
	"LOGIN": ("info", False, False),
	"WRONG_PIN": ("warn", True, False),
	"PIN_LOCKOUT": ("alarm", True, True),
	"REQUEST": ("info", True, False),
	"SUP_APPROVE": ("info", False, False),
	"SUP_REJECT": ("warn", False, False),
	"TILT_WRONG": ("warn", False, False),
	"APPROVED": ("info", False, False),
	"REJECTED": ("warn", False, False),
	"TIMEOUT": ("warn", False, False),
	"CANCEL": ("info", False, False),
	"DOOR_OPEN": ("info", True, False),
	"RELOCK": ("info", False, False),
	"COLD_ON": ("alarm", False, True),
	"COLD_OFF": ("info", False, False),
	"FORCED": ("alarm", True, True),
	"MOVED": ("alarm", True, True),
	"ATTACK": ("alarm", True, True),
	"LOCKDOWN": ("alarm", True, True),
	"CLEARED": ("info", False, False),
	"DOOR_AJAR": ("alarm", True, True),
	"LIMIT": ("info", False, False),
}


def now_str():
	return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_config():
	cfg = dict(DEFAULT_CONFIG)
	if CONFIG.exists():
		cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
	else:
		CONFIG.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
	return cfg


# ---------------------------------------------------------------- database
class DB:
	def __init__(self, path):
		self.lock = threading.Lock()
		self.con = sqlite3.connect(path, check_same_thread=False)
		self.con.row_factory = sqlite3.Row
		self.con.executescript("""
			CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts TEXT, name TEXT, severity TEXT,
				staff INTEGER, staff_name TEXT, drawer TEXT, detail TEXT, photo TEXT);
			CREATE TABLE IF NOT EXISTS readings (ts TEXT, temp REAL, lm75 REAL, lim REAL);
			CREATE TABLE IF NOT EXISTS raw (id INTEGER PRIMARY KEY, ts TEXT, dir TEXT, hex TEXT);
		""")

	def exec(self, sql, args=()):
		with self.lock:
			cur = self.con.execute(sql, args)
			self.con.commit()
			return cur.lastrowid

	def rows(self, sql, args=()):
		with self.lock:
			return [dict(r) for r in self.con.execute(sql, args).fetchall()]


# ---------------------------------------------------------------- camera
class Camera:
	"""Keeps grabbing frames so a snapshot is instant and fresh."""

	def __init__(self, index, enabled):
		self.frame = None
		self.ok = False
		self.lock = threading.Lock()
		if not enabled:
			return
		try:
			import cv2
			self.cv2 = cv2
		except ImportError:
			print("[cam] OpenCV not installed : no photos")
			return
		threading.Thread(target=self._run, args=(index,), daemon=True).start()

	def _run(self, index):
		cap = self.cv2.VideoCapture(index)
		cap.set(self.cv2.CAP_PROP_FRAME_WIDTH, 640)
		cap.set(self.cv2.CAP_PROP_FRAME_HEIGHT, 480)
		if not cap.isOpened():
			print("[cam] no webcam found : no photos")
			return
		print("[cam] webcam ready")
		while True:
			ok, f = cap.read()
			if ok:
				with self.lock:
					self.frame = f
					self.ok = True
			else:
				time.sleep(0.5)

	def snapshot(self, label):
		with self.lock:
			if self.frame is None:
				return None
			f = self.frame.copy()
		cv2 = self.cv2
		h, w = f.shape[:2]
		cv2.rectangle(f, (0, h - 34), (w, h), (0, 0, 0), -1)
		cv2.putText(f, f"MedVault  {label}  {now_str()}", (8, h - 11), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
		name = datetime.now().strftime("%Y%m%d_%H%M%S_") + re.sub(r"[^A-Za-z0-9_]", "", label) + ".jpg"
		PHOTOS.mkdir(exist_ok=True)
		cv2.imwrite(str(PHOTOS / name), f, [cv2.IMWRITE_JPEG_QUALITY, 80])
		return name


# ---------------------------------------------------------------- telegram
class Telegram:
	def __init__(self, token, chat_id):
		self.token, self.chat_id = token, chat_id
		self.q = queue.Queue()
		if token and chat_id:
			threading.Thread(target=self._run, daemon=True).start()
			print("[telegram] alerts on")
		else:
			print("[telegram] no token / chat id in config.json : alerts off")

	def send(self, text, photo=None):
		if self.token and self.chat_id:
			self.q.put((text, photo))

	def _run(self):
		import requests
		base = f"https://api.telegram.org/bot{self.token}"
		while True:
			text, photo = self.q.get()
			try:
				if photo and (PHOTOS / photo).exists():
					with open(PHOTOS / photo, "rb") as fh:
						requests.post(f"{base}/sendPhoto", data={"chat_id": self.chat_id, "caption": text}, files={"photo": fh}, timeout=15)
				else:
					requests.post(f"{base}/sendMessage", data={"chat_id": self.chat_id, "text": text}, timeout=15)
			except Exception as e:
				print("[telegram] send failed:", e)


# ---------------------------------------------------------------- serial link
class SerialLink:
	"""USB serial to the LPC1768. Reconnects by itself if the cable is pulled or the board is reflashed."""

	def __init__(self, port, on_line):
		import serial
		self.serial = serial
		self.want_port = port
		self.ser = None
		self.wlock = threading.Lock()
		self.on_line = on_line
		if not self._open():
			raise SystemExit("No mbed board found. Plug in the LPC1768 or set serial_port in config.json (or use --sim).")
		threading.Thread(target=self._run, daemon=True).start()

	def _find(self):
		if self.want_port != "auto":
			return self.want_port
		import serial.tools.list_ports
		ports = serial.tools.list_ports.comports()
		# 1st choice : the LPC1768's own USB on the App Board mini-USB (Mbed USBSerial 1F00:2012)
		# 2nd choice : the mbed interface chip's serial port (0D28)
		for vid, pid in ((0x1F00, 0x2012), (0x0D28, None)):
			for p in ports:
				if p.vid == vid and (pid is None or p.pid == pid):
					return p.device
		return None

	def _open(self):
		port = self._find()
		if not port:
			return False
		try:
			self.ser = self.serial.Serial(port, 115200, timeout=0.2)
		except Exception as e:
			print(f"[serial] cannot open {port}: {e}")
			return False
		print(f"[serial] connected to {port}")
		self.write("HELLO")
		return True

	def _run(self):
		buf = b""
		while True:
			try:
				buf += self.ser.read(256)
			except Exception as e:
				print("[serial] link lost:", e, "- retrying")
				try:
					self.ser.close()
				except Exception:
					pass
				buf = b""
				while not self._open():
					time.sleep(2)
				continue
			while b"\n" in buf:
				line, buf = buf.split(b"\n", 1)
				line = line.decode("ascii", "replace").strip()
				if line:
					try:
						self.on_line(line)
					except Exception as e:	# one bad line must not stop the gateway
						print("[gateway] error on line", line, ":", e)

	def write(self, line):
		try:
			with self.wlock:
				self.ser.write((line + "\n").encode())
		except Exception as e:
			raise ValueError(f"supervisor unit not connected ({e})")


class SimLink:
	"""Pretends to be the two boards, so the web app can be tried without hardware."""

	def __init__(self, on_line):
		self.on_line = on_line
		self.state, self.alarms, self.ctr = 0, 0, 0
		self.temp, self.limit, self.lm75, self.heat = 26.0, 30.0, 25.6, 0.0
		self.door, self.last_approval, self.remote = 0, None, False
		self.lock = threading.Lock()
		threading.Thread(target=self._run, daemon=True).start()
		self.emit("LOG,simulation mode")
		self.emit("EV,BOOT,0,0")

	def emit(self, line):
		self.on_line(line)

	def _run(self):
		while True:
			with self.lock:
				self.heat = max(0.0, self.heat - 0.25)
				self.temp += (26.0 + self.heat - self.temp) * 0.3 + random.uniform(-0.05, 0.05)
				self.lm75 += (25.6 + self.heat * 0.6 - self.lm75) * 0.2 + random.uniform(-0.03, 0.03)
				cold = self.temp > self.limit
				if cold and not self.alarms & 1:
					self.alarms |= 1
					self.emit(f"EV,COLD_ON,0,{int(self.temp)}")
				elif not cold and self.alarms & 1 and self.temp < self.limit - 0.5:
					self.alarms &= ~1
					self.emit(f"EV,COLD_OFF,0,{int(self.temp)}")
				if self.alarms & 0x10 and time.time() > self.attack_until:
					self.alarms &= ~0x10
				flags = self.door | (2 if self.state == 5 else 0) | 4 | (8 if self.remote else 0)
				self.emit(f"ST,{CAB_STATES[self.state]},{flags},{round(self.temp * 10)},{round(self.limit * 10)},{20 if not self.door else 85},{self.alarms},{round(self.lm75 * 10)},{self.ctr}")
			time.sleep(1)

	attack_until = 0

	def action(self, act):
		threading.Thread(target=self._action, args=(act,), daemon=True).start()

	def _action(self, act):
		e = self.emit
		if act == "access":
			e("EV,LOGIN,1357,0")
			with self.lock:
				self.state = 4
			nonce = random.getrandbits(32)
			e(f"RAW,N2S,A55207055B41{nonce:08X}00")
			e("RQ,1357,A")
			e("EV,REQUEST,1357,65")
			time.sleep(3)
			with self.lock:
				self.ctr += 1
				frame = "A54108" + secrets.token_hex(8).upper() + "7E"
				self.last_approval = frame
			e(f"RAW,S2N,{frame}")
			e("EV,SUP_APPROVE,1357,A")
			with self.lock:
				self.state = 5
			e("EV,APPROVED,1357,65")
			time.sleep(2)
			with self.lock:
				self.door = 1
			e("EV,DOOR_OPEN,1357,65")
			time.sleep(4)
			with self.lock:
				self.door, self.state = 0, 0
			e("EV,RELOCK,1357,65")
		elif act == "wrongpin":
			for i in (1, 2, 3):
				e(f"EV,WRONG_PIN,2468,{i}")
				time.sleep(0.8)
			with self.lock:
				self.state, self.alarms = 6, self.alarms | 8
			e("EV,PIN_LOCKOUT,2468,0")
			time.sleep(8)
			with self.lock:
				self.state, self.alarms = 0, self.alarms & ~8
		elif act == "forced":
			e("EV,FORCED,0,0")
			with self.lock:
				self.state, self.alarms = 7, self.alarms | 0x22
			e("EV,LOCKDOWN,0,1")
		elif act == "moved":
			e("EV,MOVED,0,17")
			with self.lock:
				self.ctr += 1
				self.state, self.alarms = 7, self.alarms | 0x24
			e("RAW,S2N,A54308" + secrets.token_hex(8).upper() + "11")
			e("EV,LOCKDOWN,0,2")
		elif act == "heat":
			with self.lock:
				self.heat = 9.0

	def write(self, line):
		e = self.emit
		with self.lock:
			if line == "CMD,LOCKDOWN":
				self.ctr += 1
				self.state, self.alarms = 7, self.alarms | 0x20
				e("EV,LOCKDOWN,0,3")
			elif line == "CMD,CLEAR":
				self.ctr += 1
				self.alarms &= ~0x2E
				if self.state in (6, 7):
					self.state = 0
					e("EV,CLEARED,0,2")
			elif line.startswith("CMD,LIMIT,"):
				self.ctr += 1
				self.limit = int(line[10:]) / 10
				self.remote = True
				e("EV,LIMIT,0,1")
			elif line.startswith("INJ,"):
				e(f"LOG,injected {len(line[4:]) // 2} raw bytes")
				self.alarms |= 0x10
				self.attack_until = time.time() + 6
				e("EV,ATTACK,0,1")


# ---------------------------------------------------------------- gateway core
class Gateway:
	def __init__(self, cfg, sim):
		self.cfg = cfg
		self.db = DB(HERE / "medvault.db")
		self.cam = Camera(cfg["camera_index"], cfg["camera_enabled"])
		self.tg = Telegram(cfg["telegram_token"], cfg["telegram_chat_id"])
		self.lock = threading.Lock()
		self.live = {"state": "?", "door_open": False, "unlocked": False, "temp": None, "lm75": None,
			"limit": None, "ldr": 0, "alarms": 0, "counter": 0, "last_seen": 0, "request": None,
			"remote_limit": False}
		self.last_reading = 0
		self.sim = sim
		self.link = SimLink(self.on_line) if sim else SerialLink(cfg["serial_port"], self.on_line)

	def staff_name(self, sid):
		return self.cfg["staff"].get(str(sid), "-" if not sid else f"ID {sid}")

	# ---- lines from the supervisor
	def on_line(self, line):
		parts = line.split(",")
		kind = parts[0]
		try:
			if kind == "ST" and len(parts) >= 9:
				self.on_status(parts)
			elif kind == "EV" and len(parts) >= 4:
				self.on_event(parts[1], int(parts[2] or 0), parts[3])
			elif kind == "RQ" and len(parts) >= 3:
				with self.lock:
					self.live["request"] = {"staff": int(parts[1]), "name": self.staff_name(int(parts[1])), "drawer": parts[2], "ts": now_str()}
			elif kind == "RAW" and len(parts) >= 3:
				self.db.exec("INSERT INTO raw (ts, dir, hex) VALUES (?,?,?)", (now_str(), parts[1], parts[2]))
			elif kind == "LOG":
				print("[board]", ",".join(parts[1:]))
		except (ValueError, IndexError) as e:
			print("[gateway] bad line", line, e)

	def on_status(self, p):
		flags = int(p[2])
		temp = int(p[3]) / 10 if flags & 4 else None
		with self.lock:
			L = self.live
			L.update(state=p[1], door_open=bool(flags & 1), unlocked=bool(flags & 2), remote_limit=bool(flags & 8),
				temp=temp, limit=int(p[4]) / 10, ldr=int(p[5]), alarms=int(p[6]), lm75=int(p[7]) / 10,
				counter=int(p[8]), last_seen=time.time())
			if L["state"] not in ("WAIT",):
				L["request"] = None if L["state"] != "DRAWER" else L["request"]
		if time.time() - self.last_reading >= 5:
			self.last_reading = time.time()
			self.db.exec("INSERT INTO readings VALUES (?,?,?,?)", (now_str(), temp, int(p[7]) / 10, int(p[4]) / 10))

	def detail(self, name, info):
		d = info
		drawer = ""
		if info in ("65", "66"):
			drawer = chr(int(info))
		elif info in ("A", "B"):
			drawer = info
		text = {
			"WRONG_PIN": f"wrong PIN, attempt {info} of 3",
			"PIN_LOCKOUT": "3 wrong PINs : keypad locked 60 s",
			"REQUEST": f"asks to open drawer {drawer}",
			"SUP_APPROVE": f"supervisor approved (joystick + tilt code)",
			"SUP_REJECT": "supervisor rejected",
			"TILT_WRONG": f"wrong tilt code ({info} of 3)",
			"APPROVED": f"encrypted approval accepted, drawer {drawer} unlocked",
			"REJECTED": "request rejected",
			"TIMEOUT": "no approval in 25 s",
			"CANCEL": "request cancelled",
			"DOOR_OPEN": f"door opened (drawer {drawer})",
			"RELOCK": "door closed, relocked",
			"COLD_ON": f"temperature {info} C over the limit",
			"COLD_OFF": "temperature back to normal",
			"FORCED": "light inside while LOCKED : door forced",
			"MOVED": f"accelerometer: cabinet moved (level {info})",
			"ATTACK": f"message rejected : {ATTACK_REASON.get(int(info) if info.isdigit() else 0, info)}",
			"LOCKDOWN": f"lockdown : {LOCKDOWN_REASON.get(int(info) if info.isdigit() else 0, info)}",
			"CLEARED": "lockdown cleared by " + ("admin PIN" if info == "1" else "remote command"),
			"DOOR_AJAR": "door left open after 30 s",
			"LIMIT": "limit set from web app" if info == "1" else "limit back to VR1 knob",
			"LOGIN": "staff logged in",
			"BOOT": "cabinet started",
		}.get(name, info)
		return text, drawer

	def on_event(self, name, staff, info):
		sev, photo_on, alert = EVENTS.get(name, ("info", False, False))
		detail, drawer = self.detail(name, info)
		who = self.staff_name(staff)
		photo = self.cam.snapshot(name) if photo_on else None
		self.db.exec("INSERT INTO events (ts,name,severity,staff,staff_name,drawer,detail,photo) VALUES (?,?,?,?,?,?,?,?)",
			(now_str(), name, sev, staff, who, drawer, detail, photo))
		print(f"[event] {name:12s} {who:8s} {detail}")
		if name in ("APPROVED", "REJECTED", "TIMEOUT", "CANCEL", "LOCKDOWN", "SUP_REJECT"):
			with self.lock:
				self.live["request"] = None
		if alert or (self.cfg.get("notify_access") and name in ("APPROVED", "DOOR_OPEN")):
			icon = "\U0001F6A8" if sev == "alarm" else "ℹ️"
			text = f"{icon} MedVault {name.replace('_', ' ')}\n{self.cfg['cabinet_name']}\n{detail}"
			if staff:
				text += f"\nStaff: {who} ({staff})"
			text += f"\n{datetime.now().strftime('%H:%M:%S, %d %b %Y')}"
			self.tg.send(text, photo)

	# ---- commands from the web app
	def command(self, cmd, value=None):
		if cmd == "LOCKDOWN":
			self.link.write("CMD,LOCKDOWN")
		elif cmd == "CLEAR":
			self.link.write("CMD,CLEAR")
		elif cmd == "LIMIT":
			v = int(round(float(value) * 10))
			if not 20 <= v <= 400:
				raise ValueError("limit must be 2.0 ~ 40.0 C")
			self.link.write(f"CMD,LIMIT,{v}")
		else:
			raise ValueError("unknown command")

	def replay_test(self, tamper=False):
		"""Take the last approval the supervisor really sent and push it again."""
		rows = self.db.rows("SELECT * FROM raw WHERE dir='S2N' AND hex LIKE 'A54108%' ORDER BY id DESC LIMIT 1")
		if not rows:
			raise ValueError("no recorded approval yet : do one normal access first")
		hexs = rows[0]["hex"]
		if tamper:		# flip one bit in the encrypted part, fix the CRC so only the crypto can catch it
			b = bytearray.fromhex(hexs)
			b[5] ^= 0x01
			b[-1] = crc8(b[1:-1])
			hexs = b.hex().upper()
		self.link.write("INJ," + hexs)
		return {"sent": hexs, "recorded_at": rows[0]["ts"]}

	def state(self):
		with self.lock:
			L = dict(self.live)
		age = time.time() - L["last_seen"] if L["last_seen"] else None
		L["online"] = age is not None and age < 4
		L["active_alarms"] = [{"code": c, "text": t} for bit, c, t in ALARM_BITS if L["alarms"] & bit]
		L["sim"] = self.sim
		L["cabinet_name"] = self.cfg["cabinet_name"]
		last = self.db.rows("SELECT id FROM events ORDER BY id DESC LIMIT 1")
		L["last_event_id"] = last[0]["id"] if last else 0
		cnt = self.db.rows("SELECT COUNT(*) AS n FROM events WHERE name='ATTACK'")
		L["attacks_blocked"] = cnt[0]["n"]
		return L


def crc8(data, crc=0):
	for x in data:
		crc ^= x
		for _ in range(8):
			crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
	return crc


# ---------------------------------------------------------------- web server
SESSIONS = set()
GW = None


class Handler(BaseHTTPRequestHandler):
	server_version = "MedVault/1.0"

	def log_message(self, fmt, *args):
		pass

	def send(self, code, body, ctype="application/json", headers=None):
		if isinstance(body, (dict, list)):
			body = json.dumps(body)
		if isinstance(body, str):
			body = body.encode()
		self.send_response(code)
		self.send_header("Content-Type", ctype)
		self.send_header("Content-Length", str(len(body)))
		self.send_header("Cache-Control", "no-store")
		for k, v in (headers or {}).items():
			self.send_header(k, v)
		self.end_headers()
		self.wfile.write(body)

	def authed(self):
		m = re.search(r"mv_session=([A-Za-z0-9_\-]+)", self.headers.get("Cookie", ""))
		return bool(m and m.group(1) in SESSIONS)

	def body_json(self):
		n = int(self.headers.get("Content-Length") or 0)
		raw = self.rfile.read(n) if n else b"{}"
		try:
			return json.loads(raw or b"{}")
		except json.JSONDecodeError:
			return {}

	def static(self, path):
		f = (WEB / path).resolve()
		if WEB.resolve() not in f.parents or not f.is_file():
			return self.send(404, {"error": "not found"})
		ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
		if f.suffix == ".webmanifest":
			ctype = "application/manifest+json"
		self.send(200, f.read_bytes(), ctype)

	def do_GET(self):
		u = urlparse(self.path)
		q = parse_qs(u.query)
		path = u.path
		if path in ("/manifest.webmanifest", "/icon.svg", "/login.html", "/style.css"):
			return self.static(path[1:])
		if not self.authed():
			if path.startswith("/api/") or path.startswith("/photos/"):
				return self.send(401, {"error": "login"})
			return self.static("login.html")
		if path == "/":
			return self.static("index.html")
		if path.startswith("/photos/"):
			name = path[8:]
			if not re.fullmatch(r"[A-Za-z0-9_]+\.jpg", name) or not (PHOTOS / name).exists():
				return self.send(404, {"error": "no photo"})
			return self.send(200, (PHOTOS / name).read_bytes(), "image/jpeg")
		if path == "/api/state":
			return self.send(200, GW.state())
		if path == "/api/events":
			limit = min(int(q.get("limit", ["200"])[0]), 2000)
			sev = q.get("severity", [""])[0]
			if sev:
				rows = GW.db.rows("SELECT * FROM events WHERE severity=? ORDER BY id DESC LIMIT ?", (sev, limit))
			else:
				rows = GW.db.rows("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
			return self.send(200, rows)
		if path == "/api/readings":
			mins = int(q.get("minutes", ["30"])[0])
			rows = GW.db.rows("SELECT * FROM readings WHERE ts >= datetime('now','localtime',?) ORDER BY ts", (f"-{mins} minutes",))
			return self.send(200, rows)
		if path == "/api/raw":
			return self.send(200, GW.db.rows("SELECT * FROM raw ORDER BY id DESC LIMIT 50"))
		if path == "/api/export.csv":
			rows = GW.db.rows("SELECT ts,name,severity,staff,staff_name,drawer,detail,photo FROM events ORDER BY id")
			out = io.StringIO()
			w = csv.writer(out)
			w.writerow(["time", "event", "severity", "staff_id", "staff", "drawer", "detail", "photo"])
			for r in rows:
				w.writerow(r.values())
			return self.send(200, out.getvalue(), "text/csv",
				{"Content-Disposition": f"attachment; filename=medvault_audit_{datetime.now():%Y%m%d}.csv"})
		return self.send(404, {"error": "not found"})

	def do_POST(self):
		path = urlparse(self.path).path
		if path == "/login":
			n = int(self.headers.get("Content-Length") or 0)
			form = parse_qs(self.rfile.read(n).decode())
			if form.get("password", [""])[0] == GW.cfg["password"]:
				tok = secrets.token_urlsafe(24)
				SESSIONS.add(tok)
				self.send_response(303)
				self.send_header("Set-Cookie", f"mv_session={tok}; Path=/; HttpOnly; SameSite=Strict; Max-Age=604800")
				self.send_header("Location", "/")
				self.end_headers()
				return
			self.send_response(303)
			self.send_header("Location", "/login.html?bad=1")
			self.end_headers()
			return
		if not self.authed():
			return self.send(401, {"error": "login"})
		data = self.body_json()
		try:
			if path == "/api/cmd":
				GW.command(data.get("cmd", ""), data.get("value"))
				return self.send(200, {"ok": True})
			if path == "/api/replay_test":
				return self.send(200, {"ok": True, **GW.replay_test(bool(data.get("tamper")))})
			if path == "/api/sim" and GW.sim:
				GW.link.action(data.get("act", ""))
				return self.send(200, {"ok": True})
			if path == "/logout":
				m = re.search(r"mv_session=([A-Za-z0-9_\-]+)", self.headers.get("Cookie", ""))
				SESSIONS.discard(m.group(1) if m else "")
				return self.send(200, {"ok": True})
		except ValueError as e:
			return self.send(400, {"error": str(e)})
		return self.send(404, {"error": "not found"})


def main():
	global GW
	ap = argparse.ArgumentParser(description="MedVault hospital gateway")
	ap.add_argument("--sim", action="store_true", help="simulate the boards (no hardware needed)")
	ap.add_argument("--port", help="serial port (overrides config.json)")
	ap.add_argument("--web", type=int, help="web port (overrides config.json)")
	ap.add_argument("--no-camera", action="store_true", help="do not open the webcam")
	args = ap.parse_args()

	cfg = load_config()
	if args.port:
		cfg["serial_port"] = args.port
	if args.no_camera:
		cfg["camera_enabled"] = False
	PHOTOS.mkdir(exist_ok=True)
	GW = Gateway(cfg, args.sim)
	port = args.web or cfg["web_port"]
	srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
	print(f"[web] MedVault web app on http://0.0.0.0:{port}  (password in config.json)")
	try:
		srv.serve_forever()
	except KeyboardInterrupt:
		print("bye")


if __name__ == "__main__":
	main()
