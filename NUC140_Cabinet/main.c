//
// MedVault : Cabinet Controller (Nu-LB-NUC140)        RBB3023 Project, group VaultX
//
// The cabinet only opens when a nurse logs in (staff ID + PIN on the keypad)
// AND the supervisor unit (LPC1768) sends an encrypted approval for this exact
// request. Every reply is XTEA encrypted and must carry the random challenge
// (nonce) made for this request and a counter higher than the last one, so a
// recorded old message (replay attack) is rejected.
//
// Inputs
//   Keypad 3x3     GPA0~5   digits 1~9 (ID, PIN, drawer). Same numbering as ScanKey()
//   SW_INT         GPB15    EINT1 falling edge : cancel / acknowledge
//   Thermistor     ADC6     GPA6, NTC 10k (B3950) to GND, 10k to 3.3V
//   LDR            ADC5     GPA5, LDR to GND, 10k to 3.3V (dark = high = door closed)
//                           GPA5 is also keypad column 3 : switched to GPIO only
//                           while scanning (like Lab 6). Keys 7~9 are ignored
//                           while the box is bright (door open), that is fine.
//   VR1            ADC7     GPA7, temperature alarm limit 2.0 ~ 40.0 C
//   UART1 RX       GPB4     JP5 pin 5, from LPC1768 p9 (TX)
// Outputs
//   LCD 128x64     SPI3     menus / status / alarms (only changed lines are redrawn)
//   7-segment      GPC4~7 digit, GPE0~7 segment : temperature or countdown
//   Servo SG90     GPB8     JP5 pin 9, lock (pulse made by Timer0 + Timer1 one-shot)
//   RGB LED        GPA12 B, GPA13 G, GPA14 R (low = on)
//   LEDs           GPC12 LOCKED, GPC13 OPEN, GPC14 ALARM, GPC15 LINK (low = on)
//   UART1 TX       GPB5     JP5 pin 6, to LPC1768 p10 (RX), 9600 8N1, GND (JP5 pin 17/18) to GND
//                           (not UART0 : GPB0/1 are tied to the board's RS232 chip,
//                            not GPA15 : it goes to the audio codec)
//
// Timers : Timer0 1 ms periodic (time base, 7-seg scan, servo frame 20 ms)
//          Timer1 one-shot (servo pulse width 1.0 ~ 2.0 ms)
//          WDT 1.6 s (main loop feeds it, a hang resets the board)
//
// Frame (both ways) : 0xA5 | type | len | payload[len] | CRC-8 (poly 0x07, over type..payload)
//   NUC -> LPC  'S' status 8 B, 'R' request 7 B (ID, drawer, nonce), 'E' event 4 B
//   LPC -> NUC  'A' approval 8 B (XTEA), 'C' command 8 B (XTEA), 'P' ping 1 B (1 = just booted)
//
// Staff (digits 1~9 only, the keypad has no 0) : see staff[] below.
//   ID 1111 = diagnostics screen (raw ADC values), SW_INT to leave.
//
#include <stdio.h>
#include <string.h>
#include "NUC1xx.h"
#include "SYS.h"
#include "GPIO.h"
#include "ADC.h"
#include "UART.h"
#include "LCD.h"

// ---------------- settings ----------------
#define USE_LDR		1	// 0 if the LDR is not wired yet (no door / forced detection)
#define USE_THERM	1	// 0 if the thermistor is not wired yet (temperature shows ----)
#define SERVO_LOCK_US	1000	// servo pulse for LOCKED  (tune on the real lock)
#define SERVO_OPEN_US	2000	// servo pulse for OPEN
#define SERVO_PORT	E_GPB
#define SERVO_PIN	8

#define WAIT_S		25	// time for the supervisor to approve
#define OPEN_S		30	// door may be opened within this time, then relock
#define LOCKOUT_S	60	// after 3 wrong PINs
#define COLD_DELAY_MS	5000	// temperature must stay over the limit this long

// brackets matter : the BSP macro does 0x4*PinNum without them, so pin "4 + i"
// became 0x4*4 + i, a misaligned address = hard fault on the Cortex-M0
#define PIN_OUT(port, pin)	_DRVGPIO_DOUT((port), (pin))

// ---------------- protocol ----------------
#define SOF		0xA5
#define MAGIC		0x5A
#define CMD_MAGIC	0xC3

enum { EV_BOOT = 1, EV_LOGIN, EV_WRONG_PIN, EV_PIN_LOCKOUT, EV_REQUEST, EV_APPROVED,
	EV_REJECTED, EV_TIMEOUT, EV_DOOR_OPEN, EV_RELOCK, EV_COLD_ON, EV_COLD_OFF,
	EV_FORCED, EV_ATTACK, EV_LOCKDOWN, EV_CLEARED, EV_AJAR, EV_LIMIT, EV_CANCEL };

enum { AT_REPLAY = 1, AT_OLD_COUNTER, AT_BAD_KEY };		// attack reasons
enum { LD_FORCED = 1, LD_MOVED, LD_REMOTE };			// lockdown reasons
enum { CMD_LOCKDOWN = 1, CMD_CLEAR, CMD_LIMIT };

#define AL_COLD		0x01
#define AL_FORCED	0x02
#define AL_MOVED	0x04
#define AL_PINLOCK	0x08
#define AL_ATTACK	0x10
#define AL_LOCKDOWN	0x20
#define AL_AJAR		0x40
#define AL_NOLINK	0x80

enum { ST_IDLE, ST_ID, ST_PIN, ST_DRAWER, ST_WAIT, ST_OPEN, ST_LOCKOUT, ST_LOCKDOWN, ST_DIAG };

// shared XTEA key (same in the LPC1768 program)
static const uint32_t KEY[4] = { 0x4D656456, 0x61756C74, 0x56614B58, 0x26202026 };

