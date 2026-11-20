//
// MedVault : Supervisor Unit (mbed LPC1768 + Application Board, Mbed OS 6 RTOS)
//                                                    RBB3023 Project, group VaultX
//
// The cabinet (NUC140) sends an access request with a random challenge (nonce).
// The supervisor approves with joystick CENTRE + the secret tilt code (or rejects
// with DOWN). The reply is XTEA encrypted : nonce + rolling counter + decision,
// so the cabinet can tell a fresh answer from a recorded one (replay attack).
//
// While no request is open, the board is "armed" : if the accelerometer feels
// the cabinet being moved / shaken, it orders a LOCKDOWN.
// Everything is forwarded to the Raspberry Pi gateway over USB serial.
//
// RTOS design
//   main thread  : security / logic. Waits on the inbox Mail queue, builds the
//                  encrypted replies, pings the cabinet every 1 s
//   t_nuc        : reads UART3 from the cabinet, checks frames, posts to inbox
//   t_sensor     : MMA7660 at 50 Hz (tilt code / tamper), LM75B at 1 Hz
//   t_ui         : LCD + joystick + LEDs, redraws at 5 Hz or at once when the
//                  main thread releases the redraw Semaphore
//   t_pi         : USB serial to the Pi : sends lines from the pi_out Mail queue,
//                  reads commands from the Pi
//   EventFlags   : mode / alarm bits shared by all threads
//   Mutex        : guards the shared status structure
//
// Pins
//   UART3 to NUC140 : p9 TX -> NUC GPB0, p10 RX <- NUC GPB1, GND - GND, 9600
//   USB serial      : to the Raspberry Pi, 115200
//   LCD             : p5 p6 p7 p8 p11 (lcd_c12832.h)
//   Joystick        : down p12, left p13, centre p14, up p15, right p16
//   POT1 p19        : tilt sensitivity      POT2 p20 : tamper sensitivity
//   RGB LED         : p23 R, p24 G, p25 B (low = on)
//   MMA7660 + LM75B : I2C SDA p28, SCL p27
//   LED1 UI heartbeat, LED2 cabinet link, LED3 tilt code step, LED4 sensor heartbeat
//
// Pi lines (out) : ST,... / EV,name,staff,info / RQ,staff,drawer / RAW,dir,hex / LOG,text
// Pi lines (in)  : CMD,LOCKDOWN  CMD,CLEAR  CMD,LIMIT,<x10>  INJ,<hex>  (INJ = replay test)
//
#include "mbed.h"
#include <math.h>
#include "lcd_c12832.h"

#define ALLOW_INJECT 1		// gateway may push raw bytes to the cabinet (security test only)
#define GW_LINK_USB  1		// 1 = gateway on the App Board mini-USB (LPC1768's own USB, shows as a
				//     normal COM port on Windows); 0 = mbed interface serial (USBTX/USBRX)

// ---------------- shared protocol (same as NUC140) ----------------
static const uint32_t KEY[4] = { 0x4D656456, 0x61756C74, 0x56614B58, 0x26202026 };
#define SOF		0xA5
#define MAGIC		0x5A
#define CMD_MAGIC	0xC3
enum { CMD_LOCKDOWN = 1, CMD_CLEAR, CMD_LIMIT };
enum { LD_FORCED = 1, LD_MOVED, LD_REMOTE };

static const char *const EV_NAME[] = { "?", "BOOT", "LOGIN", "WRONG_PIN", "PIN_LOCKOUT",
	"REQUEST", "APPROVED", "REJECTED", "TIMEOUT", "DOOR_OPEN", "RELOCK", "COLD_ON",
	"COLD_OFF", "FORCED", "ATTACK", "LOCKDOWN", "CLEARED", "DOOR_AJAR", "LIMIT", "CANCEL" };
#define N_EV (sizeof(EV_NAME) / sizeof(EV_NAME[0]))

static const char *const ST_NAME[] = { "IDLE", "LOGIN", "PIN", "DRAWER", "WAIT", "OPEN",
	"LOCKOUT", "LOCKDOWN", "DIAG" };

