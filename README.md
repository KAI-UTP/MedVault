# MedVault (group VaultX): how to build, wire and run

Chan Li Kai (22010900), William Wong Xiao Kang (22010943). RBB3023 Project.

| Folder | What |
|---|---|
| `Proposal/` | Proposal PDF (`make_proposal.py` rebuilds it) |
| `MedVault_Project_Plan.md` | Full design (pins, protocol, alarms, stages) |
| `NUC140_Cabinet/` | Cabinet controller: `main.c` + Keil project (`Keil/Proj_New.uvprojx`) |
| `LPC1768_Supervisor/` | Copy of the supervisor program + ready `.bin` (real program: `C:\Users\TUF FA707RC-HX024W\Mbed Programs\MedVault_Supervisor`) |
| `Pi_Gateway/` | Raspberry Pi gateway: web app, camera, Telegram, replay test |

**Status:** all code builds and the web app was tested in simulation mode. Nothing has been run on the real boards yet.

---

## 1. Wiring

```
NUC140 GPB1 (TX0) ─────────► LPC1768 p10 (RX)
NUC140 GPB0 (RX0) ◄───────── LPC1768 p9  (TX)
NUC140 GND ─────────────────  LPC1768 GND          (both boards run at 3.3 V logic)

Raspberry Pi USB ──────────── LPC1768 USB (power + serial 115200)

Thermistor (NTC 10k):  3.3V ── 10k ──┬── GPA6 (ADC6)         LDR:  3.3V ── 10k ──┬── GPA5 (ADC5)
                                     └── NTC ── GND                              └── LDR ── GND
Servo SG90:  brown = GND, red = 5V (+100 µF across 5V/GND), orange = GPA15
```

- Put the LDR **inside the box** (dark when the lid is closed). The NUC140 measures the dark level at power-on, so **close the lid before powering on**.
- Not wired yet? Set `USE_LDR 0` / `USE_THERM 0` at the top of `NUC140_Cabinet/main.c`, otherwise floating pins cause false alarms.
- **Check GPA15 is free on your board** before wiring the servo. Tune `SERVO_LOCK_US` / `SERVO_OPEN_US` on the real lock.

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