typedef struct {
	uint16_t id;
	uint16_t pin;
	char     name[9];
	uint8_t  admin;
} staff_t;

static const staff_t staff[] = {
	{ 1357, 2468, "AINA",    0 },
	{ 2468, 1397, "WILLIAM", 0 },
	{ 3579, 8642, "LIKAI",   0 },
	{ 9999, 7531, "ADMIN",   1 },
};
#define N_STAFF (sizeof(staff) / sizeof(staff[0]))

// thermistor ADC value at -10, -5, 0 ... 60 C (10k NTC B3950 to GND, 10k to 3.3V)
static const uint16_t therm_tab[15] = { 3495, 3337, 3156, 2955, 2738, 2510, 2278, 2048,
	1825, 1614, 1419, 1241, 1081, 940, 815 };

// ---------------- globals ----------------
volatile uint32_t ms_tick = 0;
volatile uint8_t  seg_pat[4] = { 0xFF, 0xFF, 0xFF, 0xFF };	// [3] = leftmost, active low
volatile uint16_t servo_us = SERVO_LOCK_US;
volatile uint16_t servo_frames = 0;		// pulses still to send (0 = servo idle)
volatile uint8_t  sw_pressed = 0;

// UART receive (filled by the ISR)
volatile uint8_t rx_buf[16];
volatile uint8_t rx_type, rx_len, rx_pos, rx_state = 0;
#define NFRAMES 4				// small queue : a ping + an approval can arrive together
volatile uint8_t frames[NFRAMES][16];		// good frames : [0] type [1] len [2..] payload
volatile uint8_t fr_head = 0, fr_tail = 0;	// ISR writes head, main reads tail
uint8_t frame[16];				// the frame main is handling now

uint8_t  state = ST_IDLE;
uint8_t  alarms = 0;
uint8_t  lockdown_reason = 0;
uint8_t  attack_reason = 0;
uint32_t attack_until = 0;
uint8_t  digits = 0;				// digits typed so far
uint16_t entry = 0;				// number being typed
uint16_t cur_id = 0;
int8_t   cur_staff = -1;
uint8_t  wrong_pins = 0;
uint8_t  drawer = 'A';
uint32_t nonce = 0;
uint8_t  nonce_valid = 0;
uint16_t last_ctr = 0;
uint32_t deadline = 0;				// end time of WAIT / OPEN / LOCKOUT
uint8_t  door_open = 0, door_was_opened = 0;
uint32_t last_ping = 0;
uint8_t  link_ok = 0;
uint32_t msg_until = 0;				// a 2 s message is on the LCD
char     msg1[17], msg2[17];

int16_t  temp_x10 = 0;				// 0.1 C
uint8_t  temp_ok = 0;
int16_t  limit_x10 = 80;
uint8_t  remote_limit = 0;
uint16_t vr_at_remote = 0;
uint16_t adc_t = 0, adc_l = 0, adc_v = 0;	// filtered ADC values
uint16_t ldr_thr = 1500;
uint32_t cold_since = 0, cool_since = 0, forced_since = 0;
uint32_t rng = 0x1234567;

char shown[4][17];				// what the LCD shows now

// ---------------- small helpers ----------------

uint8_t reached(uint32_t t)
{
	return (int32_t)(ms_tick - t) >= 0;
}

int32_t secs_left(uint32_t t)
{
	int32_t ms = (int32_t)(t - ms_tick);
	return ms <= 0 ? 0 : (ms + 999) / 1000;
}

uint32_t rand32(void)				// xorshift32, mixed with ADC noise
{
	rng ^= ms_tick ^ ((uint32_t)adc_t << 11) ^ ((uint32_t)adc_l << 21) ^ adc_v;
	rng ^= rng << 13;
	rng ^= rng >> 17;
	rng ^= rng << 5;
	return rng;
}

// ---------------- XTEA (64-bit block, 128-bit key, 32 rounds) ----------------

void xtea_decrypt(uint8_t *b)
{
	uint32_t v0 = ((uint32_t)b[0] << 24) | ((uint32_t)b[1] << 16) | ((uint32_t)b[2] << 8) | b[3];
	uint32_t v1 = ((uint32_t)b[4] << 24) | ((uint32_t)b[5] << 16) | ((uint32_t)b[6] << 8) | b[7];
	uint32_t delta = 0x9E3779B9, sum = delta * 32;
	uint8_t i;

	for (i = 0; i < 32; i++)
	{
		v1 -= (((v0 << 4) ^ (v0 >> 5)) + v0) ^ (sum + KEY[(sum >> 11) & 3]);
		sum -= delta;
		v0 -= (((v1 << 4) ^ (v1 >> 5)) + v1) ^ (sum + KEY[sum & 3]);
	}
	b[0] = v0 >> 24; b[1] = v0 >> 16; b[2] = v0 >> 8; b[3] = v0;
	b[4] = v1 >> 24; b[5] = v1 >> 16; b[6] = v1 >> 8; b[7] = v1;
}

uint8_t crc8(const volatile uint8_t *p, uint8_t n, uint8_t crc)
{
	uint8_t i;
	while (n--)
	{
		crc ^= *p++;
		for (i = 0; i < 8; i++)
			crc = (crc & 0x80) ? (crc << 1) ^ 0x07 : crc << 1;
	}
	return crc;
}

// ---------------- Timer0 1 ms : time base, 7-seg scan, servo frame ----------------

void TMR0_IRQHandler(void)
{
	static uint8_t d = 0, f = 0;
	uint8_t i, p;

	ms_tick++;

	// 7-seg : one digit per ms
	for (i = 0; i < 4; i++) PIN_OUT(E_GPC, 4 + i) = 0;
	p = seg_pat[d];
	for (i = 0; i < 8; i++) PIN_OUT(E_GPE, i) = (p >> i) & 1;
	PIN_OUT(E_GPC, 4 + d) = 1;
	d = (d + 1) & 3;

	// servo : every 20 ms start one pulse, Timer1 ends it
	if (++f >= 20)
	{
		f = 0;
		if (servo_frames)
		{
			servo_frames--;
			PIN_OUT(SERVO_PORT, SERVO_PIN) = 1;
			TIMER1->TCMPR = servo_us;		// 1 us per count
			TIMER1->TCSR.CRST = 1;
			TIMER1->TCSR.CEN = 1;
		}
	}
	TIMER0->TISR.TIF = 1;
}