struct StaffName { uint16_t id; const char *name; };
static const StaffName STAFF[] = { { 1357, "AINA" }, { 2468, "WILLIAM" }, { 3579, "LIKAI" }, { 9999, "ADMIN" } };

// cabinet alarm bits (from the status frame)
#define AL_COLD		0x01
#define AL_FORCED	0x02
#define AL_MOVED	0x04
#define AL_PINLOCK	0x08
#define AL_ATTACK	0x10
#define AL_LOCKDOWN	0x20
#define AL_AJAR		0x40

// secret tilt code (L = left, R = right, F = forward, B = back)
static const char TILT_CODE[] = "LLF";
#define TILT_STEPS	3
#define TILT_TIME_S	12
#define REQ_TIME_S	25

// ---------------- hardware ----------------
BufferedSerial nuc(p9, p10, 9600);
#if GW_LINK_USB
#include "USBSerial.h"
#else
BufferedSerial pc(USBTX, USBRX, 115200);
#endif
I2C i2c(p28, p27);
LCD_C12832 lcd;
DigitalIn joy_down(p12, PullDown), joy_left(p13, PullDown), joy_centre(p14, PullDown),
	  joy_up(p15, PullDown), joy_right(p16, PullDown);
AnalogIn pot1(p19), pot2(p20);
DigitalOut rgb_r(p23, 1), rgb_g(p24, 1), rgb_b(p25, 1);
DigitalOut led1(LED1), led2(LED2), led3(LED3), led4(LED4);

#define ACC_ADDR	(0x4C << 1)
#define LM75_ADDR	(0x48 << 1)

// ---------------- RTOS objects ----------------
enum { M_FRAME = 1, M_JOY, M_TILT_STEP, M_TILT_OK, M_TILT_BAD, M_TAMPER, M_PI_CMD, M_PI_INJ };

struct Msg {
	uint8_t kind;
	uint8_t type;		// frame type / joystick key / command
	uint8_t len;
	uint8_t data[40];
	int16_t arg;
};
struct Line { char s[100]; };

Mail<Msg, 16>  inbox;
Mail<Line, 24> pi_out;
EventFlags     flags;
Mutex          st_mtx;
Semaphore      redraw(0, 1);

#define F_REQUEST	(1UL << 0)	// a request is waiting for the supervisor
#define F_TILT		(1UL << 1)	// tilt code entry running
#define F_ARMED		(1UL << 2)	// tamper detection active
#define F_LINK		(1UL << 3)	// cabinet link OK
#define F_ALARM		(1UL << 4)	// cabinet reports an alarm

Thread t_nuc(osPriorityAboveNormal, 2048);
Thread t_sensor(osPriorityNormal, 2048);
Thread t_ui(osPriorityNormal, 3072);
Thread t_pi(osPriorityBelowNormal, 4096);	// holds the USBSerial object

// ---------------- shared status (guarded by st_mtx) ----------------
struct Shared {
	// from the cabinet
	uint8_t cab_state = 0, cab_flags = 0, cab_alarms = 0, ldr = 0;
	int16_t temp_x10 = 0, limit_x10 = 80;
	// own sensors
	int16_t lm75_x10 = 0;
	int8_t ax = 0, ay = 0, az = 0;
	char tilt_now = '-';
	// request
	uint16_t req_staff = 0;
	uint8_t req_drawer = 'A';
	uint32_t req_nonce = 0;
	uint64_t req_end = 0;
	uint64_t tilt_end = 0;
	uint8_t tilt_done = 0, tilt_fails = 0;
	// info
	uint16_t counter = 0;
	char last_event[22] = "started";
	uint32_t frames_in = 0, frames_bad = 0;
} sh;

uint64_t now_ms()
{
	return Kernel::Clock::now().time_since_epoch().count();
}

const char *staff_name(uint16_t id)
{
	for (auto &s : STAFF) if (s.id == id) return s.name;
	return "UNKNOWN";
}

void wake_ui() { redraw.release(); }

// ---------------- Pi output ----------------

