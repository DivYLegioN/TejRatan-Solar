# Solar OA / Group Captive Simulator — Local GUI

Your original calc logic (`solar_core.py`) untouched — every formula, loop,
condition, banking rule stays exact. Only change: rates/charges no longer
`input()`-locked, now live edit via Settings page, auto-fed into backend.

## Setup (one time)

```bash
pip install -r requirements.txt
```

Only dependency is Flask (pure Python, no C build — installs cleanly on
phone via Termux/Pydroid, unlike Streamlit which needs pyarrow).

## Run

```bash
python app_flask.py
```

Opens `http://localhost:5000` (or `http://127.0.0.1:5000`) in browser. Pure
local — no cloud, no server upload, no domain, no cost.

## Phone access (same Wi-Fi, optional)

1. Find phone/PC's local IP (Termux: `ip a`, Windows: `ipconfig`).
2. On another device's browser: `http://<that-IP>:5000`.
3. Both devices must be on same Wi-Fi. Not required if you're only using it
   on the one device running the server.

## Files

- `solar_core.py` — your calc engine, formulas untouched.
- `app_flask.py` — GUI: Settings page (all rates/charges editable) + Simulator (capacity, customers, run, results), all server-rendered HTML, no JS framework needed.
- `requirements.txt` — flask only.

## What's editable in Settings (no more hardcoding)

PPA rate, Gov PPA/OG rate, MPEB deduction %, bank withdrawal %, bank
settlement rate, solar charges (33/11kV, captive/normal), peak-hour rates,
captive % eligibility range, project cost, equity %, EMI, O&M. Per-customer
fixed charge stays where your original script had it — asked once per
customer, now via form field instead of `input()`.