// Timer1 one-shot : end of the servo pulse
void TMR1_IRQHandler(void)
{
	PIN_OUT(SERVO_PORT, SERVO_PIN) = 0;
	TIMER1->TCSR.CEN = 0;
	TIMER1->TISR.TIF = 1;
}

void lcd_line(uint8_t n, const char *text);

// a crash (bad address, bad instruction) lands here : say so on the LCD
void HardFault_Handler(void)
{
	print_Line(3, "!! HARD FAULT !!");
	while (1);
}

void init_timers(void)
{
	SYSCLK->CLKSEL1.TMR0_S = 0;		// 12 MHz
	SYSCLK->APBCLK.TMR0_EN = 1;
	TIMER0->TCSR.MODE = 1;			// periodic
	TIMER0->TCSR.PRESCALE = 11;		// 1 MHz
	TIMER0->TCMPR = 1000;			// 1 ms
	TIMER0->TCSR.IE = 1;
	TIMER0->TISR.TIF = 1;
	NVIC_EnableIRQ(TMR0_IRQn);
	TIMER0->TCSR.CRST = 1;
	TIMER0->TCSR.CEN = 1;

	SYSCLK->CLKSEL1.TMR1_S = 0;
	SYSCLK->APBCLK.TMR1_EN = 1;
	TIMER1->TCSR.CEN = 0;
	TIMER1->TCSR.MODE = 0;			// one-shot
	TIMER1->TCSR.PRESCALE = 11;		// 1 us per count
	TIMER1->TCMPR = SERVO_LOCK_US;		// never leave TCMPR at 0 : the flag can fire nonstop
	TIMER1->TCSR.IE = 1;
	TIMER1->TISR.TIF = 1;
	NVIC_EnableIRQ(TMR1_IRQn);
}

void servo_set(uint16_t us)
{
	servo_us = us;
	servo_frames = 40;			// 40 x 20 ms = 0.8 s of pulses, then rest
}

// ---------------- Watchdog ----------------

void WDT_IRQHandler(void)
{
	UNLOCKREG();
	WDT->WTCR.WTIF = 1;			// not fed : reset follows
	LOCKREG();
}

void init_wdt(void)
{
	UNLOCKREG();
	SYSCLK->CLKSEL1.WDT_S = 3;		// 10 kHz
	SYSCLK->APBCLK.WDT_EN = 1;
	WDT->WTCR.WTIS = 5;			// 2^14 / 10 kHz = 1.6 s
	WDT->WTCR.WTRE = 1;
	WDT->WTCR.WTIF = 1;
	WDT->WTCR.WTIE = 1;
	NVIC_EnableIRQ(WDT_IRQn);
	WDT->WTCR.WTE = 1;
	WDT->WTCR.WTR = 1;
	LOCKREG();
}

void feed_wdt(void)
{
	UNLOCKREG();
	WDT->WTCR.WTR = 1;
	LOCKREG();
}

// ---------------- UART1 to the supervisor unit ----------------

void UART_INT_HANDLE(uint32_t u32IntStatus)
{
	uint8_t c[1], i;

	while (UART1->ISR.RDA_IF == 1)
	{
		DrvUART_Read(UART_PORT1, c, 1);
		switch (rx_state)
		{
		case 0:	if (c[0] == SOF) rx_state = 1; break;
		case 1:	rx_type = c[0]; rx_state = 2; break;
		case 2:	rx_len = c[0]; rx_pos = 0;
			rx_state = (rx_len <= 12) ? (rx_len ? 3 : 4) : 0;
			break;
		case 3:	rx_buf[rx_pos++] = c[0];
			if (rx_pos >= rx_len) rx_state = 4;
			break;
		case 4:	rx_state = 0;
			{
				uint8_t crc = crc8(&rx_type, 1, 0);
				crc = crc8(&rx_len, 1, crc);
				crc = crc8(rx_buf, rx_len, crc);
				uint8_t next = (fr_head + 1) % NFRAMES;
				if (crc == c[0] && next != fr_tail)	// full queue : drop
				{
					frames[fr_head][0] = rx_type;
					frames[fr_head][1] = rx_len;
					for (i = 0; i < rx_len; i++) frames[fr_head][2 + i] = rx_buf[i];
					fr_head = next;
				}
			}
			break;
		}
	}
}

void init_uart(void)
{
	STR_UART_T p;

	DrvGPIO_InitFunction(E_FUNC_UART1);	// GPB4 RX1, GPB5 TX1
	p.u32BaudRate       = 9600;
	p.u8cDataBits       = DRVUART_DATABITS_8;
	p.u8cStopBits       = DRVUART_STOPBITS_1;
	p.u8cParity         = DRVUART_PARITY_NONE;
	p.u8cRxTriggerLevel = DRVUART_FIFO_1BYTES;
	DrvUART_Open(UART_PORT1, &p);
	DrvUART_EnableInt(UART_PORT1, DRVUART_RDAINT, UART_INT_HANDLE);
}

void send_frame(uint8_t type, const uint8_t *data, uint8_t len)
{
	uint8_t buf[16];
	uint8_t i;

	buf[0] = SOF;
	buf[1] = type;
	buf[2] = len;
	for (i = 0; i < len; i++) buf[3 + i] = data[i];
	buf[3 + len] = crc8(buf + 1, len + 2, 0);
	DrvUART_Write(UART_PORT1, buf, len + 4);
}

