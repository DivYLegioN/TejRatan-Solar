"""
Solar OA / Group Captive Simulator -- Local GUI (Flask version)
Run: python app_flask.py
Opens on http://localhost:5000 (or http://127.0.0.1:5000)

Why Flask instead of Streamlit: Flask + its dependencies (Werkzeug, Jinja2,
itsdangerous, click, MarkupSafe) are pure-Python -- no C/C++ build step, so
pip install never fails on a phone. Streamlit pulls in pyarrow, which is a
huge C++ library that cannot be compiled on Android.

Backend logic is 100% the same solar_core.py, untouched.
"""

import io
import contextlib
from flask import Flask, request, redirect, url_for

from solar_core import (
    Tariffs, MONTHS, SUPPORTED_CAPACITIES_KW,
    get_monthly_generation, get_max_customers,
    get_allocation_limit,
    PPAPartner, SolarPlant,
)

app = Flask(__name__)

# ---------------------------------------------------------------------------
# In-memory settings (single local user, no DB needed). All future-changing
# values live here -- nothing hardcoded in solar_core.py itself.
# ---------------------------------------------------------------------------
SETTINGS = {
    "ppa_rate": Tariffs.PPA_RATE,
    "gov_ppa_rate": Tariffs.GOV_PPA_RATE,
    "gov_og_rate": Tariffs.GOV_OG_RATE,
    "mpeb_deduction_pct": Tariffs.MPEB_DEDUCTION * 100,
    "bank_withdrawal_pct": Tariffs.BANK_WITHDRAWAL_FACTOR * 100,
    "bank_settlement_rate": Tariffs.BANK_SETTLEMENT_RATE,
    "solchr_33_captive": Tariffs.SOLCHR_33KV_CAPTIVE,
    "solchr_11_captive": Tariffs.SOLCHR_11KV_CAPTIVE,
    "solchr_33_normal": Tariffs.SOLCHR_33KV_NORMAL,
    "solchr_11_normal": Tariffs.SOLCHR_11KV_NORMAL,
    "peak_rate_33": Tariffs.PEAK_RATE_33KV,
    "peak_rate_11": Tariffs.PEAK_RATE_11KV,
    "captive_min_pct": Tariffs.CAPTIVE_SHARE_MIN_PCT,
    "captive_max_pct": Tariffs.CAPTIVE_SHARE_MAX_PCT,
    "project_cost": 14000000.0,
    "equity_pct": 20.0,
    "emi": 160626.0,
    "om": 35000.0,
    # Default values pre-filled in each new customer form (Step 2)
    "default_units": 10000.0,
    "default_kv": 11,
    "default_fixed_charge": 50000.0,
    "default_captive_pct": 0.0,
    "default_peak_units": 1000.0,
    # New: project-wide Group Captive equity stake, configurable 26-49%
    "total_captive_pct": 26.0,
}


def push_tariffs_to_backend():
    """Send current Settings values into the unmodified backend's Tariffs
    class. No formula changes -- just assignment."""
    Tariffs.configure(
        PPA_RATE=SETTINGS["ppa_rate"],
        GOV_PPA_RATE=SETTINGS["gov_ppa_rate"],
        GOV_OG_RATE=SETTINGS["gov_og_rate"],
        MPEB_DEDUCTION=SETTINGS["mpeb_deduction_pct"] / 100,
        BANK_WITHDRAWAL_FACTOR=SETTINGS["bank_withdrawal_pct"] / 100,
        BANK_SETTLEMENT_RATE=SETTINGS["bank_settlement_rate"],
        SOLCHR_33KV_CAPTIVE=SETTINGS["solchr_33_captive"],
        SOLCHR_11KV_CAPTIVE=SETTINGS["solchr_11_captive"],
        SOLCHR_33KV_NORMAL=SETTINGS["solchr_33_normal"],
        SOLCHR_11KV_NORMAL=SETTINGS["solchr_11_normal"],
        PEAK_RATE_33KV=SETTINGS["peak_rate_33"],
        PEAK_RATE_11KV=SETTINGS["peak_rate_11"],
        CAPTIVE_SHARE_MIN_PCT=SETTINGS["captive_min_pct"],
        CAPTIVE_SHARE_MAX_PCT=SETTINGS["captive_max_pct"],
    )


