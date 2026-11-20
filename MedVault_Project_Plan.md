# MedVault: Smart Controlled-Drug & Vaccine Cabinet

**Course:** RBB3023 Embedded and IoT Systems (Patrick Sebastian), Project (30%)
**Student:** Chan Li Kai (22010900)
**Boards:** Nu-LB-NUC140 (Keil, C) + mbed NXP LPC1768 with App Board (Mbed OS 6 RTOS, C++) + Raspberry Pi 3 (Python)

---

## 1. The real-world problem

Hospitals keep controlled drugs (for example morphine) and vaccines in locked cabinets. The rules are strict:

| Rule | Why it matters | What MedVault does |
|---|---|---|
| **Two-person rule** | One person alone must not be able to take controlled drugs | Nurse enters PIN on the cabinet, **and** a supervisor approves on a second device |
| **Audit trail** | Every opening must be recorded: who, when, approved by whom | Every event is saved in a database on the Pi, **with a photo** |
| **Cold chain (2–8 °C)** | Vaccines that get too warm must be thrown away | Two temperature sensors, alarm on the cabinet and on the phone |
| **Tamper detection** | Forcing or moving the cabinet must raise an alarm | Light sensor (door forced), accelerometer (cabinet moved), heat (drilling) |
| **Secure communication** | A copied radio or wire signal must not open the lock | Encrypted challenge-response with rolling code: replay attacks fail |

Commercial systems cost thousands of ringgit. MedVault does the same job with two learning boards, a Raspberry Pi 3 and about RM40 of parts.

---

## 2. System overview

```
                         WiFi (phone / laptop browser)
                                    │
        ┌───────────────────────────┴────────────────────────────┐
        │  [3] HOSPITAL GATEWAY: Raspberry Pi 3 (Python)          │
        │  - audit log database (SQLite)                          │
        │  - web dashboard (Flask): live temp graph, door state,  │
        │    alarms, photo log, REMOTE LOCKDOWN button            │
        │  - USB webcam: photo on every opening / alarm           │
        │  - Telegram bot: instant phone alert with the photo     │
        │  - security test: replay-attack script                  │
        └───────────────────────────┬────────────────────────────┘
                                    │ USB serial 115200
        ┌───────────────────────────┴────────────────────────────┐
        │  [2] SUPERVISOR UNIT: LPC1768 + App Board (Mbed RTOS)   │
        │  - shows "Nurse 1023 requests DRAWER A, approve?"       │
        │  - approve = joystick + secret tilt gesture             │
        │  - XTEA encryption, rolling code, challenge-response    │
        │  - LM75B backup temperature, MMA7660 tamper (moved)     │
        │  - RTOS threads + queues + semaphore + event flags      │
        └───────────────────────────┬────────────────────────────┘
                                    │ UART 9600, encrypted frames
        ┌───────────────────────────┴────────────────────────────┐
        │  [1] CABINET CONTROLLER: Nu-LB-NUC140 (Keil, C)         │
        │  - keypad: staff ID + PIN, menu                         │
        │  - LCD 128x64: menus, status, alarms                    │
        │  - 7-segment: fridge temperature / door countdown       │
        │  - ADC: thermistor (temp), LDR (door/forced), VR1 (limit)│
        │  - servo lock (PWM), timers, watchdog                   │
        └─────────────────────────────────────────────────────────┘
```

**Rule of the design:** neither board can open the cabinet alone. The cabinet needs the nurse's PIN **and** a valid encrypted approval from the supervisor unit.

---

## 3. Board 1: Cabinet Controller (Nu-LB-NUC140)

### Jobs
- Nurse login: staff ID + 4-digit PIN on the keypad
- Choose a drawer (A = controlled drugs, B = vaccines)
- Send an access request to the supervisor unit, then check its encrypted reply
- Open the servo lock, start the 30 s door timer, relock automatically
- Read the sensors all the time and raise alarms
- Show everything on the LCD and the 7-segment display

### Pin plan