void send_status(void)
{
	uint8_t d[8];

	d[0] = state;
	d[1] = (door_open ? 1 : 0) | (state == ST_OPEN ? 2 : 0) | (temp_ok ? 4 : 0) | (remote_limit ? 8 : 0);
	d[2] = (uint16_t)temp_x10 >> 8;
	d[3] = temp_x10 & 0xFF;
	d[4] = (uint16_t)limit_x10 >> 8;
	d[5] = limit_x10 & 0xFF;
	d[6] = (uint32_t)adc_l * 100 / 4095;
	d[7] = alarms;
	send_frame('S', d, 8);
}

void send_event(uint8_t code, uint8_t info)
{
	uint8_t d[4];

	d[0] = code;
	d[1] = cur_id >> 8;
	d[2] = cur_id & 0xFF;
	d[3] = info;
	send_frame('E', d, 4);
	send_status();
}

// ---------------- SW_INT (GPB15 / EINT1) ----------------

void sw_callback(void)
{
	sw_pressed = 1;
}

// ---------------- keypad + ADC (GPA5 shared, like Lab 6) ----------------

void gpa5_adc(void)
{
	DrvGPIO_Open(E_GPA, 5, E_IO_INPUT);
	GPIOA->OFFD |= 1UL << (16 + 5);
	SYS->GPAMFP.ADC5_AD8 = 1;
}

void gpa5_key(void)
{
	SYS->GPAMFP.ADC5_AD8 = 0;
	GPIOA->OFFD &= ~(1UL << (16 + 5));
	DrvGPIO_Open(E_GPA, 5, E_IO_QUASI);
}

void key_delay(void)
{
	volatile int j;
	for (j = 0; j < 1000; j++);
}

// 1 ~ 9 like BSP ScanKey(), 0 = none
uint8_t key_scan(void)
{
	uint8_t row, col, ext = 0, key = 0;

	gpa5_key();
	for (row = 0; row < 6; row++) DrvGPIO_SetBit(E_GPA, row);
	key_delay();
	if (DrvGPIO_GetBit(E_GPA, 5) == 0) ext = 1;	// LDR holds column 3 low (bright)

	for (row = 0; row < 3 && key == 0; row++)
	{
		DrvGPIO_ClrBit(E_GPA, 2 - row);
		key_delay();
		for (col = 0; col < 3; col++)
		{
			if (col == 2 && ext) continue;
			if (DrvGPIO_GetBit(E_GPA, 3 + col) == 0)
			{
				key = row + 1 + col * 3;
				break;
			}
		}
		DrvGPIO_SetBit(E_GPA, 2 - row);
	}
	gpa5_adc();
	return key;
}

void init_adc(void)
{
	DrvGPIO_Open(E_GPA, 6, E_IO_INPUT);
	DrvGPIO_Open(E_GPA, 7, E_IO_INPUT);
	GPIOA->OFFD |= 0x00C00000;		// digital path off GPA6 / 7
	SYS->GPAMFP.ADC6_AD7 = 1;
	SYS->GPAMFP.ADC7_SS21_AD6 = 1;
	gpa5_adc();
	DrvADC_Open(ADC_SINGLE_END, ADC_SINGLE_OP, 0xE0, INTERNAL_RC22MHZ, 1);	// 11 MHz
}

uint16_t adc_read(uint8_t ch)
{
	ADC->ADCR.ADST = 0;
	while (ADC->ADSR.BUSY == 1);
	ADC->ADCR.ADMD = 0;			// single
	ADC->ADCHER.CHEN = 1UL << ch;
	ADC->ADSR.ADF = 1;
	ADC->ADCR.ADST = 1;
	while (ADC->ADSR.ADF == 0);
	ADC->ADSR.ADF = 1;
	return ADC->ADDR[ch].RSLT;
}

// thermistor ADC -> 0.1 C by table + straight line between points
int16_t therm_to_x10(uint16_t a)
{
	uint8_t i;

	if (a >= therm_tab[0]) return -100;
	for (i = 1; i < 15; i++)
	{
		if (a >= therm_tab[i])
			return (int16_t)(-100 + (i - 1) * 50 +
				(int32_t)(therm_tab[i - 1] - a) * 50 / (therm_tab[i - 1] - therm_tab[i]));
	}
	return 600;
}

void read_sensors(void)
{
	uint16_t v;

	v = adc_read(6); adc_t = (adc_t * 3 + v) / 4;
	v = adc_read(5); adc_l = (adc_l * 3 + v) / 4;
	v = adc_read(7); adc_v = (adc_v * 3 + v) / 4;

#if USE_THERM
	temp_ok = (adc_t > 300 && adc_t < 3900);	// out of range = not wired / broken
	temp_x10 = therm_to_x10(adc_t);
#else
	temp_ok = 0;
#endif

	// VR1 -> 2.0 ~ 40.0 C, unless the web app set a limit and VR1 has not moved since
	if (remote_limit && (adc_v > vr_at_remote + 150 || adc_v + 150 < vr_at_remote))
	{
		remote_limit = 0;
		send_event(EV_LIMIT, 0);
	}
	if (!remote_limit)
		limit_x10 = 20 + (int32_t)adc_v * 380 / 4095;

#if USE_LDR
	if (door_open)	door_open = adc_l < ldr_thr + ldr_thr / 5;	// hysteresis
	else		door_open = adc_l < ldr_thr;
#else
	door_open = 0;
#endif
}

// ---------------- 7-segment ----------------
// segment bits (0 = on) : 0 c, 1 dp, 2 f, 3 a, 4 b, 5 d, 6 e, 7 g
#define SA 0x08
#define SB 0x10
#define SC 0x01
#define SD 0x20
#define SE 0x40
#define SF 0x04
#define SG 0x80
#define DP 0x02