void pi_line(const char *fmt, ...)
{
	Line *l = pi_out.try_alloc();
	if (!l) return;				// Pi too slow : drop
	va_list ap;
	va_start(ap, fmt);
	vsnprintf(l->s, sizeof(l->s) - 2, fmt, ap);
	va_end(ap);
	strcat(l->s, "\n");
	pi_out.put(l);
}

void hex_str(char *out, const uint8_t *b, int n)
{
	static const char h[] = "0123456789ABCDEF";
	for (int i = 0; i < n; i++) { out[2 * i] = h[b[i] >> 4]; out[2 * i + 1] = h[b[i] & 15]; }
	out[2 * n] = 0;
}

void set_event(const char *txt)
{
	st_mtx.lock();
	snprintf(sh.last_event, sizeof(sh.last_event), "%s", txt);
	st_mtx.unlock();
	wake_ui();
}

// ---------------- crypto + frames ----------------

void xtea_encrypt(uint8_t *b)
{
	uint32_t v0 = ((uint32_t)b[0] << 24) | ((uint32_t)b[1] << 16) | ((uint32_t)b[2] << 8) | b[3];
	uint32_t v1 = ((uint32_t)b[4] << 24) | ((uint32_t)b[5] << 16) | ((uint32_t)b[6] << 8) | b[7];
	uint32_t sum = 0, delta = 0x9E3779B9;
	for (int i = 0; i < 32; i++)
	{
		v0 += (((v1 << 4) ^ (v1 >> 5)) + v1) ^ (sum + KEY[sum & 3]);
		sum += delta;
		v1 += (((v0 << 4) ^ (v0 >> 5)) + v0) ^ (sum + KEY[(sum >> 11) & 3]);
	}
	b[0] = v0 >> 24; b[1] = v0 >> 16; b[2] = v0 >> 8; b[3] = v0;
	b[4] = v1 >> 24; b[5] = v1 >> 16; b[6] = v1 >> 8; b[7] = v1;
}

uint8_t crc8(const uint8_t *p, int n, uint8_t crc = 0)
{
	while (n--)
	{
		crc ^= *p++;
		for (int i = 0; i < 8; i++) crc = (crc & 0x80) ? (crc << 1) ^ 0x07 : crc << 1;
	}
	return crc;
}

// only the main thread writes to the cabinet
void send_frame(uint8_t type, const uint8_t *d, uint8_t len, bool log_raw)
{
	uint8_t buf[16];
	buf[0] = SOF; buf[1] = type; buf[2] = len;
	memcpy(buf + 3, d, len);
	buf[3 + len] = crc8(buf + 1, len + 2);
	nuc.write(buf, len + 4);
	if (log_raw)
	{
		char hex[40];
		hex_str(hex, buf, len + 4);
		pi_line("RAW,S2N,%s", hex);
	}
}

uint16_t next_counter()
{
	st_mtx.lock();
	uint16_t c = ++sh.counter;
	st_mtx.unlock();
	return c;
}

void send_decision(bool yes)
{
	uint8_t b[8];
	st_mtx.lock();
	uint32_t n = sh.req_nonce;
	uint16_t staff = sh.req_staff;
	uint8_t drawer = sh.req_drawer;
	st_mtx.unlock();
	uint16_t c = next_counter();

	b[0] = n >> 24; b[1] = n >> 16; b[2] = n >> 8; b[3] = n;
	b[4] = c >> 8; b[5] = c; b[6] = yes ? 'Y' : 'N'; b[7] = MAGIC;
	xtea_encrypt(b);
	send_frame('A', b, 8, true);

	flags.clear(F_REQUEST | F_TILT);		// tamper re-arms when the cabinet reports IDLE again
	pi_line("EV,%s,%u,%c", yes ? "SUP_APPROVE" : "SUP_REJECT", staff, drawer);
	set_event(yes ? "approved" : "rejected");
}

void send_command(uint8_t cmd, uint16_t arg)
{
	uint8_t b[8];
	uint16_t c = next_counter();
	b[0] = CMD_MAGIC; b[1] = cmd; b[2] = arg >> 8; b[3] = arg; b[4] = c >> 8; b[5] = c; b[6] = 0; b[7] = MAGIC;
	xtea_encrypt(b);
	send_frame('C', b, 8, true);
}