| Function | Pin | Notes |
|---|---|---|
| Keypad 3x3 | GPA0–GPA5 | `ScanKey()`, check key numbers on the real board (top-middle = 1) |
| LCD 128x64 | SPI3 (on board) | `print_Line()`, `draw_LCD()` |
| 7-segment | GPC4–7 digit, GPE0–7 segment | refreshed in Timer0 1 ms ISR |
| Status LEDs | GPC12–15 | LOCKED / OPEN / ALARM / LINK |
| RGB LED | GPA12–14 | green OK, blue waiting approval, red alarm |
| SW_INT button | GPB15 | "door closed" / alarm acknowledge |
| **VR1** | ADC7 (GPA7) | set the temperature alarm limit |
| **Thermistor** (NTC 10k + 10k resistor divider) | ADC6 (GPA6) | fridge temperature |
| **LDR** (+ 10k resistor divider) | ADC5 (GPA5) | light inside the box = door open / forced. Shares the pin with a keypad column, switched like Lab 6 |
| **Servo SG90** (lock) | GPB8 (JP5 pin 9), pulse made by Timer0 + Timer1 | 50 Hz, 1 ms = locked, 2 ms = open. Power from the board 5V pin |
| **UART1** to LPC1768 | GPB5 TX1 (JP5 pin 6), GPB4 RX1 (JP5 pin 5) | 9600 8N1, GND to GND. Not UART0: GPB0/1 are tied to the RS232 chip |

> GPA15 is not used: it goes to the audio codec. Check the LPC's 3.3 V TX is read correctly by the NUC140. If not reliable, add a small logic-level shifter (about RM3).

### Timers
| Timer | Use |
|---|---|
| Timer0 1 ms | 7-segment scan, keypad debounce, servo pulse (if software PWM) |
| Timer1 | 30 s door-open countdown, auto relock |
| Timer2 | 10 s approval time limit, 3-wrong-PIN lockout (60 s) |
| WDT | resets the controller if the program freezes (real devices must not hang) |

### Screens (LCD)
`IDLE` (temp + LOCKED) → `ENTER ID` → `ENTER PIN` → `CHOOSE DRAWER` → `WAITING APPROVAL` → `OPEN 30s` → `LOCKED`
Alarm screens: `COLD CHAIN ALARM`, `TAMPER: FORCED`, `LOCKDOWN (REMOTE)`, `ATTACK BLOCKED`, `PIN LOCKOUT 60s`

### Size limit
Keil MDK-Lite only links up to **32 KB**. Keep this board lean: no big fonts or images, small XTEA code (about 0.5 KB). Heavy work goes to the LPC1768 and the Pi.

---

## 4. Board 2: Supervisor Unit (LPC1768 + App Board, Mbed OS 6 RTOS)

### Jobs
- Receive access requests from the cabinet and show them on the 128x32 LCD
- Supervisor approves with **joystick centre + secret tilt gesture** (for example: left, left, forward), or rejects with joystick down
- Do the encrypted challenge-response with the cabinet
- Watch its own sensors: LM75B backup temperature, MMA7660 accelerometer (cabinet moved / shaken)
- Forward every event to the Pi, and pass remote commands (lockdown) from the Pi to the cabinet

### Pins (App Board)
| Function | Pin |
|---|---|
| UART3 to NUC140 | p9 TX, p10 RX (XBee header), 9600 |
| USB serial to Pi | USBTX / USBRX, 115200 |
| LCD C12832 | p5, p6, p7, p8, p11 (own `lcd_c12832.h` driver) |
| Joystick | up p15, down p12, left p13, right p16, centre p14 |
| POT1 / POT2 | p19 / p20 (gesture sensitivity, display brightness) |
| RGB LED | p23 / p24 / p25 (low = on) |
| MMA7660 + LM75B | I2C SDA p28, SCL p27 |