# ---------------------------------------------------------------------------
# Shared page chrome (mobile-friendly, no external CSS/JS -- fully offline)
# ---------------------------------------------------------------------------
BASE_CSS = """
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, Roboto, Arial, sans-serif; background:#f4f6f8;
         margin:0; padding:0 0 40px 0; color:#1a1a1a; }
  header { background:#0f7b3f; color:#fff; padding:16px 20px; }
  header h1 { margin:0; font-size:1.25rem; }
  nav { display:flex; gap:10px; padding:10px 20px; background:#0c5e30; }
  nav a { color:#fff; text-decoration:none; font-weight:600; font-size:.95rem;
          padding:6px 10px; border-radius:6px; }
  nav a:hover { background:#0f7b3f; }
  .wrap { max-width:640px; margin:16px auto; padding:0 14px; }
  .card { background:#fff; border-radius:10px; padding:16px; margin-bottom:14px;
          box-shadow:0 1px 3px rgba(0,0,0,.08); }
  .card h2 { margin-top:0; font-size:1.05rem; color:#0c5e30; }
  label { display:block; font-size:.85rem; color:#444; margin:10px 0 4px; }
  input[type=number], input[type=text], select {
      width:100%; padding:10px; font-size:1rem; border:1px solid #ccd; border-radius:8px;
  }
  .row2 { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  button, .btn { background:#0f7b3f; color:#fff; border:none; padding:12px 16px;
       border-radius:8px; font-size:1rem; font-weight:600; width:100%; margin-top:14px;
       cursor:pointer; text-align:center; display:block; text-decoration:none; }
  button:active, .btn:active { background:#0c5e30; }
  table { width:100%; border-collapse:collapse; font-size:.85rem; margin-top:8px; }
  th, td { text-align:left; padding:6px 8px; border-bottom:1px solid #eee; }
  th { color:#666; font-weight:600; }
  .metric { display:inline-block; background:#eef7f1; border-radius:8px; padding:10px 14px;
            margin:4px 6px 4px 0; min-width:130px; }
  .metric .v { font-size:1.15rem; font-weight:700; color:#0c5e30; }
  .metric .l { font-size:.75rem; color:#666; }
  .flash { background:#e3f6e8; border:1px solid #0f7b3f; padding:10px 14px; border-radius:8px;
           margin-bottom:12px; font-size:.9rem; }
  details { background:#fafafa; border-radius:8px; padding:8px 12px; margin-top:10px; }
  summary { cursor:pointer; font-weight:600; color:#0c5e30; }
  .customer-block { border:1px solid #e0e0e0; border-radius:8px; padding:12px; margin-top:10px; }
  pre { white-space:pre-wrap; font-size:.72rem; background:#0c0c0c; color:#c9f7c9;
        padding:10px; border-radius:8px; overflow-x:auto; }
</style>
"""


def page(title, body, flash=None):
    flash_html = f'<div class="flash">{flash}</div>' if flash else ""
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>{BASE_CSS}</head>
<body>
<header><h1>&#9728;&#65039; Solar OA / Group Captive Simulator</h1></header>
<nav>
  <a href="{url_for('simulate_step1')}">&#127981; Simulator</a>
  <a href="{url_for('settings')}">&#9881;&#65039; Settings</a>
</nav>
<div class="wrap">{flash_html}{body}</div>
</body></html>"""


def num_field(name, label, value, step="any"):
    return f"""<label>{label}</label>