// ---------------- thread : cabinet UART ----------------

void nuc_thread()
{
	uint8_t c, type = 0, len = 0, pos = 0, st = 0;
	uint8_t buf[16];

	while (true)
	{
		if (nuc.read(&c, 1) != 1) continue;	// blocking read
		switch (st)
		{
		case 0: if (c == SOF) st = 1; break;
		case 1: type = c; st = 2; break;
		case 2: len = c; pos = 0; st = (len <= 12) ? (len ? 3 : 4) : 0; break;
		case 3: buf[pos++] = c; if (pos >= len) st = 4; break;
		case 4:
			st = 0;
			{
				uint8_t hdr[2] = { type, len };
				uint8_t crc = crc8(buf, len, crc8(hdr, 2));
				st_mtx.lock();
				if (crc == c) sh.frames_in++; else sh.frames_bad++;
				st_mtx.unlock();
				if (crc != c) break;
				Msg *m = inbox.try_alloc();
				if (!m) break;
				m->kind = M_FRAME; m->type = type; m->len = len;
				memcpy(m->data, buf, len);
				inbox.put(m);
				led2 = !led2;
			}
			break;
		}
	}
}

// ---------------- thread : sensors ----------------

bool acc_read(int8_t &x, int8_t &y, int8_t &z)
{
	char reg = 0x00, d[3];
	for (int tries = 0; tries < 3; tries++)
	{
		if (i2c.write(ACC_ADDR, &reg, 1, true) != 0) return false;
		if (i2c.read(ACC_ADDR, d, 3) != 0) return false;
		if ((d[0] | d[1] | d[2]) & 0x40) continue;	// alert bit : value being updated
		auto conv = [](char v) { int8_t s = v & 0x3F; return (int8_t)(s & 0x20 ? s - 64 : s); };
		x = conv(d[0]); y = conv(d[1]); z = conv(d[2]);
		return true;
	}
	return false;
}

bool lm75_read(int16_t &x10)
{
	char reg = 0x00, d[2];
	if (i2c.write(LM75_ADDR, &reg, 1, true) != 0) return false;
	if (i2c.read(LM75_ADDR, d, 2) != 0) return false;
	int16_t raw = (int16_t)((d[0] << 8) | d[1]) >> 5;	// 0.125 C per step
	x10 = raw * 10 / 8;
	return true;
}

void post(uint8_t kind, uint8_t type = 0, int16_t arg = 0)
{
	Msg *m = inbox.try_alloc();
	if (!m) return;
	m->kind = kind; m->type = type; m->arg = arg; m->len = 0;
	inbox.put(m);
}