### RTOS design
| Thread | Priority | Job |
|---|---|---|
| `link_nuc` | high | read/write UART3 frames, check CRC, put messages in a Queue |
| `security` | high | XTEA encrypt/decrypt, rolling-code counter, nonce check |
| `sensors` | normal | read accelerometer (50 Hz) and LM75B (1 Hz), gesture recogniser, tamper detect |
| `ui` | normal | LCD screens, joystick, LEDs |
| `link_pi` | low | send events to the Pi, receive LOCKDOWN / UNLOCK-REMOTE commands |

- **Queue / Mail:** messages between threads (no shared globals without protection)
- **Mutex:** guards the LCD and the I2C bus
- **Semaphore:** "approval done" signal from `ui` to `security`
- **EventFlags:** alarm states (TAMPER, COLD, ATTACK, LOCKDOWN) seen by all threads
- Remember: no `printf`, mutex or `BusOut` inside Ticker / InterruptIn callbacks.

---

## 5. Board 3: Hospital Gateway (Raspberry Pi 3)

| Part | What it does |
|---|---|
| `gateway.py` | reads the LPC USB serial, saves every event to SQLite (`medvault.db`) |
| **USB webcam** (OpenCV) | takes a photo on: door opened, wrong PIN x3, tamper, cold alarm. Photo saved with the event |
| **Flask web dashboard** (WiFi) | live temperature graph (both sensors), door state, audit log table **with photos**, alarm list, **REMOTE LOCKDOWN** and **CLEAR ALARM** buttons |
| **Telegram bot** | sends the alert text + photo to the phone in about 2 seconds |
| `replay_attack.py` | security test: records a real unlock frame, sends it again later, shows it is rejected |

Example Telegram alert:
```
🚨 MedVault TAMPER ALARM
Cabinet 1, Drawer A (controlled drugs)
Door forced open while LOCKED
14:32:07, 8 Nov 2026
[photo]
```

---

## 6. Communication protocol

### Frame (both links)
```
0xA5 | type | seq | len | payload (encrypted) | CRC-8
```

| Type | From → To | Meaning |
|---|---|---|
| `REQ` | NUC → LPC | access request: staff ID, drawer |
| `CHAL` | NUC → LPC | random challenge (nonce) for this request |
| `RESP` | LPC → NUC | XTEA(key, nonce + rolling counter + APPROVE/REJECT) |
| `STATUS` | NUC → LPC → Pi | temperatures, door state, lock state, every 1 s |
| `EVENT` | NUC/LPC → Pi | OPENED, CLOSED, WRONG_PIN, COLD, TAMPER, ATTACK |
| `CMD` | Pi → LPC → NUC | LOCKDOWN, CLEAR (also encrypted) |

### Security (why replay fails)
1. Each request, the cabinet makes a **new random challenge** (from ADC noise + timer value).
2. The supervisor encrypts **challenge + rolling counter + decision** with **XTEA** (128-bit shared key).
3. The cabinet decrypts and checks: challenge matches, counter is higher than the last one, CRC is OK.
4. A recorded old reply has the **wrong challenge and an old counter**, so the cabinet rejects it, shows `ATTACK BLOCKED` and logs it.

---

## 7. Alarms

| Alarm | Detected by | Reaction |
|---|---|---|
| **Cold chain** | thermistor (NUC) or LM75B (LPC) above the VR1 limit for 10 s | LCD + red LED + 7-seg flashes temp, Pi logs, Telegram |
| **Door forced** | LDR sees light while the lock is LOCKED | lockdown, photo, Telegram |
| **Cabinet moved** | MMA7660 shake / tilt over threshold | lockdown, photo, Telegram |
| **PIN attack** | 3 wrong PINs | 60 s lockout, photo |
| **Replay attack** | bad challenge / old counter | `ATTACK BLOCKED`, logged |
| **Door left open** | door timer > 30 s | warning, auto relock when closed |
| **Link lost** | no STATUS for 3 s | cabinet stays locked (fail-secure), LINK LED off |

---

## 8. Course requirements check