<input type="number" step="{step}" name="{name}" value="{value}" required>"""


# ===========================================================================
# SETTINGS PAGE
# ===========================================================================
@app.route("/settings", methods=["GET", "POST"])
def settings():
    flash = None
    if request.method == "POST":
        for k in SETTINGS:
            SETTINGS[k] = float(request.form[k])
        push_tariffs_to_backend()
        flash = "Settings saved and pushed to calculator."

    s = SETTINGS
    body = f"""
    <form method="post">
      <div class="card"><h2>Core Rates (Rs / unit)</h2>
        <div class="row2">
          {num_field('ppa_rate', 'PPA Rate (customer to plant)', s['ppa_rate'])}
          {num_field('gov_ppa_rate', 'Government PPA Rate', s['gov_ppa_rate'])}
        </div>
        <div class="row2">
          {num_field('gov_og_rate', 'Government OG Rate (full grid tariff)', s['gov_og_rate'])}
          {num_field('bank_settlement_rate', 'Bank Settlement Rate', s['bank_settlement_rate'])}
        </div>
      </div>

      <div class="card"><h2>Losses &amp; Banking</h2>
        <div class="row2">
          {num_field('mpeb_deduction_pct', 'MPEB / Wheeling Deduction (%)', s['mpeb_deduction_pct'])}
          {num_field('bank_withdrawal_pct', 'Bank Withdrawal Factor (%)', s['bank_withdrawal_pct'])}
        </div>
      </div>

      <div class="card"><h2>Solar Charge (Rs / unit)</h2>
        <div class="row2">
          {num_field('solchr_33_captive', '33 kV - Group Captive', s['solchr_33_captive'])}
          {num_field('solchr_11_captive', '11 kV - Group Captive', s['solchr_11_captive'])}
        </div>
        <div class="row2">
          {num_field('solchr_33_normal', '33 kV - Third Party', s['solchr_33_normal'])}
          {num_field('solchr_11_normal', '11 kV - Third Party', s['solchr_11_normal'])}
        </div>
      </div>

      <div class="card"><h2>Peak-Hour Rate (5PM-10PM, Rs/unit)</h2>
        <div class="row2">
          {num_field('peak_rate_33', '33 kV Peak Rate', s['peak_rate_33'])}
          {num_field('peak_rate_11', '11 kV Peak Rate', s['peak_rate_11'])}
        </div>
      </div>

      <div class="card"><h2>Group Captive Eligibility (% equity)</h2>
        <div class="row2">
          {num_field('captive_min_pct', 'Minimum %', s['captive_min_pct'], step="1")}
          {num_field('captive_max_pct', 'Maximum %', s['captive_max_pct'], step="1")}
        </div>
      </div>

      <div class="card"><h2>Project Financials</h2>
        <div class="row2">
          {num_field('project_cost', 'Project Cost (Rs)', s['project_cost'])}
          {num_field('emi', 'Monthly EMI (Rs)', s['emi'])}
        </div>
        <div class="row2">
          {num_field('equity_pct', 'Equity %', s['equity_pct'], step="1")}
          {num_field('om', 'Monthly O&amp;M (Rs)', s['om'])}
        </div>
      </div>

      <div class="card"><h2>Default Customer Form Values</h2>
        <p style="font-size:.8rem;color:#666;margin-top:-6px;">Pre-filled when you add a new customer in Step 2 -- still editable per customer there.</p>
        <div class="row2">
          {num_field('default_units', 'Monthly Required Units', s['default_units'])}
          {num_field('default_kv', 'Voltage Level (11 or 33)', s['default_kv'], step="1")}
        </div>
        <div class="row2">
          {num_field('default_fixed_charge', 'Fixed Charge (Rs)', s['default_fixed_charge'])}
          {num_field('default_captive_pct', 'Equity Shareholding %', s['default_captive_pct'], step="1")}
        </div>
        {num_field('default_peak_units', 'Peak Hour Units (5PM-10PM)', s['default_peak_units'])}
      </div>

      <div class="card"><h2>Group Captive Equity Stake</h2>
        <p style="font-size:.8rem;color:#666;margin-top:-6px;">Project-wide captive ownership stake (regulatory band: 26%-49%). Split across customers by their unit ratio; whatever real customers don't claim goes to the Mock Customer automatically.</p>
        <label>Total Captive Stake %</label>
        <input type="number" step="1" min="26" max="49" name="total_captive_pct" value="{s['total_captive_pct']}" required>
      </div>

      <button type="submit">Save Settings</button>
    </form>
    """
    return page("Settings", body, flash)


# ===========================================================================
# SIMULATOR -- STEP 1: capacity + number of customers
# ===========================================================================
@app.route("/", methods=["GET"])
@app.route("/simulate", methods=["GET"])
def simulate_step1():
    options = "".join(
        f'<option value="{c}">{c} kW &mdash; allocation limit {get_allocation_limit(c):,.0f} units/month</option>'
        for c in SUPPORTED_CAPACITIES_KW
    )
    body = f"""
    <div class="card">
      <h2>Step 1: Plant &amp; Customers</h2>
      <form method="get" action="{url_for('simulate_step2')}">
        <label>Plant Capacity</label>
        <select name="capacity">{options}</select>
        <label>Number of Real Customers</label>
        <input type="number" name="n" min="1" value="1" required>
        <p style="font-size:.8rem;color:#666;">If your real customers together need less than the plant's monthly allocation limit, a "Mock Customer / Unallocated Capacity" is auto-added for the remaining units -- it's calculated fully (bills, revenue, P&amp;L, captive funding) but is never a real PPA customer.</p>
        <button type="submit">Next: Enter Customer Details</button>
      </form>
    </div>
    """
    return page("Simulator", body)


# ===========================================================================
# SIMULATOR -- STEP 2: per-customer form
# ===========================================================================
@app.route("/simulate/customers", methods=["GET"])
def simulate_step2():
    capacity_kw = int(request.args.get("capacity"))
    n = int(request.args.get("n"))
    max_customers = get_max_customers(capacity_kw)
    if n > max_customers:
        n = max_customers
    if n < 1:
        n = 1

    blocks = []
    default_kv = int(SETTINGS["default_kv"])
    for i in range(n):
        kv_options = "".join(
            f'<option value="{v}"{" selected" if v == default_kv else ""}>{v} kV</option>'
            for v in (11, 33)
        )
        blocks.append(f"""
        <div class="customer-block">
          <h2>Customer {i+1}</h2>
          <label>Name</label>
          <input type="text" name="name_{i}" value="Customer {chr(65+i)}" required>
          <div class="row2">
            <div>{num_field(f'units_{i}', 'Monthly Required Units', SETTINGS['default_units'])}</div>
            <div><label>Voltage Level</label>
              <select name="kv_{i}">{kv_options}</select>
            </div>
          </div>
          <div class="row2">
            {num_field(f'fc_{i}', 'Fixed Charge (Rs)', SETTINGS['default_fixed_charge'])}
            {num_field(f'cap_{i}', 'Equity Shareholding % (0 if none)', SETTINGS['default_captive_pct'])}
          </div>
          {num_field(f'peak_{i}', 'Peak Hour Units (5PM-10PM)', SETTINGS['default_peak_units'])}
        </div>
        """)

    body = f"""
    <div class="card">
      <h2>Step 2: Customer Details -- {capacity_kw} kW plant, {n} customer(s)</h2>
      <p style="font-size:.85rem;color:#666;">Max real customers for this capacity: {max_customers} &nbsp;|&nbsp;
      Plant monthly allocation limit: {get_allocation_limit(capacity_kw):,.0f} units (Mock Customer fills any unclaimed units automatically)</p>
      <form method="post" action="{url_for('run_simulation')}">
        <input type="hidden" name="capacity" value="{capacity_kw}">
        <input type="hidden" name="n" value="{n}">
        {''.join(blocks)}
        <button type="submit">&#9654;&#65039; Run Simulation</button>
      </form>
    </div>
    """
    return page("Customer Details", body)


# ===========================================================================
# RUN SIMULATION + RESULTS
# ===========================================================================
@app.route("/simulate/run", methods=["POST"])
def run_simulation():
    push_tariffs_to_backend()

    capacity_kw = int(request.form["capacity"])
    n = int(request.form["n"])
    generation_data = get_monthly_generation(capacity_kw)

    plant = SolarPlant(
        project_cost=SETTINGS["project_cost"],
        equity_pct=SETTINGS["equity_pct"] / 100,
        emi=SETTINGS["emi"],
        om=SETTINGS["om"],
        months=MONTHS,
        generation=generation_data,
        capacity_kw=capacity_kw,
    )

    for i in range(n):
        name = request.form[f"name_{i}"]
        units = float(request.form[f"units_{i}"])
        kv = int(request.form[f"kv_{i}"])
        fixed_charge = float(request.form[f"fc_{i}"])
        captive_pct = float(request.form[f"cap_{i}"])
        peak = float(request.form[f"peak_{i}"])
        ptype = 26 if Tariffs.CAPTIVE_SHARE_MIN_PCT <= captive_pct <= Tariffs.CAPTIVE_SHARE_MAX_PCT else 50
        plant.add_partner(PPAPartner(name, units, kv, ptype,
                                      fixed_charge=fixed_charge,
                                      captive_share_pct=captive_pct,
                                      peak_units=peak))

    real_total_units = plant.total_contracted_units()

    # NEW: auto-fill remaining plant allocation with a clearly labelled
    # Mock Customer, if real customers claim less than the fixed monthly
    # allocation limit for this capacity. Does not touch run()/billing --
    # the mock is just another PPAPartner appended before run() executes.
    mock_partner = plant.add_mock_customer_if_needed()

    total_captive_pct = SETTINGS["total_captive_pct"]

    log_buffer = io.StringIO()
    with contextlib.redirect_stdout(log_buffer):
        plant.run()
        annual_summary = plant.print_annual_summary()
        project_summary = plant.print_project_summary()
    log_text = log_buffer.getvalue()

    # NEW: read-only Group Captive equity-stake / funding report, computed
    # on top of the already-finished run -- does not alter any bill,
    # revenue, or P&L figure above.
    captive_funding = plant.compute_captive_funding(total_captive_pct)

    ps = project_summary
    payback = "Never" if ps["payback_years"] == float("inf") else f"{ps['payback_years']:.1f} yrs"

    allocation_note = ""
    if mock_partner:
        allocation_note = f"""
        <div class="flash">
          Real customers claimed {real_total_units:,.0f} of {get_allocation_limit(capacity_kw):,.0f} units/month.
          <b>Mock Customer / Unallocated Capacity</b> auto-added for the remaining {mock_partner.monthly_units:,.0f} units so total allocation exactly matches plant capacity.
        </div>
        """
    else:
        allocation_note = f"""
        <div class="flash">
          Real customers already claim {real_total_units:,.0f} units/month (limit {get_allocation_limit(capacity_kw):,.0f}) -- no Mock Customer needed.
        </div>
        """

    metrics = f"""
    <div class="metric"><div class="v">Rs {ps['total_revenue']:,.0f}</div><div class="l">Total Revenue</div></div>
    <div class="metric"><div class="v">Rs {ps['total_profit']:,.0f}</div><div class="l">Total Profit</div></div>
    <div class="metric"><div class="v">{ps['roi_pct']:.1f}%</div><div class="l">ROI</div></div>
    <div class="metric"><div class="v">{ps['plant_utilization_pct']:.1f}%</div><div class="l">Plant Utilization</div></div>
    <div class="metric"><div class="v">{payback}</div><div class="l">Payback Period</div></div>
    <div class="metric"><div class="v">{ps['customers_added']}/{ps['max_customers']}</div><div class="l">Customers</div></div>
    """

    project_table_rows = [
        ("Plant Capacity (kW)", ps["capacity_kw"]),
        ("Total Generation (units)", f"{ps['total_generation']:.1f}"),
        ("Total Banked Units", f"{ps['total_banked_units']:.1f}"),
        ("Total Gov Units", f"{ps['total_gov_units']:.1f}"),
        ("Total EMI (yr)", f"Rs {ps['total_emi']:,.0f}"),
        ("Total O&M (yr)", f"Rs {ps['total_om']:,.0f}"),
        ("Total Expenses (yr)", f"Rs {ps['total_expense']:,.0f}"),
        ("Bank Units Lapsed (31-Mar)", f"{ps['bank_lapsed_units']:.1f}"),
        ("Govt Payout for Lapsed Bank", f"Rs {ps['bank_lapsed_payout']:,.0f}"),
    ]
    project_table = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in project_table_rows)

    customer_sections = []
    for name, a in annual_summary.items():
        cf = captive_funding.get(name, {})
        is_mock = cf.get("is_mock", False)
        display_name = f"&#9888;&#65039; {name}" if is_mock else name

        info_rows = [
            ("Annual Solar Units", f"{a['solar_units']:.1f}"),
            ("Annual Gov Units", f"{a['gov_units']:.1f}"),
            ("Annual Revenue", f"Rs {a['revenue']:,.0f}"),
            ("Annual Profit", f"Rs {a['profit']:,.0f}"),
            ("Avg. Saving vs Grid", f"{a['avg_saving_pct']:.1f}%"),
            ("Expense Share (yr)", f"Rs {a['expense_share']:,.0f}"),
            ("Fixed Charge (per bill)", f"Rs {a['fixed_charge']:,.0f}"),
            ("Group Captive? (billing rate)", "Yes" if a["is_captive"] else "No"),
        ]
        if a["is_captive"]:
            info_rows.append(("Captive Equity Value (billing-linked)", f"Rs {a['captive_equity_value']:,.0f}"))
        info_table = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in info_rows)

        captive_table = ""
        if cf:
            captive_rows = [
                ("Unit Ratio (share of plant allocation)", f"{cf['unit_ratio_pct']:.2f}%"),
                (f"Captive Stake Share (of {total_captive_pct:.0f}% total)", f"{cf['captive_equity_pct']:.3f}%"),
                ("Captive Funding / Ownership Amount", f"Rs {cf['captive_funding_amount']:,.0f}"),
            ]
            captive_table = "<table>" + "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in captive_rows) + "</table>"

        month_rows = "".join(
            f"<tr><td>{r['month']}</td><td>{r['solar_units']:.0f}</td><td>{r['gov_units']:.0f}</td>"
            f"<td>{r['revenue']:.0f}</td><td>{r['solbill']:.0f}</td><td>{r['govbill']:.0f}</td>"
            f"<td>{r['ogbill']:.0f}</td><td>{r['saving']*100:.1f}%</td></tr>"
            for r in a["monthly_records"]
        )

        mock_tag = '<p style="font-size:.75rem;color:#b45309;margin:-6px 0 8px;">Not a real PPA customer -- auto-generated to absorb unclaimed plant allocation.</p>' if is_mock else ""

        customer_sections.append(f"""
        <div class="card" style="{'border:1.5px dashed #b45309;' if is_mock else ''}">
          <h2>{display_name}</h2>
          {mock_tag}
          <table>{info_table}</table>
          <details open>
            <summary>Group Captive Equity Stake (new)</summary>
            {captive_table}
          </details>
          <details>
            <summary>Monthly breakdown</summary>
            <table>
              <tr><th>Month</th><th>Solar</th><th>Gov</th><th>Rev</th>
                  <th>SolBill</th><th>GovBill</th><th>GridBill</th><th>Saving</th></tr>
              {month_rows}
            </table>
          </details>
        </div>
        """)

    body = f"""
    {allocation_note}
    <div class="card">
      <h2>&#128202; Overall Project Summary</h2>
      {metrics}
      <details>
        <summary>Full Project Numbers</summary>
        <table>{project_table}</table>
      </details>
    </div>

    <h2 style="margin:18px 4px 4px;">&#128101; Per-Customer Summary</h2>
    {''.join(customer_sections)}

    <div class="card">
      <details>
        <summary>&#128421;&#65039; Raw Console Log (original script output)</summary>
        <pre>{log_text}</pre>
      </details>
    </div>

    <a class="btn" href="{url_for('simulate_step1')}">&#8634; Run Another Simulation</a>
    """
    return page("Results", body)


if __name__ == "__main__":
    # 0.0.0.0 so it's also reachable from other devices on same Wi-Fi if wanted;
    # still fully local -- nothing leaves your network, no internet needed.
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
