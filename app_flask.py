from flask import Flask, jsonify, request, send_from_directory
from solar_core import MPPBTariffConfig, MPPBBillingEngine
from pathlib import Path

app = Flask(__name__, static_folder=".")

@app.get("/")
def index():
    return send_from_directory(Path(__file__).parent, "index.html")

@app.get("/api/connections")
def connections():
    category = request.args.get("category", "HV_3.1")
    if category not in MPPBTariffConfig.CATEGORIES:
        return jsonify({"error":"Invalid category"}), 400
    return jsonify({"connections": MPPBTariffConfig.CATEGORIES[category]})

@app.get("/api/settings")
def get_settings():
    return jsonify({
        "HV_3.1_11kV_tariff": MPPBTariffConfig.TARIFF[("HV_3.1","11kV")],
        "HV_3.1_33kV_tariff": MPPBTariffConfig.TARIFF[("HV_3.1","33kV")],
        "HV_3.2_11kV_tariff": MPPBTariffConfig.TARIFF[("HV_3.2","11kV")],
        "HV_3.2_33kV_tariff": MPPBTariffConfig.TARIFF[("HV_3.2","33kV")],
        "LV_4.1_Rural_tariff": MPPBTariffConfig.TARIFF[("LV_4.1","Rural")],
        "LV_4.1_Urban_tariff": MPPBTariffConfig.TARIFF[("LV_4.1","Urban")],
        "HV_3.1_11kV_fixed": MPPBTariffConfig.FIXED_CHARGE_RATE[("HV_3.1","11kV")],
        "HV_3.1_33kV_fixed": MPPBTariffConfig.FIXED_CHARGE_RATE[("HV_3.1","33kV")],
        "HV_3.2_11kV_fixed": MPPBTariffConfig.FIXED_CHARGE_RATE[("HV_3.2","11kV")],
        "HV_3.2_33kV_fixed": MPPBTariffConfig.FIXED_CHARGE_RATE[("HV_3.2","33kV")],
        "LV_4.1_Rural_fixed": MPPBTariffConfig.FIXED_CHARGE_RATE[("LV_4.1","Rural")],
        "LV_4.1_Urban_fixed": MPPBTariffConfig.FIXED_CHARGE_RATE[("LV_4.1","Urban")],
        "wheeling_11kV": MPPBTariffConfig.WHEELING["11kV"],
        "wheeling_33kV": MPPBTariffConfig.WHEELING["33kV"],
        "transmission": MPPBTariffConfig.TRANSMISSION,
        "css": MPPBTariffConfig.CSS,
        "additional_surcharge": MPPBTariffConfig.ADDITIONAL_SURCHARGE,
        "fppas_pct": MPPBTariffConfig.FPPAS_PCT,
        "electricity_duty_pct": MPPBTariffConfig.ELECTRICITY_DUTY_PCT
    })

@app.post("/api/settings")
def save_settings():
    d = request.get_json(force=True)
    tariff_map = {
        "HV_3.1_11kV_tariff":("HV_3.1","11kV"), "HV_3.1_33kV_tariff":("HV_3.1","33kV"),
        "HV_3.2_11kV_tariff":("HV_3.2","11kV"), "HV_3.2_33kV_tariff":("HV_3.2","33kV"),
        "LV_4.1_Rural_tariff":("LV_4.1","Rural"), "LV_4.1_Urban_tariff":("LV_4.1","Urban")}
    fixed_map = {
        "HV_3.1_11kV_fixed":("HV_3.1","11kV"), "HV_3.1_33kV_fixed":("HV_3.1","33kV"),
        "HV_3.2_11kV_fixed":("HV_3.2","11kV"), "HV_3.2_33kV_fixed":("HV_3.2","33kV"),
        "LV_4.1_Rural_fixed":("LV_4.1","Rural"), "LV_4.1_Urban_fixed":("LV_4.1","Urban")}
    for k,pair in tariff_map.items():
        if k in d: MPPBTariffConfig.set_tariff(*pair,float(d[k]))
    for k,pair in fixed_map.items():
        if k in d: MPPBTariffConfig.set_fixed_charge_rate(*pair,float(d[k]))
    for k,attr in [("wheeling_11kV",None),("wheeling_33kV",None)]:
        if k in d: MPPBTariffConfig.WHEELING["11kV" if "11" in k else "33kV"]=float(d[k])
    for k,attr in [("transmission","TRANSMISSION"),("css","CSS"),("additional_surcharge","ADDITIONAL_SURCHARGE"),("fppas_pct","FPPAS_PCT")]:
        if k in d: setattr(MPPBTariffConfig,attr,float(d[k]))
    if "electricity_duty_pct" in d: MPPBTariffConfig.set_electricity_duty_pct(float(d["electricity_duty_pct"]))
    return jsonify({"message":"Settings saved"})

@app.post("/api/calculate-billing")
def calculate_billing():
    try:
        d=request.get_json(force=True)
        category=d["category"]; connection=d["connection"]
        demand=float(d["contract_demand"]); solar_units=float(d["solar_units"])
        gov_units=float(d["gov_units"]); extra=float(d.get("extra_charges",0))
        ppa=float(d["ppa_tariff"]); duty=float(d["duty_pct"])
        fppas=float(d["fppas_pct"]); captive=bool(d["is_captive"])

        # Government bill: peak/government units only.
        government = MPPBBillingEngine.mppb_bill(
            gov_units, category, connection, demand, duty, fppas, extra)

        # OG bill: all required units treated as MPPB/government supply.
        og = MPPBBillingEngine.mppb_bill(
            solar_units + gov_units, category, connection, demand, duty, fppas, extra)

        # Solar bill: energy uses LANDING PRICE; FPPAS never applies to solar.
        solar = MPPBBillingEngine.solar_bill(
            solar_units, ppa, "33" if connection=="33kV" else "11" if connection=="11kV" else connection,
            captive, duty, extra)

        final = solar["total_solar_bill"] + government["total_mppb_bill"]
        saving = og["total_mppb_bill"] - final
        saving_pct = saving / og["total_mppb_bill"] * 100 if og["total_mppb_bill"] else 0

        return jsonify({"solar":solar,"government":government,"og":og,
                        "final_customer_bill":final,"saving":saving,
                        "saving_pct":saving_pct})
    except Exception as e:
        return jsonify({"error":str(e)}),400

if __name__=="__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
