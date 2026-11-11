#!/usr/bin/env python3
"""
MedVault security test : replay attack (run on the Pi while gateway.py is running)

An attacker taps the wire between the supervisor unit and the cabinet, records a
real "APPROVE" message, and sends it again later hoping the door opens.
MedVault must reject it: the message is XTEA encrypted and carries the random
challenge of an old request plus an old counter.

  python3 replay_attack.py                 replay the last recorded approval
  python3 replay_attack.py --tamper        flip one bit (forged message)
  python3 replay_attack.py --wait 5        wait 5 s first (e.g. while a new request is open)
"""
import argparse
import json
import time
import urllib.request
from http.cookiejar import CookieJar
from urllib.parse import urlencode

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://127.0.0.1:8080")
ap.add_argument("--password", default="medvault")
ap.add_argument("--tamper", action="store_true")
ap.add_argument("--wait", type=int, default=3)
a = ap.parse_args()

web = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def call(path, body=None):
	data = None if body is None else json.dumps(body).encode()
	req = urllib.request.Request(a.url + path, data=data, headers={"Content-Type": "application/json"})
	return json.loads(web.open(req, timeout=10).read())


web.open(a.url + "/login", urlencode({"password": a.password}).encode(), timeout=10)
before = call("/api/state")["attacks_blocked"]

raw = [r for r in call("/api/raw") if r["dir"] == "S2N" and r["hex"].startswith("A54108")]
if not raw:
	raise SystemExit("No approval recorded yet. Do one normal access first (nurse login + supervisor approve).")
print(f"[hacker] recorded approval from {raw[0]['ts']}:")
print("         " + raw[0]["hex"])
for i in range(a.wait, 0, -1):
	print(f"[hacker] replaying in {i} ...", end="\r")
	time.sleep(1)

r = call("/api/replay_test", {"tamper": a.tamper})
print(f"[hacker] sent {'FORGED' if a.tamper else 'REPLAYED'} frame: {r['sent']}      ")

for _ in range(10):
	time.sleep(0.5)
	s = call("/api/state")
	if s["attacks_blocked"] > before:
		ev = [e for e in call("/api/events?limit=5") if e["name"] == "ATTACK"][0]
		print(f"[MedVault] ATTACK BLOCKED : {ev['detail']}")
		print("[MedVault] cabinet stayed LOCKED" if not s["unlocked"] else "[MedVault] WARNING: cabinet is unlocked!")
		break
else:
	print("[MedVault] no answer from the cabinet (is the link up?)")
