# MedVault (group VaultX): how to build, wire and run

Chan Li Kai (22010900), William Wong Xiao Kang (22010943). RBB3023 Project.

| Folder | What |
|---|---|
| `Proposal/` | Proposal PDF (`make_proposal.py` rebuilds it) |
| `MedVault_Project_Plan.md` | Full design (pins, protocol, alarms, stages) |
| `NUC140_Cabinet/` | Cabinet controller: `main.c` + Keil project (`Keil/Proj_New.uvprojx`) |
| `LPC1768_Supervisor/` | Copy of the supervisor program + ready `.bin` (real program: `C:\Users\TUF FA707RC-HX024W\Mbed Programs\MedVault_Supervisor`) |
| `Pi_Gateway/` | Raspberry Pi gateway: web app, camera, Telegram, replay test |

**Status (17 Nov 2026):** all code is written and reviewed; both firmwares build with 0 errors (NUC140 30.2 KB of 32 KB) and the gateway passes a full simulated run. Nothing has been run on the real boards yet.

---

## 0. Parts list

**Already have**
- Nu-LB-NUC140 learning board + USB cable
- mbed LPC1768 + App Board + USB cable. The accelerometer, temperature sensor, LCD, joystick and pots are already on the App Board.
- Raspberry Pi 3 + microSD card (16 GB or more, Raspberry Pi OS) + 5 V 2.5 A power supply

**To buy (about RM40)**

| # | Part | Qty | For | Approx. |
|---|---|---|---|---|
| 1 | NTC thermistor 10k, B3950 (5 mm bead or probe) | 1 (+1 spare) | cabinet temperature, ADC6 | RM1 |
| 2 | LDR / photoresistor (GL5528) | 1 | door open / forced, ADC5 | RM0.50 |
| 3 | 10 kΩ resistor | 2 | voltage dividers for 1 and 2 | RM0.20 |
| 4 | SG90 micro servo | 1 | door lock, GPB8 | RM7 |
| 5 | 100 µF capacitor (10 V or more) | 1 | across servo 5V/GND, stops resets | RM0.30 |
| 6 | USB webcam (any UVC, 480p/720p) | 1 | event photos on the Pi | RM25 |
| 7 | Jumper wires, male-female + male-male | 10 + 10 | all wiring | RM4 |
| 8 | Mini breadboard (170 holes) | 1 | the two dividers | RM3 |
| 9 | Small box with a lid (cardboard / plastic / acrylic) | 1 | the cabinet; servo arm = bolt | RM0–10 |
| – | Logic-level shifter (only if the UART link misbehaves) | 0–1 | NUC140 to LPC1768 levels | RM3 |

---

## 1. Wiring

NUC140 headers: JP4 = GPIOA, JP5 = GPIOB (pin *n* = GPx(*n*−1), pins 17/18 = GND).
Power pins for outside parts: **Vcc33** (3.3 V), **Vcc5** (5 V), **GND**, next to the power switch.

```
Board link (UART1, 9600, jumper wires, no breadboard)
  NUC140 GPB5 TX1 (JP5 pin 6) ────────► LPC1768 p10 (RX)
  NUC140 GPB4 RX1 (JP5 pin 5) ◄──────── LPC1768 p9  (TX)
  NUC140 GND      (JP5 pin 17) ───────  LPC1768 GND

Breadboard
  Thermistor:  Vcc33 ── 10k ──┬── NTC ── GND        ● = NUC140 GPA6 (ADC6, JP4 pin 7)
  LDR:         Vcc33 ── 10k ──┬── LDR ── GND        ● = NUC140 GPA5 (ADC5, JP4 pin 6)
  Servo SG90:  red = Vcc5, brown = GND, orange = NUC140 GPB8 (JP5 pin 9)
               100 µF across red/brown (long leg = +, to Vcc5)

Raspberry Pi USB ──────────── LPC1768 USB (power + serial 115200)
```

**Why these pins:** GPB0/GPB1 (UART0) are tied to the board's RS232 chip, and GPA15 goes to the audio codec. UART1 on GPB4/GPB5 is the pair already proven in Assessment 2 (Task 2A).

**Build order**
1. **Power off** both boards (unplug USB) before wiring.
2. **Breadboard rails:** wire Vcc33 to the + rail and GND to the − rail. Don't use Vcc5 for the rails: the ADC only reads up to 3.3 V.
3. **Thermistor:** 10k from the + rail to an empty row; thermistor from that row to the − rail; a jumper from that row to GPA6 (JP4 pin 7).
4. **LDR:** the same in another row, with the jumper going to GPA5 (JP4 pin 6). Use long wires so the LDR can sit **inside the box**.
5. **Servo:** a row for 5 V (jumper from Vcc5) and the GND rail. Put the 100 µF across them, long leg on 5 V. Servo red → 5 V row, brown → GND, orange → GPB8 (JP5 pin 9). The servo plug is female, so use male-male jumpers into the breadboard.
6. **Board link:** three jumpers straight between the boards: GPB5 → p10, GPB4 ← p9, GND → GND. Wire it the same way as Assignment 1.
7. **Check before power-on:** nothing goes from Vcc5 to an ADC pin, and no 5 V reaches the + rail.
8. Plug in USB with the **box lid closed**: the NUC140 learns the LDR's dark level at power-on.