void sensor_thread()
{
	char cfg[2] = { 0x07, 0x00 };		// MODE = standby
	i2c.frequency(100000);
	i2c.write(ACC_ADDR, cfg, 2);
	cfg[0] = 0x08; cfg[1] = 0x00;		// 120 samples/s
	i2c.write(ACC_ADDR, cfg, 2);
	cfg[0] = 0x07; cfg[1] = 0x01;		// MODE = active
	i2c.write(ACC_ADDR, cfg, 2);

	float bx = 0, by = 0, bz = 21;		// resting position (baseline)
	bool base_ok = false, was_armed = false;
	int over = 0, n = 0;
	uint64_t quiet_until = now_ms() + 3000;	// no tamper check right after start
	uint8_t step = 0;
	bool need_flat = true;
	char held = 0;
	int held_n = 0;

	while (true)
	{
		ThisThread::sleep_for(20ms);
		if (++n % 25 == 0) led4 = !led4;

		int8_t x, y, z;
		if (!acc_read(x, y, z)) continue;
		int th = 6 + (int)(pot1.read() * 14);		// tilt threshold 6 ~ 20
		char dir = '-';
		if (x <= -th) dir = 'L'; else if (x >= th) dir = 'R';
		else if (y >= th) dir = 'F'; else if (y <= -th) dir = 'B';

		st_mtx.lock();
		sh.ax = x; sh.ay = y; sh.az = z; sh.tilt_now = dir;
		st_mtx.unlock();

		if (n % 50 == 0)			// LM75B once a second
		{
			int16_t t;
			if (lm75_read(t)) { st_mtx.lock(); sh.lm75_x10 = t; st_mtx.unlock(); }
		}

		uint32_t f = flags.get();
		if (f & F_TILT)
		{
			// tilt code : hold a direction 0.2 s, then return to flat before the next one
			quiet_until = now_ms() + 5000;		// time to put the board down afterwards
			base_ok = false;
			if (dir == '-' && abs(x) < 5 && abs(y) < 5) { need_flat = false; held = 0; held_n = 0; continue; }
			if (need_flat || dir == '-') continue;
			if (dir == held) held_n++; else { held = dir; held_n = 1; }
			if (held_n < 10) continue;
			need_flat = true;
			if (dir == TILT_CODE[step])
			{
				step++;
				led3 = 1;
				post(M_TILT_STEP, dir, step);
				if (step >= TILT_STEPS) { step = 0; post(M_TILT_OK); }
			}
			else
			{
				step = 0;
				post(M_TILT_BAD, dir);
			}
			continue;
		}
		step = 0;
		need_flat = true;
		led3 = 0;

		// tamper : big change from the resting position while armed
		bool armed = f & F_ARMED;
		if (armed && !was_armed) quiet_until = now_ms() + 3000;	// just re-armed : let the board settle
		was_armed = armed;
		if (!armed || now_ms() < quiet_until) { base_ok = false; over = 0; continue; }
		if (!base_ok) { bx = x; by = y; bz = z; base_ok = true; continue; }
		float dev = fabsf(x - bx) + fabsf(y - by) + fabsf(z - bz);
		float limit = 4 + pot2.read() * 26;		// 4 ~ 30 counts
		if (dev > limit) over++; else { over = 0; bx += (x - bx) * 0.02f; by += (y - by) * 0.02f; bz += (z - bz) * 0.02f; }
		if (over >= 3)
		{
			over = 0;
			post(M_TAMPER, 0, (int16_t)dev);
			quiet_until = now_ms() + 5000;
			base_ok = false;
		}
	}
}

// ---------------- thread : LCD, joystick, LEDs ----------------