| Requirement | Where |
|---|---|
| a. GPIO switch input (must) | NUC keypad + SW_INT, LPC joystick |
| b. 7-seg / LCD (must) | NUC 7-seg (temp, countdown) + LCD 128x64, LPC LCD 128x32 |
| c. Timer | NUC Timer0/1/2 + WDT, LPC Ticker / RTOS timing |
| d. Multiple ADC | NUC: ADC5 LDR, ADC6 thermistor, ADC7 VR1. LPC: POT1, POT2 |
| e. UART / SPI / I2C / WiFi | UART (NUC ↔ LPC, LPC ↔ Pi), SPI (both LCDs), I2C (MMA7660, LM75B), WiFi (Pi dashboard + Telegram) |
| f. RTOS (bonus) | LPC1768: 5 threads, Queue, Mutex, Semaphore, EventFlags |
| 2. Boards communicate | full unlock needs both boards; Pi joins as the gateway |

---

## 9. Parts list

| Part | Approx. cost |
|---|---|
| NTC thermistor 10k + 10k resistor | RM1 |
| LDR + 10k resistor | RM1 |
| SG90 micro servo | RM8 |
| USB webcam (for the Pi) | RM25 |
| Jumper wires (male-female) | RM3 |
| Logic-level shifter (only if needed) | RM3 |
| Small box (cardboard / acrylic) as the cabinet | RM0–10 |
| **Total** | **about RM40** |

Already have: Nu-LB-NUC140, LPC1768 + App Board, Raspberry Pi 3, USB cables.

---

## 10. Build stages (always keep a working version)

| Stage | Goal | Done when |
|---|---|---|
| **1. Link** | NUC ↔ LPC frames with CRC, Pi reads the LPC | STATUS frames shown on both LCDs and on the Pi terminal |
| **2. Access flow** | ID + PIN, request, approve with joystick, servo opens / relocks | full unlock works (no encryption yet) |
| **3. Security** | XTEA, challenge, rolling counter, tilt gesture | replay script is rejected |
| **4. Sensors & alarms** | thermistor, LDR, VR1, LM75B, accelerometer, timers, WDT | each alarm triggers correctly |
| **5. RTOS clean-up** | LPC split into threads + queues + flags | runs for 30 min without freezing |
| **6. Pi gateway** | SQLite log, Flask dashboard, remote lockdown | dashboard works on a phone over WiFi |
| **7. Camera + Telegram** | photo on events, phone alert | alert with photo arrives in < 3 s |
| **8. Box + demo** | mount everything in the box, record video | demo script below runs smoothly |

---

## 11. Demo script (about 3 minutes)

1. **Normal use:** nurse enters ID + PIN, picks Drawer A. Supervisor unit shows the request, supervisor approves with a tilt gesture. Servo opens, 7-seg counts down 30 s, door relocks. Dashboard shows the log entry **with the nurse's photo**.
2. **Cold chain:** hold the thermistor with fingers. 7-seg temperature rises, `COLD CHAIN ALARM`, phone gets a Telegram alert.
3. **Break-in:** open the box lid while locked (LDR sees light) or shake the box. Lockdown, photo of the "thief", **phone buzzes with their face**.
4. **Remote lockdown:** tap LOCKDOWN on the phone, the servo locks at once.
5. **Hacker test:** run `replay_attack.py` on the Pi. The cabinet shows `ATTACK BLOCKED`, the dashboard logs the attack.

---

## 12. Risks

| Risk | Plan |
|---|---|
| NUC140 32 KB Keil limit | lean code, no images; heavy work on LPC and Pi |
| 3.3 V / 5 V UART levels | test first; add level shifter if bytes are wrong |
| Servo noise resets the board | power the servo from 5V with a 100 µF capacitor, common GND |
| LPC old firmware board (slow sleep) | use the newer-firmware board (E:/COM9) as the supervisor unit |
| Pi WiFi at the demo venue | Pi can make its own WiFi hotspot as a backup |
| Telegram needs internet | dashboard still works offline; Telegram is the bonus |