- Not wired yet? Set `USE_LDR 0` / `USE_THERM 0` at the top of `NUC140_Cabinet/main.c`, otherwise floating pins cause false alarms.
- Tune `SERVO_LOCK_US` / `SERVO_OPEN_US` on the real lock.
- Check each sensor on the diagnostics screen (staff ID **1111**): `THERM` should be about 2000 at room temperature and drop when you pinch the thermistor. `LDR` should be high in the dark and drop in light.

## 2. Logins (keypad has digits 1–9, no 0)

| Staff | ID | PIN |
|---|---|---|
| Aina (nurse) | 1357 | 2468 |
| William (nurse) | 2468 | 1397 |
| Li Kai (nurse) | 3579 | 8642 |
| Admin (clears lockdown) | 9999 | 7531 |
| Diagnostics screen (raw ADC values) | 1111 | – |

- **SW_INT** = cancel / acknowledge.
- Drawer: key 1 = A (controlled drugs), key 2 = B (vaccines).
- **Supervisor (LPC1768):**
  - Joystick CENTRE, then tilt the secret code **Left, Left, Forward** (hold each for 0.2 s, back to flat between steps). The directions depend on how the board is held, so check page 2 of the LPC LCD (joystick right) to see what it detects.
  - DOWN = reject.
  - POT1 = tilt sensitivity, POT2 = tamper sensitivity.
- **Web app password:** `medvault`. Change it in `Pi_Gateway/config.json`.
- **Keypad numbering:** check which physical key gives which number. On our board the top-middle key is 1.

## 3. Build and flash

**NUC140:** open `NUC140_Cabinet/Keil/Proj_New.uvprojx` in Keil, Build, Download.
- Size is 29.9 KB of the 32 KB MDK-Lite limit, so keep additions small.
- Command line:

```bash
"%LOCALAPPDATA%\Keil_v5\UV4\UV4.exe" -r Keil\Proj_New.uvprojx -j0 -o build.log
```

**LPC1768:**
1. Run `C:\Users\TUF FA707RC-HX024W\Mbed Programs\build_medvault.cmd` (or build in Mbed Studio).
2. Copy `MedVault_Supervisor\BUILD\LPC1768\ARMC6\MedVault_Supervisor.bin` to the MBED drive and press reset.

Use the newer-firmware board (E:/COM9).

## 4. Raspberry Pi

```bash
sudo apt install -y python3-serial python3-opencv python3-requests
```

Copy `Pi_Gateway/` to `/home/pi/Pi_Gateway`, plug in the LPC1768 and the USB webcam, then:

```bash
python3 gateway.py
```

Open `http://<pi-ip>:8080` on a phone on the same WiFi. On the phone, use "Add to Home screen" to get the app icon.

- **Telegram alerts:**
  1. Create a bot with @BotFather and put its token in `config.json` as `telegram_token`.
  2. Send the bot a message, open `https://api.telegram.org/bot<token>/getUpdates` and copy `chat.id` into `telegram_chat_id`.
- **Start automatically on boot:**

```bash
sudo cp medvault.service /etc/systemd/system/ && sudo systemctl enable --now medvault
```

- **No boards?** Run `python3 gateway.py --sim` and use the buttons on the Control tab. This also works on the laptop with `py gateway.py --sim --no-camera`.
- **Replay attack demo:** use the Security tab or `python3 replay_attack.py`. It needs one real approval first.

## 5. Demo order (about 3 minutes)

1. **Normal access:** log in as 1357 / 2468 and pick drawer 1. On the LPC, press CENTRE and tilt L-L-F. The servo opens, the 7-seg counts down, then open and close the lid. The web app logs everything with a photo.
2. **Cold chain:** set the limit to just above room temperature (VR1 or the web app), then hold the thermistor. The 7-seg flashes, the alarm shows, and Telegram sends a message.
3. **Break-in:** open the lid while it's locked. You get LOCKDOWN, a photo and a Telegram alert. Clear it with admin 9999 / 7531 or the Clear button on the web.
4. **Cabinet moved:** shake the LPC board while idle. You get LOCKDOWN (reason: cabinet moved).
5. **Remote lockdown:** press the button on the phone.
6. **Hacker:** run the replay attack. The cabinet LCD shows ATTACK BLOCKED: REPLAY / NONCE.

## 6. Changes from the plan

- The web server uses Python's built-in server instead of Flask, so there's nothing extra to install on the Pi.
- The door sensor is the LDR alone. SW_INT is cancel / acknowledge.
- The servo pulse is made by Timer0 (20 ms frame) plus a Timer1 one-shot (pulse width), not hardware PWM. Timer2 was not needed: the countdowns use the Timer0 1 ms tick.
- Keys 7–9 are ignored while the box is bright (door open), because keypad column 3 shares GPA5 with the LDR. No typing is needed while the door is open.