void ui_thread()
{
	lcd.init();
	uint8_t last = 0;
	int page = 0;
	char t[40];

	while (true)
	{
		redraw.try_acquire_for(100ms);		// 10 Hz (joystick + LCD), or at once when woken
		led1 = !led1;

		// joystick (new press only)
		uint8_t k = joy_centre ? 'C' : joy_down ? 'D' : joy_left ? 'L' : joy_right ? 'R' : joy_up ? 'U' : 0;
		if (k && k != last)
		{
			if ((k == 'L' || k == 'R') && !(flags.get() & (F_REQUEST | F_TILT)))
				page = (page + (k == 'R' ? 1 : 2)) % 3;
			else
				post(M_JOY, k);
		}
		last = k;

		st_mtx.lock();
		Shared s = sh;
		st_mtx.unlock();
		uint32_t f = flags.get();
		uint64_t now = now_ms();
		bool blink = (now / 300) & 1;

		lcd.clear();
		if (f & F_TILT)
		{
			lcd.text(0, 0, "TILT THE SECRET CODE");
			for (int i = 0; i < TILT_STEPS; i++)
			{
				lcd.box(4 + i * 18, 10, 14, 12);
				if (i < s.tilt_done) lcd.fill_box(6 + i * 18, 12, 10, 8);
			}
			snprintf(t, sizeof(t), "now:%c", s.tilt_now);
			lcd.text(62, 12, t);
			int left = s.tilt_end > now ? (int)((s.tilt_end - now + 999) / 1000) : 0;
			snprintf(t, sizeof(t), "%2ds  DOWN=reject", left);
			lcd.text(0, 24, t);
		}
		else if (f & F_REQUEST)
		{
			lcd.text(0, 0, blink ? "ACCESS REQUEST  >>>" : "ACCESS REQUEST");
			snprintf(t, sizeof(t), "%u %s  drawer %c", s.req_staff, staff_name(s.req_staff), s.req_drawer);
			lcd.text(0, 9, t);
			lcd.text(0, 17, "CENTRE=approve DOWN=no");
			int left = s.req_end > now ? (int)((s.req_end - now + 999) / 1000) : 0;
			snprintf(t, sizeof(t), "expires in %2ds", left);
			lcd.text(0, 25, t);
		}
		else if (page == 0)
		{
			lcd.text(0, 0, "MedVault SUPERVISOR");
			if (!(f & F_LINK)) lcd.text(0, 9, "CABINET: NO LINK");
			else
			{
				snprintf(t, sizeof(t), "Cab:%s %s", s.cab_state < 9 ? ST_NAME[s.cab_state] : "?",
					(s.cab_flags & 2) ? "OPEN" : "LOCKED");
				lcd.text(0, 9, t);
			}
			snprintf(t, sizeof(t), "T%d.%d LM%d.%d max%d", s.temp_x10 / 10, abs(s.temp_x10 % 10),
				s.lm75_x10 / 10, abs(s.lm75_x10 % 10), s.limit_x10 / 10);
			lcd.text(0, 17, t);
			if (s.cab_alarms & AL_LOCKDOWN)	lcd.text(0, 25, blink ? "!! LOCKDOWN !!" : "");
			else if (s.cab_alarms & AL_COLD)	lcd.text(0, 25, blink ? "!! COLD CHAIN !!" : "");
			else if (s.cab_alarms & AL_ATTACK)	lcd.text(0, 25, "ATTACK BLOCKED");
			else {
				snprintf(t, sizeof(t), "%s %s", (f & F_ARMED) ? "ARMED" : "-----", s.last_event);
				lcd.text(0, 25, t);
			}
		}
		else if (page == 1)
		{
			lcd.text(0, 0, "SENSORS        2/3");
			snprintf(t, sizeof(t), "ACC x%3d y%3d z%3d", s.ax, s.ay, s.az);
			lcd.text(0, 9, t);
			snprintf(t, sizeof(t), "tilt %c  th %d", s.tilt_now, 6 + (int)(pot1.read() * 14));
			lcd.text(0, 17, t);
			snprintf(t, sizeof(t), "tamper %d  LDR %d%%", 4 + (int)(pot2.read() * 26), s.ldr);
			lcd.text(0, 25, t);
		}
		else
		{
			lcd.text(0, 0, "LINK / SECURITY 3/3");
			snprintf(t, sizeof(t), "frames %lu bad %lu", (unsigned long)s.frames_in, (unsigned long)s.frames_bad);
			lcd.text(0, 9, t);
			snprintf(t, sizeof(t), "counter %u", s.counter);
			lcd.text(0, 17, t);
			snprintf(t, sizeof(t), "last: %s", s.last_event);
			lcd.text(0, 25, t);
		}
		lcd.update();

		// RGB : red = alarm, blue = request, green = all fine
		bool alarm = s.cab_alarms & (AL_COLD | AL_FORCED | AL_MOVED | AL_ATTACK | AL_LOCKDOWN | AL_AJAR);
		rgb_r = !(alarm && blink);
		rgb_b = !(!alarm && (f & (F_REQUEST | F_TILT)));
		rgb_g = !(!alarm && !(f & (F_REQUEST | F_TILT)) && (f & F_LINK));
	}
}

// ---------------- thread : Raspberry Pi ----------------

int hex_val(char c)
{
	if (c >= '0' && c <= '9') return c - '0';
	if (c >= 'A' && c <= 'F') return c - 'A' + 10;
	if (c >= 'a' && c <= 'f') return c - 'a' + 10;
	return -1;
}

void pi_command(char *s)
{
	if (strcmp(s, "CMD,LOCKDOWN") == 0)	post(M_PI_CMD, CMD_LOCKDOWN, LD_REMOTE);
	else if (strcmp(s, "CMD,CLEAR") == 0)	post(M_PI_CMD, CMD_CLEAR, 0);
	else if (strncmp(s, "CMD,LIMIT,", 10) == 0) post(M_PI_CMD, CMD_LIMIT, (int16_t)atoi(s + 10));
#if ALLOW_INJECT
	else if (strncmp(s, "INJ,", 4) == 0)
	{
		Msg *m = inbox.try_alloc();
		if (!m) return;
		m->kind = M_PI_INJ; m->len = 0;
		for (char *p = s + 4; p[0] && p[1] && m->len < sizeof(m->data); p += 2)
		{
			int a = hex_val(p[0]), b = hex_val(p[1]);
			if (a < 0 || b < 0) break;
			m->data[m->len++] = (a << 4) | b;
		}
		inbox.put(m);
	}
#endif
	else if (strcmp(s, "HELLO") == 0) pi_line("LOG,MedVault supervisor ready");
}