uint8_t seg_char(char c)
{
	static const uint8_t num[10] = {
		SA|SB|SC|SD|SE|SF, SB|SC, SA|SB|SG|SE|SD, SA|SB|SG|SC|SD, SF|SG|SB|SC,
		SA|SF|SG|SC|SD, SA|SF|SG|SE|SD|SC, SA|SB|SC, SA|SB|SC|SD|SE|SF|SG, SA|SB|SC|SD|SF|SG };
	uint8_t on = 0;

	if (c >= '0' && c <= '9') on = num[c - '0'];
	else switch (c)
	{
		case 'A': on = SA|SB|SC|SE|SF|SG; break;
		case 'C': on = SA|SD|SE|SF; break;
		case 'd': on = SB|SC|SD|SE|SG; break;
		case 'E': on = SA|SD|SE|SF|SG; break;
		case 'H': on = SB|SC|SE|SF|SG; break;
		case 'L': on = SD|SE|SF; break;
		case 'o': on = SC|SD|SE|SG; break;
		case 'P': on = SA|SB|SE|SF|SG; break;
		case 'r': on = SE|SG; break;
		case '-': on = SG; break;
		default:  on = 0; break;
	}
	return (uint8_t)~on;
}

// 4 characters, dot_pos = digit (0 = rightmost) with the decimal point, 0xFF = none
void seg_show(const char *s, uint8_t dot_pos)
{
	uint8_t i, p;

	for (i = 0; i < 4; i++)
	{
		p = seg_char(s[i]);
		if (3 - i == dot_pos) p &= ~DP;
		seg_pat[3 - i] = p;
	}
}

void seg_temp(void)
{
	char s[5];
	int16_t t = temp_x10;

	if (!temp_ok) { seg_show("----", 0xFF); return; }
	if (alarms & AL_COLD && (ms_tick / 400) & 1) { seg_show("    ", 0xFF); return; }	// flash
	if (t < 0) { t = -t; sprintf(s, "-%2d%d", (t / 10) % 100, t % 10); }
	else	   sprintf(s, "%3d%d", t / 10, t % 10);
	seg_show(s, 1);
}

void seg_count(char letter, int32_t n)
{
	char s[5];
	sprintf(s, "%c%3d", letter, (int)(n > 999 ? 999 : n));
	seg_show(s, 0xFF);
}

// ---------------- LCD : redraw only lines that changed ----------------

void lcd_line(uint8_t n, const char *text)
{
	char buf[17];
	uint8_t i;

	for (i = 0; i < 16 && text[i]; i++) buf[i] = text[i];
	for (; i < 16; i++) buf[i] = ' ';
	buf[16] = 0;
	if (memcmp(buf, shown[n], 17) != 0)
	{
		memcpy(shown[n], buf, 17);
		print_Line(n, buf);
	}
}

void show_msg(const char *a, const char *b, uint16_t ms)
{
	strncpy(msg1, a, 16); msg1[16] = 0;
	strncpy(msg2, b, 16); msg2[16] = 0;
	msg_until = ms_tick + ms;
}

// ---------------- LEDs ----------------

void leds(void)
{
	uint8_t blink = (ms_tick / 250) & 1;
	uint8_t r = 0, g = 0, b = 0;
	uint8_t alarm_now = alarms & (AL_COLD | AL_FORCED | AL_MOVED | AL_ATTACK | AL_LOCKDOWN | AL_AJAR | AL_PINLOCK);

	PIN_OUT(E_GPC, 12) = (state == ST_OPEN);		// LOCKED (low = on)
	PIN_OUT(E_GPC, 13) = (state != ST_OPEN);		// OPEN
	PIN_OUT(E_GPC, 14) = !(alarm_now && blink);		// ALARM
	PIN_OUT(E_GPC, 15) = !link_ok;				// LINK

	if (alarm_now)			r = blink;
	else if (state == ST_WAIT)	b = 1;
	else if (state == ST_OPEN)	{ g = 1; b = 1; }
	else				g = 1;
	PIN_OUT(E_GPA, 14) = !r;
	PIN_OUT(E_GPA, 13) = !g;
	PIN_OUT(E_GPA, 12) = !b;
}

// ---------------- state changes ----------------

void go(uint8_t s)
{
	state = s;
	digits = 0;
	entry = 0;
	send_status();
}

void relock(void)
{
	servo_set(SERVO_LOCK_US);
	alarms &= ~AL_AJAR;
	send_event(EV_RELOCK, drawer);
	go(ST_IDLE);
}

void start_lockdown(uint8_t reason)
{
	if (state == ST_OPEN) servo_set(SERVO_LOCK_US);
	lockdown_reason = reason;
	alarms |= AL_LOCKDOWN;
	if (reason == LD_FORCED) alarms |= AL_FORCED;
	if (reason == LD_MOVED)  alarms |= AL_MOVED;
	nonce_valid = 0;
	send_event(EV_LOCKDOWN, reason);
	go(ST_LOCKDOWN);
}

void clear_lockdown(uint8_t by)
{
	alarms &= ~(AL_LOCKDOWN | AL_FORCED | AL_MOVED | AL_ATTACK);
	lockdown_reason = 0;
	send_event(EV_CLEARED, by);
	show_msg("LOCKDOWN CLEARED", by == 1 ? "by ADMIN" : "by REMOTE", 2500);
	go(ST_IDLE);
}

void attack(uint8_t reason)
{
	attack_reason = reason;
	attack_until = ms_tick + 6000;
	alarms |= AL_ATTACK;
	send_event(EV_ATTACK, reason);
}

// ---------------- frames from the supervisor unit ----------------