// one received character from the gateway : build lines, run them
void gw_char(char c, char *line, int &n)
{
	if (c == '\n' || c == '\r')
	{
		line[n] = 0;
		if (n) pi_command(line);
		n = 0;
	}
	else if (n < 99) line[n++] = c;
}

void pi_thread()
{
	char line[100];
	int n = 0;
#if GW_LINK_USB
	USBSerial usb(false);			// App Board mini-USB = the LPC1768's own USB
	while (true)
	{
		Line *l = pi_out.try_get_for(20ms);
		if (l)
		{
			if (usb.connected()) usb.send((uint8_t *)l->s, strlen(l->s));	// nobody listening : drop
			pi_out.free(l);
		}
		while (usb.available()) gw_char((char)usb.getc(), line, n);
	}
#else
	pc.set_blocking(false);
	while (true)
	{
		Line *l = pi_out.try_get_for(20ms);
		if (l)
		{
			pc.write(l->s, strlen(l->s));
			pi_out.free(l);
		}
		char c;
		while (pc.read(&c, 1) == 1) gw_char(c, line, n);
	}
#endif
}

// ---------------- main thread : security / logic ----------------

void handle_frame(Msg *m)
{
	uint8_t *d = m->data;

	if (m->type == 'S' && m->len == 8)
	{
		st_mtx.lock();
		sh.cab_state = d[0]; sh.cab_flags = d[1];
		sh.temp_x10 = (int16_t)((d[2] << 8) | d[3]);
		sh.limit_x10 = (int16_t)((d[4] << 8) | d[5]);
		sh.ldr = d[6]; sh.cab_alarms = d[7];
		int16_t lm = sh.lm75_x10;
		uint16_t ctr = sh.counter;
		st_mtx.unlock();
		if (d[7] & 0x7F) flags.set(F_ALARM); else flags.clear(F_ALARM);
		// ST,state,flags,temp,limit,ldr,alarms,lm75,ctr
		pi_line("ST,%s,%u,%d,%d,%u,%u,%d,%u", d[0] < 9 ? ST_NAME[d[0]] : "?", d[1],
			(int16_t)((d[2] << 8) | d[3]), (int16_t)((d[4] << 8) | d[5]), d[6], d[7], lm, ctr);
		// tamper detection only while the cabinet is locked and idle
		if (!(flags.get() & (F_REQUEST | F_TILT)) && (d[0] == 0 || d[0] == 7)) flags.set(F_ARMED);
		else flags.clear(F_ARMED);
	}
	else if (m->type == 'R' && m->len == 7)
	{
		char hex[40];
		uint8_t raw[16] = { SOF, 'R', 7 };
		memcpy(raw + 3, d, 7);
		raw[10] = crc8(raw + 1, 9);
		hex_str(hex, raw, 11);
		pi_line("RAW,N2S,%s", hex);

		st_mtx.lock();
		sh.req_staff = (d[0] << 8) | d[1];
		sh.req_drawer = d[2];
		sh.req_nonce = ((uint32_t)d[3] << 24) | (d[4] << 16) | (d[5] << 8) | d[6];
		sh.req_end = now_ms() + REQ_TIME_S * 1000;
		sh.tilt_done = 0; sh.tilt_fails = 0;
		uint16_t staff = sh.req_staff;
		st_mtx.unlock();
		flags.clear(F_ARMED | F_TILT);
		flags.set(F_REQUEST);
		pi_line("RQ,%u,%c", staff, d[2]);
		set_event("request");
	}
	else if (m->type == 'E' && m->len == 4)
	{
		uint8_t code = d[0];
		uint16_t staff = (d[1] << 8) | d[2];
		pi_line("EV,%s,%u,%u", code < N_EV ? EV_NAME[code] : "?", staff, d[3]);
		if (code == 8 || code == 19 || code == 15)		// timeout, cancel, lockdown : request is over
			flags.clear(F_REQUEST | F_TILT);
		if (code < N_EV) set_event(EV_NAME[code]);
	}
}