void handle_frame(void)
{
	uint8_t t = frame[0], len = frame[1];
	uint8_t b[8], i;
	uint16_t ctr;

	if (t == 'P' && len == 1)
	{
		last_ping = ms_tick;
		if (!link_ok) { link_ok = 1; send_status(); }
		if (frame[2] == 1) last_ctr = 0;	// supervisor just booted : counter restarts
		return;
	}
	if ((t != 'A' && t != 'C') || len != 8) return;

	for (i = 0; i < 8; i++) b[i] = frame[2 + i];
	xtea_decrypt(b);
	ctr = ((uint16_t)b[4] << 8) | b[5];

	if (t == 'A')		// approval : nonce(4) ctr(2) decision(1) MAGIC
	{
		uint32_t n = ((uint32_t)b[0] << 24) | ((uint32_t)b[1] << 16) | ((uint32_t)b[2] << 8) | b[3];

		if (b[7] != MAGIC)				{ attack(AT_BAD_KEY); return; }
		// real reply to our last request, but it was cancelled / timed out first :
		// fresh counter, so not a replay. Ignore it (a replay of it later has an old counter)
		if (n == nonce && !nonce_valid && ctr > last_ctr)
		{
			last_ctr = ctr;
			show_msg("APPROVAL CAME", "too late: ignored", 2500);
			return;
		}
		if (state != ST_WAIT || !nonce_valid || n != nonce)	{ attack(AT_REPLAY); return; }
		if (ctr <= last_ctr)				{ attack(AT_OLD_COUNTER); return; }
		last_ctr = ctr;
		nonce_valid = 0;				// one use only
		if (b[6] == 'Y')
		{
			servo_set(SERVO_OPEN_US);
			door_was_opened = 0;
			deadline = ms_tick + OPEN_S * 1000UL;
			send_event(EV_APPROVED, drawer);
			go(ST_OPEN);
		}
		else
		{
			send_event(EV_REJECTED, drawer);
			show_msg("REQUEST REJECTED", "by supervisor", 2500);
			go(ST_IDLE);
		}
	}
	else			// command : CMD_MAGIC cmd arg(2) ctr(2) 0 MAGIC
	{
		uint16_t arg = ((uint16_t)b[2] << 8) | b[3];

		if (b[7] != MAGIC || b[0] != CMD_MAGIC)	{ attack(AT_BAD_KEY); return; }
		if (ctr <= last_ctr)				{ attack(AT_OLD_COUNTER); return; }
		last_ctr = ctr;
		if (b[1] == CMD_LOCKDOWN)
		{
			if (state != ST_LOCKDOWN) start_lockdown(arg == LD_MOVED ? LD_MOVED : LD_REMOTE);
		}
		else if (b[1] == CMD_CLEAR)
		{
			alarms &= ~(AL_PINLOCK | AL_AJAR);
			if (state == ST_LOCKDOWN) clear_lockdown(2);
			else if (state == ST_LOCKOUT) { wrong_pins = 0; go(ST_IDLE); }
			alarms &= ~AL_ATTACK;
		}
		else if (b[1] == CMD_LIMIT && arg >= 20 && arg <= 400)
		{
			limit_x10 = arg;
			remote_limit = 1;
			vr_at_remote = adc_v;
			send_event(EV_LIMIT, 1);
		}
	}
}

// ---------------- keypad input ----------------

int8_t find_staff(uint16_t id)
{
	uint8_t i;
	for (i = 0; i < N_STAFF; i++)
		if (staff[i].id == id) return i;
	return -1;
}

void on_key(uint8_t k)
{
	switch (state)
	{
	case ST_IDLE:
	case ST_LOCKDOWN:
		cur_staff = -1;
		cur_id = 0;
		go(ST_ID);				// a lockdown stays set in alarms
		entry = k;
		digits = 1;
		break;

	case ST_ID:
		entry = entry * 10 + k;
		if (++digits == 4)
		{
			cur_id = entry;
			if (cur_id == 1111) { go(ST_DIAG); break; }
			cur_staff = find_staff(cur_id);
			go(ST_PIN);			// unknown IDs still ask for a PIN (no hint)
		}
		break;

	case ST_PIN:
		entry = entry * 10 + k;
		if (++digits == 4)
		{
			if (cur_staff >= 0 && staff[cur_staff].pin == entry)
			{
				wrong_pins = 0;
				send_event(EV_LOGIN, 0);
				if (alarms & AL_LOCKDOWN)
				{
					if (staff[cur_staff].admin) clear_lockdown(1);
					else { show_msg("LOCKDOWN ACTIVE", "admin needed", 2500); go(ST_LOCKDOWN); }
				}
				else go(ST_DRAWER);
			}
			else
			{
				char t[17];
				wrong_pins++;
				send_event(EV_WRONG_PIN, wrong_pins);
				if (wrong_pins >= 3)
				{
					alarms |= AL_PINLOCK;
					deadline = ms_tick + LOCKOUT_S * 1000UL;
					send_event(EV_PIN_LOCKOUT, 0);
					go(ST_LOCKOUT);
				}
				else
				{
					sprintf(t, "attempt %d of 3", wrong_pins);
					show_msg("WRONG ID / PIN", t, 2000);
					go((alarms & AL_LOCKDOWN) ? ST_LOCKDOWN : ST_IDLE);
				}
			}
		}
		break;

	case ST_DRAWER:
		if (k == 1 || k == 2)
		{
			uint8_t d[7];
			drawer = (k == 1) ? 'A' : 'B';
			nonce = rand32();
			nonce_valid = 1;
			d[0] = cur_id >> 8;  d[1] = cur_id & 0xFF;  d[2] = drawer;
			d[3] = nonce >> 24;  d[4] = nonce >> 16;  d[5] = nonce >> 8;  d[6] = nonce;
			send_frame('R', d, 7);
			send_event(EV_REQUEST, drawer);
			deadline = ms_tick + WAIT_S * 1000UL;
			go(ST_WAIT);
		}
		break;
	}
}

void on_switch(void)
{
	alarms &= ~AL_ATTACK;
	msg_until = ms_tick;
	switch (state)
	{
	case ST_ID: case ST_PIN: case ST_DRAWER: case ST_DIAG:
		go((alarms & AL_LOCKDOWN) ? ST_LOCKDOWN : ST_IDLE);
		break;
	case ST_WAIT:
		nonce_valid = 0;
		send_event(EV_CANCEL, drawer);
		go(ST_IDLE);
		break;
	}
}

// ---------------- checks done every loop ----------------

void checks(void)
{
	// link : the supervisor pings every second
	if (link_ok && reached(last_ping + 3000))
	{
		link_ok = 0;
		send_status();
	}
	if (link_ok) alarms &= ~AL_NOLINK; else alarms |= AL_NOLINK;

	if ((alarms & AL_ATTACK) && reached(attack_until)) alarms &= ~AL_ATTACK;

	// cold chain
	if (temp_ok && temp_x10 > limit_x10)
	{
		cool_since = ms_tick;
		if (!(alarms & AL_COLD) && reached(cold_since + COLD_DELAY_MS))
		{
			alarms |= AL_COLD;
			send_event(EV_COLD_ON, (uint8_t)(temp_x10 / 10));
		}
	}
	else
	{
		cold_since = ms_tick;
		if ((alarms & AL_COLD) && (!temp_ok || temp_x10 < limit_x10 - 5) && reached(cool_since + COLD_DELAY_MS))
		{
			alarms &= ~AL_COLD;
			send_event(EV_COLD_OFF, (uint8_t)(temp_x10 / 10));
		}
		if (temp_ok && temp_x10 >= limit_x10 - 5) cool_since = ms_tick;
	}

	// door
	if (state == ST_OPEN)
	{
		if (door_open && !door_was_opened)
		{
			door_was_opened = 1;
			send_event(EV_DOOR_OPEN, drawer);
		}
		if (!door_open && door_was_opened) relock();	// closed again
		else if (reached(deadline))
		{
			if (!door_open) relock();
			else if (!(alarms & AL_AJAR))
			{
				alarms |= AL_AJAR;
				send_event(EV_AJAR, drawer);
			}
		}
	}
	else if (door_open && state != ST_DIAG)		// light inside while locked
	{
		if (reached(forced_since + 300) && !(alarms & AL_FORCED))
		{
			send_event(EV_FORCED, 0);
			start_lockdown(LD_FORCED);
		}
	}
	else forced_since = ms_tick;

	if (state == ST_WAIT && reached(deadline))
	{
		nonce_valid = 0;
		send_event(EV_TIMEOUT, drawer);
		show_msg("NO APPROVAL", "request expired", 2500);
		go(ST_IDLE);
	}
	if (state == ST_LOCKOUT && reached(deadline))
	{
		wrong_pins = 0;
		alarms &= ~AL_PINLOCK;
		go(ST_IDLE);
	}
}

// ---------------- screens ----------------

void screen(void)
{
	char t[17];
	static const char *ld_text[] = { "", "Door forced", "Cabinet moved", "Remote command" };
	static const char *at_text[] = { "", "REPLAY / NONCE", "OLD COUNTER", "WRONG KEY" };

	// 7-segment
	if (state == ST_WAIT)		seg_count('P', secs_left(deadline));
	else if (state == ST_OPEN)	seg_count('o', secs_left(deadline));
	else if (state == ST_LOCKOUT)	seg_count('L', secs_left(deadline));
	else if (state == ST_LOCKDOWN && (ms_tick / 500) & 1) seg_show("LOCd", 0xFF);
	else				seg_temp();

	if (alarms & AL_ATTACK)
	{
		lcd_line(0, "!! ATTACK !!    ");
		lcd_line(1, "BLOCKED:");
		lcd_line(2, at_text[attack_reason]);
		lcd_line(3, "event logged");
		return;
	}
	if (!reached(msg_until))
	{
		lcd_line(0, "MedVault");
		lcd_line(1, msg1);
		lcd_line(2, msg2);
		lcd_line(3, "");
		return;
	}

	switch (state)
	{
	case ST_IDLE:
		lcd_line(0, "MedVault  LOCKED");
		if (temp_ok) sprintf(t, "T %2d.%dC max%2d.%d", temp_x10 / 10, (temp_x10 < 0 ? -temp_x10 : temp_x10) % 10,
			limit_x10 / 10, limit_x10 % 10);
		else sprintf(t, "T --.-C max%2d.%d", limit_x10 / 10, limit_x10 % 10);
		lcd_line(1, t);
		sprintf(t, "Door %s %s", door_open ? "OPEN  " : "CLOSED", link_ok ? "LINK" : "----");
		lcd_line(2, t);
		if (alarms & AL_COLD)		lcd_line(3, (ms_tick / 500) & 1 ? "!COLD CHAIN!" : "");
		else if (!link_ok)		lcd_line(3, "No supervisor");
		else				lcd_line(3, "Key = Staff ID");
		break;

	case ST_ID:
		lcd_line(0, (alarms & AL_LOCKDOWN) ? "ADMIN LOGIN" : "STAFF LOGIN");
		sprintf(t, "ID : %s", digits == 0 ? "____" : "");
		if (digits)
		{
			uint8_t i;
			sprintf(t, "ID : %u", entry);
			for (i = digits; i < 4; i++) strcat(t, "_");
		}
		lcd_line(1, t);
		lcd_line(2, "");
		lcd_line(3, "SW_INT = cancel");
		break;

	case ST_PIN:
		sprintf(t, "ID : %u", cur_id);
		lcd_line(1, t);
		strcpy(t, "PIN: ");
		{
			uint8_t i;
			for (i = 0; i < 4; i++) strcat(t, i < digits ? "*" : "_");
		}
		lcd_line(2, t);
		break;

	case ST_DRAWER:
		sprintf(t, "Hi %s", staff[cur_staff].name);
		lcd_line(0, t);
		lcd_line(1, "1 = Drawer A");
		lcd_line(2, "    (controlled)");
		lcd_line(3, "2 = Drawer B vac");
		break;

	case ST_WAIT:
		lcd_line(0, "WAITING APPROVAL");
		sprintf(t, "%s  Drawer %c", staff[cur_staff].name, drawer);
		lcd_line(1, t);
		sprintf(t, "Supervisor  %2ds", (int)secs_left(deadline));
		lcd_line(2, t);
		lcd_line(3, "SW_INT = cancel");
		break;

	case ST_OPEN:
		lcd_line(0, "*** UNLOCKED ***");
		sprintf(t, "%s  Drawer %c", staff[cur_staff].name, drawer);
		lcd_line(1, t);
		if (alarms & AL_AJAR)	lcd_line(2, (ms_tick / 500) & 1 ? "CLOSE THE DOOR!" : "");
		else			{ sprintf(t, "Relock in %2ds", (int)secs_left(deadline)); lcd_line(2, t); }
		lcd_line(3, door_open ? "Door: OPEN" : "Door: closed");
		break;

	case ST_LOCKOUT:
		lcd_line(0, "PIN LOCKOUT");
		lcd_line(1, "3 wrong PINs");
		sprintf(t, "Wait %2ds", (int)secs_left(deadline));
		lcd_line(2, t);
		lcd_line(3, "photo logged");
		break;

	case ST_LOCKDOWN:
		lcd_line(0, (ms_tick / 500) & 1 ? "!!! LOCKDOWN !!!" : "");
		lcd_line(1, ld_text[lockdown_reason < 4 ? lockdown_reason : 0]);
		lcd_line(2, "Admin login or");
		lcd_line(3, "remote clear");
		break;

	case ST_DIAG:
		sprintf(t, "THERM %4u %3d", adc_t, temp_x10);
		lcd_line(0, t);
		sprintf(t, "LDR %4u thr%4u", adc_l, ldr_thr);
		lcd_line(1, t);
		sprintf(t, "VR1 %4u door %d", adc_v, door_open);
		lcd_line(2, t);
		sprintf(t, "link %d ctr %u", link_ok, last_ctr);
		lcd_line(3, t);
		break;
	}
}