int main()
{
	t_ui.start(ui_thread);
	t_nuc.start(nuc_thread);
	t_sensor.start(sensor_thread);
	t_pi.start(pi_thread);

	pi_line("LOG,MedVault supervisor booted");
	int boot_pings = 5;
	uint64_t next_ping = 0, last_rx = 0;
	uint32_t frames_seen = 0;

	while (true)
	{
		// ping the cabinet every second (first 5 say "just booted" : counter restarts)
		uint64_t now = now_ms();
		if (now >= next_ping)
		{
			next_ping = now + 1000;
			uint8_t b = boot_pings > 0 ? 1 : 0;
			if (boot_pings > 0) boot_pings--;
			send_frame('P', &b, 1, false);

			st_mtx.lock();
			uint32_t fr = sh.frames_in;
			bool req_old = (flags.get() & F_REQUEST) && now > sh.req_end;
			bool tilt_old = (flags.get() & F_TILT) && now > sh.tilt_end;
			st_mtx.unlock();
			if (fr != frames_seen) { frames_seen = fr; last_rx = now; }
			if (fr > 0 && now - last_rx < 3000) flags.set(F_LINK); else flags.clear(F_LINK);
			if (req_old) { flags.clear(F_REQUEST | F_TILT); set_event("req expired"); }
			else if (tilt_old) send_decision(false);
		}

		Msg *m = inbox.try_get_for(100ms);
		if (!m) continue;

		uint32_t f = flags.get();
		switch (m->kind)
		{
		case M_FRAME:
			handle_frame(m);
			break;

		case M_JOY:
			if ((f & F_REQUEST) && !(f & F_TILT) && m->type == 'C')
			{
				st_mtx.lock();
				sh.tilt_end = now_ms() + TILT_TIME_S * 1000;
				sh.tilt_done = 0;
				st_mtx.unlock();
				flags.set(F_TILT);
				pi_line("LOG,supervisor entering tilt code");
			}
			else if ((f & F_REQUEST) && m->type == 'D')
				send_decision(false);
			else if (m->type == 'U' && !(f & F_REQUEST))
				pi_line("LOG,supervisor present");
			break;

		case M_TILT_STEP:
			st_mtx.lock(); sh.tilt_done = m->arg; st_mtx.unlock();
			break;

		case M_TILT_OK:
			if (f & F_TILT) send_decision(true);
			break;

		case M_TILT_BAD:
			if (f & F_TILT)
			{
				st_mtx.lock();
				sh.tilt_done = 0;
				uint8_t fails = ++sh.tilt_fails;
				uint16_t staff = sh.req_staff;
				st_mtx.unlock();
				pi_line("EV,TILT_WRONG,%u,%u", staff, fails);
				set_event("wrong tilt");
				if (fails >= 3) send_decision(false);
			}
			break;

		case M_TAMPER:
			pi_line("EV,MOVED,0,%d", m->arg);
			set_event("MOVED!");
			send_command(CMD_LOCKDOWN, LD_MOVED);
			break;

		case M_PI_CMD:
			if (m->type == CMD_LIMIT && (m->arg < 20 || m->arg > 400)) break;
			send_command(m->type, (uint16_t)m->arg);
			pi_line("LOG,remote command %u sent", m->type);
			set_event(m->type == CMD_LOCKDOWN ? "remote lock" : m->type == CMD_CLEAR ? "remote clear" : "remote limit");
			break;

		case M_PI_INJ:
			// security test : raw bytes straight onto the cabinet wire (like an attacker)
			nuc.write(m->data, m->len);
			pi_line("LOG,injected %u raw bytes", m->len);
			set_event("TEST INJECT");
			break;
		}
		wake_ui();
		inbox.free(m);
	}
}