// ---------------- main ----------------

int32_t main(void)
{
	uint8_t key, last_key = 0, i;
	uint32_t next_status = 0, next_loop = 0;
	uint32_t dark = 0;

	UNLOCKREG();
	DrvSYS_Open(48000000);
	LOCKREG();

	for (i = 0; i < 8; i++) DrvGPIO_Open(E_GPE, i, E_IO_OUTPUT);
	for (i = 4; i < 8; i++) DrvGPIO_Open(E_GPC, i, E_IO_OUTPUT);
	for (i = 12; i < 16; i++) { DrvGPIO_Open(E_GPC, i, E_IO_OUTPUT); PIN_OUT(E_GPC, i) = 1; }
	for (i = 12; i < 15; i++) { DrvGPIO_Open(E_GPA, i, E_IO_OUTPUT); PIN_OUT(E_GPA, i) = 1; }
	DrvGPIO_Open(SERVO_PORT, SERVO_PIN, E_IO_OUTPUT);
	PIN_OUT(SERVO_PORT, SERVO_PIN) = 0;
	DrvGPIO_Open(E_GPB, 15, E_IO_QUASI);	// SW_INT, pulled high
	DrvGPIO_EnableEINT1(E_IO_FALLING, E_MODE_EDGE, sw_callback);

	init_LCD();
	clear_LCD();
	memset(shown, 0, sizeof(shown));
	lcd_line(0, "MedVault");
	lcd_line(1, "Cabinet unit");
	lcd_line(2, (SYS->RSTSRC.RSTS_WDT) ? "(watchdog reset)" : "starting...");
	SYS->RSTSRC.RSTS_WDT = 1;

	// start-up steps on line 3 : if the board freezes, the last number shows where
	lcd_line(3, "boot 1 ADC");
	init_adc();
	lcd_line(3, "boot 2 timers");
	init_timers();
	lcd_line(3, "boot 3 UART1");
	init_uart();

	// door must be closed at power on : measure the dark level of the LDR
	lcd_line(3, "boot 4 LDR read");
	for (i = 0; i < 16; i++) { dark += adc_read(5); DrvSYS_Delay(5000); }
	dark /= 16;
	ldr_thr = dark * 55 / 100;
	if (ldr_thr < 300) ldr_thr = 300;
	lcd_line(3, "boot 5 sensors");
	adc_t = adc_read(6); adc_l = adc_read(5); adc_v = adc_read(7);
	rng ^= dark << 7;

	lcd_line(3, "boot 6 tick");
	{
		uint32_t t0 = ms_tick;
		uint32_t n = 0;
		while (ms_tick == t0 && ++n < 2000000);	// Timer0 must be counting
		if (ms_tick == t0) lcd_line(3, "boot 6 NO TICK!");
	}
	servo_set(SERVO_LOCK_US);
	lcd_line(3, "boot 7 watchdog");
	init_wdt();
	lcd_line(3, "boot 8 send");
	send_event(EV_BOOT, 0);
	lcd_line(3, "boot 9 running");

	while (1)
	{
		while (!reached(next_loop));		// 20 ms loop
		next_loop = ms_tick + 20;
		feed_wdt();

		key = key_scan();
		read_sensors();
		if (key && !last_key)
		{
			msg_until = ms_tick;		// any key closes a message
			on_key(key);
		}
		last_key = key;

		if (sw_pressed)
		{
			sw_pressed = 0;
			on_switch();
		}
		while (fr_tail != fr_head)		// every frame that came in
		{
			for (i = 0; i < 16; i++) frame[i] = frames[fr_tail][i];
			fr_tail = (fr_tail + 1) % NFRAMES;
			handle_frame();
		}
		checks();

		if (reached(next_status))
		{
			next_status = ms_tick + 1000;
			send_status();
		}
		screen();
		leds();
	}
}
