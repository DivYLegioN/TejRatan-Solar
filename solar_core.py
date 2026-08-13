"""
=============================================================================
 MULTI-CUSTOMER SOLAR OPEN ACCESS / GROUP CAPTIVE FINANCIAL SIMULATOR (v2)
 -- CORE BACKEND (GUI-ready) --

 This file is your original calculator. ONLY two kinds of change made,
 both non-logic:

   1. Tariffs.PPA_RATE / Tariffs.BANK_SETTLEMENT_RATE no longer call
      input() at import time (that would freeze a GUI app). They are now
      plain class attrs with placeholder defaults, set via
      Tariffs.configure(...) from the Settings page. Every formula that
      USES these values is 100% unchanged.

   2. collect_capacity()/collect_partners_interactively()/main() (pure
      input()-driven console glue) removed -- replaced by GUI code in
      app.py. print_annual_summary() / print_project_summary() keep their
      original print() lines untouched AND now also return a dict of the
      exact same computed values (same expressions, same order) so the
      GUI can render them without re-deriving any math.

 No formula, condition, loop, or business rule inside Tariffs, PPAPartner,
 BankingEngine, BillingEngine, or SolarPlant.run() was touched.
=============================================================================
"""

from typing import List, Dict


# ---------------------------------------------------------------------------
# 1. TARIFF / CONSTANT TABLE  (all rupee rates & rules live here)
# ---------------------------------------------------------------------------
class Tariffs:
    PPA_RATE = 4.50                 # Rs/unit paid by customers to the solar plant (placeholder -- set in Settings)
    GOV_PPA_RATE = 8.4              # Rs/unit -- grid/utility tariff
    GOV_OG_RATE = 10.58             # Rs/unit -- grid/utility tariff including all charges (peak-hour, fixed charges etc.)
    MPEB_DEDUCTION = 0.032           # 3.2% transmission/wheeling loss on gross generation
    BANK_WITHDRAWAL_FACTOR = 0.92    # only 92% of surplus generation is bankable
    BANK_SETTLEMENT_RATE = 3.50     # Rs/unit paid out for units left in bank at year end (placeholder -- set in Settings)

    # Per-unit open-access / cross-subsidy "solar charge" -- depends on
    # voltage level AND whether the customer is Group Captive or Third Party.
    SOLCHR_33KV_CAPTIVE = 0.63
    SOLCHR_11KV_CAPTIVE = 1.15
    SOLCHR_33KV_NORMAL = 3.30
    SOLCHR_11KV_NORMAL = 3.82

    # Peak-hour (5 PM - 10 PM) billing rate, Rs/unit, by voltage level
    PEAK_RATE_33KV = 8.4
    PEAK_RATE_11KV = 7.5

    # Partner "type" codes <= this value are treated as Group Captive (26%+ rule)
    CAPTIVE_SHARE_MIN_PCT = 26
    CAPTIVE_SHARE_MAX_PCT = 49

    @classmethod
    def configure(cls, **kwargs):
        """Set any Tariffs attribute from the Settings page. Pure assignment,
        no formula here -- every rate below is used unchanged wherever the
        original script used it."""
        for k, v in kwargs.items():
            if hasattr(cls, k):
                setattr(cls, k, v)


# ---------------------------------------------------------------------------
# 1B. GSA GENERATION PROFILES -- avg. daily generation (kWh/day) per month,
#     extracted from the 3 Global Solar Atlas reports (Madhya Pradesh site)
# ---------------------------------------------------------------------------
MONTHS = ["Apr", "May", "Jun", "Jul", "Aug", "Sep",
          "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]

DAYS_IN_MONTH = [30, 31, 30, 31, 31, 30, 31, 30, 31, 31, 28, 31]
# capacity (kW) -> average DAILY generation (kWh/day) for each month, Jan..Dec
GSA_DAILY_GENERATION = {
    500: [2588, 2369, 1866, 1344, 1431, 1930,
          2416, 2364, 2334, 2396, 2654, 2715],

    1000: [5176, 4739, 3733, 2687, 2862, 3860,
           4833, 4728, 4667, 4792, 5307, 5429],

    1500: [7791, 7153, 5634, 4047, 4268, 5834,
           7317, 7109, 6984, 7200, 7974, 8152],
}

SUPPORTED_CAPACITIES_KW = sorted(GSA_DAILY_GENERATION.keys())

# ---------------------------------------------------------------------------
# 1C. PLANT-WISE MONTHLY SOLAR ALLOCATION LIMITS (new, additive)
#     -- total contracted units a plant is allowed to allocate per month,
#        regardless of how many real customers actually sign up.
# ---------------------------------------------------------------------------
ALLOCATION_LIMIT_UNITS = {
    500: 66000,
    1000: 126000,
    1500: 192000,
}


def get_allocation_limit(capacity_kw: int) -> float:
    """Fixed monthly allocation limit (units) for a plant capacity."""
    if capacity_kw not in ALLOCATION_LIMIT_UNITS:
        raise ValueError(
            f"No allocation limit defined for {capacity_kw} kW. "
            f"Supported: {sorted(ALLOCATION_LIMIT_UNITS.keys())}"
        )
    return ALLOCATION_LIMIT_UNITS[capacity_kw]



def get_monthly_generation(capacity_kw: int) -> List[float]:
    """
    Monthly generation (units) = avg daily generation (kWh/day) x days in
    that month, for the chosen plant capacity, using the GSA report data.
    """
    if capacity_kw not in GSA_DAILY_GENERATION:
        raise ValueError(
            f"No GSA data available for {capacity_kw} kW. "
            f"Supported capacities: {SUPPORTED_CAPACITIES_KW}"
        )
    daily = GSA_DAILY_GENERATION[capacity_kw]
    return [round(daily[i] * DAYS_IN_MONTH[i], 1) for i in range(12)]


def get_max_customers(capacity_kw: int) -> int:
    """
    Max customers allowed for a plant = capacity_kW // 100, minimum 1.
    e.g. 500 kW -> 5, 1000 kW -> 10, 1500 kW -> 15.
    """
    return max(1, capacity_kw // 100)


# ---------------------------------------------------------------------------
# 2. PPA PARTNER (a single customer / off-taker)
# ---------------------------------------------------------------------------
class PPAPartner:
    def __init__(self, name: str, monthly_units: float, kv_level: int,
                 partner_type: int, fixed_charge: float,
                 captive_share_pct: float = 0, peak_units: float = 0,
                 category: str = "HV_3.1", connection: str = None,
                 contract_demand: float = 0.0):
        self.name = name
        self.monthly_units = monthly_units          # contracted units required / month
        self.kv_level = kv_level                    # 11 or 33
        self.partner_type = partner_type             # <=26 -> Group Captive, else Third Party
        self.fixed_charge = fixed_charge              # Rs, legacy per-customer fixed charge (kept for backward-compat, no longer used by BillingEngine)
        self.captive_share_pct = captive_share_pct    # % equity held (only used if captive)
        self.peak_units = peak_units                  # units consumed 5PM-10PM

        # NEW -- consumer category / connection / contract demand, used by
        # BillingEngine to look up MPPB tariff + fixed-charge rate from
        # MPPBTariffConfig (replaces the old SOLCHR/PEAK_RATE/GOV_PPA_RATE
        # constants). connection defaults to this partner's kv_level
        # ("11kV"/"33kV") unless explicitly overridden (needed for LV_4.1,
        # which uses "Rural"/"Urban" instead).
        self.category = category
        self.connection = connection if connection is not None else f"{kv_level}kV"
        self.contract_demand = contract_demand

        self.is_captive = (
    Tariffs.CAPTIVE_SHARE_MIN_PCT <= captive_share_pct <= Tariffs.CAPTIVE_SHARE_MAX_PCT
)

        self.is_mock = False   # new, additive flag -- True only for the auto-generated
                                 # "Mock Customer / Unallocated Capacity". Does not affect
                                 # any existing formula; PPAPartner behaves identically either way.

        self.monthly_records: List[Dict] = []
        self.annual = {
            "solar_units": 0.0, "gov_units": 0.0, "revenue": 0.0,
            "expense_share": 0.0, "profit": 0.0,
            "solbill": 0.0, "govbill": 0.0, "ogbill": 0.0,
        }

    def captive_equity_value(self, project_equity: float) -> float:
        """Rupee value of plant equity attributable to this captive partner."""
        if not self.is_captive:
            return 0.0
        return project_equity * (self.captive_share_pct / 100)

    def record_month(self, record: Dict):
        self.monthly_records.append(record)
        self.annual["solar_units"] += record["solar_units"]
        self.annual["gov_units"] += record["gov_units"]
        self.annual["revenue"] += record["revenue"]
        self.annual["expense_share"] += record["expense_share"]
        self.annual["profit"] += record["profit"]
        self.annual["solbill"] += record["solbill"]
        self.annual["govbill"] += record["govbill"]
        self.annual["ogbill"] += record["ogbill"]


# ---------------------------------------------------------------------------
# 3. BANKING ENGINE -- plant-level surplus/deficit banking (shared pool)
# ---------------------------------------------------------------------------
class BankingEngine:
    def __init__(self, opening_bank: float = 0):
        self.bank = opening_bank

    def process_month(self, net_generation: float, total_contracted_units: float):
        """
        Same rule as the original script, applied at whole-plant level:
          - if generation covers demand -> bank the surplus (at 92%)
          - else draw down the bank first, then request government units
        Returns: (total_solar_units_supplied, total_gov_units_needed, units_banked_this_month)
        """
        banked_this_month = 0.0
        gov_units = 0.0

        if net_generation >= total_contracted_units:
            surplus = net_generation - total_contracted_units
            banked_this_month = surplus * Tariffs.BANK_WITHDRAWAL_FACTOR
            self.bank += banked_this_month
            total_solar_supplied = total_contracted_units
        else:
            deficit = total_contracted_units - net_generation
            if self.bank >= deficit:
                self.bank -= deficit
                total_solar_supplied = total_contracted_units
            else:
                gov_units = deficit - self.bank
                self.bank = 0.0
                total_solar_supplied = total_contracted_units - gov_units

        self.bank = max(0.0, self.bank)   # <-- bank kabhi negative na jaaye, safety clamp
        return total_solar_supplied, gov_units, banked_this_month

# ---------------------------------------------------------------------------
# 4. BILLING ENGINE -- per-customer bill & saving calculations
# ---------------------------------------------------------------------------
class BillingEngine:
    """
    UPDATED: solar_bill / gov_bill / original_gov_bill now delegate to the
    new MPPB category-based formulas (MPPBBillingEngine + MPPBTariffConfig,
    section 6 below) instead of the old flat SOLCHR_*/PEAK_RATE_*/
    GOV_PPA_RATE/GOV_OG_RATE constants on Tariffs. Those old constants are
    left in place (unused) for backward compatibility, but no longer drive
    any calculation. Every other formula, loop, and rule in run() -- the
    banking split, EMI/O&M share, revenue, profit -- is untouched; only the
    per-customer bill amount computation changed.

    Signatures are unchanged (still take `partner` + a unit count and
    return a single float total) so SolarPlant.run() needed no changes.
    """

    @staticmethod
    def solar_bill(partner: PPAPartner, solar_units: float) -> float:
        """NEW: solar bill = MPPB landing-price formula (PPA + wheeling +
        transmission + CSS + additional surcharge, CSS/surcharge zeroed
        for captive), keyed by the partner's kv_level. No fixed charge and
        no FPPAS on the solar side, per spec."""
        result = MPPBBillingEngine.solar_bill(
            solar_units=solar_units,
            ppa_tariff=Tariffs.PPA_RATE,
            kv_level=partner.kv_level,
            is_captive=partner.is_captive,
        )
        return result["total_solar_bill"]

    @staticmethod
    def gov_bill(partner: PPAPartner, gov_units: float) -> float:
        """UPDATED: Government (peak-hour, 5PM-10PM) bill. Energy charges
        now bill only partner.peak_units (not full gov_units) at the
        category+connection government tariff. FPPAS + Electricity Duty
        key off these peak energy charges; Fixed Charge = contract_demand
        x fixed-charge rate; extra_charges defaults to 0 (pass via caller
        if needed). Delegates to GovOGSolarBillingEngine.government_bill()."""
        government_tariff = MPPBTariffConfig.get_tariff(partner.category, partner.connection)
        fixed_rate = MPPBTariffConfig.get_fixed_charge_rate(partner.category, partner.connection)
        result = GovOGSolarBillingEngine.government_bill(
            peak_units=partner.peak_units,
            government_tariff=government_tariff,
            contract_load=partner.contract_demand,
            mpeb_fixed_charge_rate=fixed_rate,
        )
        return result["total_government_bill"]

    @staticmethod
    def original_gov_bill(partner: PPAPartner) -> float:
        """What the customer would pay if 100% grid-supplied -- same NEW
        MPPB formula, applied to the partner's full monthly_units."""
        result = MPPBBillingEngine.mppb_bill(
            gov_units=partner.monthly_units,
            category=partner.category,
            connection=partner.connection,
            contract_demand=partner.contract_demand,
        )
        return result["total_mppb_bill"]

    @staticmethod
    def saving_pct(solbill: float, govbill: float, ogbill: float) -> float:
        if ogbill == 0:
            return 0.0
        return 1 - ((solbill + govbill) / ogbill)


# ---------------------------------------------------------------------------
# 5. SOLAR PLANT -- top-level orchestrator tying everything together
# ---------------------------------------------------------------------------
class SolarPlant:
    def __init__(self, project_cost: float, equity_pct: float, emi: float,
                 om: float, months: List[str], generation: List[float],
                 capacity_kw: int):
        self.project_cost = project_cost
        self.equity = project_cost * equity_pct
        self.emi = emi
        self.om = om
        self.monthly_expense = emi + om
        self.months = months            # e.g. ["Jan", "Feb", ...]
        self.generation = generation    # gross generation values, parallel to `months`
        self.capacity_kw = capacity_kw
        self.max_customers = get_max_customers(capacity_kw)

        self.partners: List[PPAPartner] = []
        self.banking = BankingEngine()

        self.annual = {
            "gross_gen": 0.0, "net_gen": 0.0, "solar_units": 0.0,
            "gov_units": 0.0, "revenue": 0.0, "banked_units": 0.0,
        }

    def add_partner(self, partner: PPAPartner):
        if len(self.partners) >= self.max_customers:
            print(
                f"  [!] Cannot add '{partner.name}': plant capacity "
                f"{self.capacity_kw} kW allows a maximum of "
                f"{self.max_customers} customer(s). Skipping this partner."
            )
            return
        self.partners.append(partner)

    def total_contracted_units(self) -> float:
        return sum(p.monthly_units for p in self.partners)

    # -----------------------------------------------------------------
    # NEW, ADDITIVE: auto-fill plant allocation with a Mock Customer.
    # Does not touch add_partner(), total_contracted_units(), run(), or
    # any billing/banking formula -- it only decides whether to append one
    # extra PPAPartner before run() is called.
    # -----------------------------------------------------------------
    def add_mock_customer_if_needed(self, kv_level: int = 33,
                                     fixed_charge: float = 0.0,
                                     peak_units: float = 0.0) -> "PPAPartner | None":
        """
        Compares the sum of REAL customers' monthly_units against this
        plant's fixed monthly allocation limit (get_allocation_limit).
        If real customers require less than the limit, auto-creates a
        clearly labelled 'Mock Customer / Unallocated Capacity' PPAPartner
        for exactly the remaining units, and appends it directly to
        self.partners (bypassing the max_customers cap -- the mock is not
        a real PPA customer and must never count against that limit or be
        confused with one).

        The mock customer then flows through run() / billing / P&L exactly
        like any other partner, because run() already loops over
        self.partners unconditionally -- no change needed there.

        Returns the created PPAPartner, or None if real customers already
        meet/exceed the allocation limit (nothing to fill).
        """
        limit = get_allocation_limit(self.capacity_kw)
        real_total = sum(p.monthly_units for p in self.partners if not p.is_mock)
        remaining = limit - real_total
        if remaining <= 0:
            return None

        mock = PPAPartner(
            name="Mock Customer / Unallocated Capacity",
            monthly_units=remaining,
            kv_level=kv_level,
            partner_type=50,
            fixed_charge=fixed_charge,
            captive_share_pct=0,
            peak_units=peak_units,
        )
        mock.is_mock = True
        self.partners.append(mock)   # deliberately bypasses add_partner()'s
                                       # max_customers cap -- mock is not real
        return mock

    # -----------------------------------------------------------------
    # NEW, ADDITIVE: Group-Captive equity-stake / funding report.
    # Purely a read-only calculation on top of already-computed values
    # (self.equity, partner.monthly_units, total_contracted_units()).
    # Does NOT alter is_captive, solar_bill, gov_bill, revenue, or any
    # other existing formula -- it is an extra report, not a replacement.
    # -----------------------------------------------------------------
    def compute_captive_funding(self, total_captive_pct: float) -> Dict[str, Dict]:
        """
        total_captive_pct: the PROJECT-WIDE Group Captive equity stake the
        user has chosen for this plant, configurable between 26% and 49%
        (per the Group Captive regulatory band).

        Every partner currently on the plant -- real customers AND the
        Mock Customer if one was added -- gets a slice of that stake
        proportional to their unit ratio:

            unit_ratio        = partner.monthly_units / total_contracted_units()
            captive_equity_pct = total_captive_pct * unit_ratio
            captive_funding    = self.equity * (captive_equity_pct / 100)

        Because every partner's unit_ratio together sums to 1 (mock fills
        exactly the remaining units), the captive_equity_pct slices sum to
        exactly total_captive_pct -- so whatever real customers don't
        claim automatically lands on the Mock Customer with no separate
        'leftover' step required.
        """
        total_con = self.total_contracted_units()
        out: Dict[str, Dict] = {}
        if total_con == 0:
            return out
        for p in self.partners:
            unit_ratio = p.monthly_units / total_con
            captive_equity_pct = total_captive_pct * unit_ratio
            captive_funding = self.equity * (captive_equity_pct / 100)
            out[p.name] = {
                "is_mock": p.is_mock,
                "monthly_units": p.monthly_units,
                "unit_ratio_pct": unit_ratio * 100,
                "captive_equity_pct": captive_equity_pct,
                "captive_funding_amount": captive_funding,
            }
        return out


    def run(self):
        total_con = self.total_contracted_units()
        if total_con == 0:
            raise ValueError("No PPA partners added, or total contracted units is zero.")

        for month, gross in zip(self.months, self.generation):
            # --- Step 1: MPEB (transmission) deduction ---
            net = gross * (1 - Tariffs.MPEB_DEDUCTION)

            # --- Step 2: plant-level banking decision (shared bank) ---
            total_solar, total_gov, banked = self.banking.process_month(net, total_con)

            month_revenue = total_solar * Tariffs.PPA_RATE
            month_PL = month_revenue - self.monthly_expense

            self.annual["gross_gen"] += gross
            self.annual["net_gen"] += net
            self.annual["solar_units"] += total_solar
            self.annual["gov_units"] += total_gov
            self.annual["revenue"] += month_revenue
            self.annual["banked_units"] += banked

            print(f"\n===== {month} =====")
            print(f"Gross={gross:.1f} | Net={net:.1f} | Bank={self.banking.bank:.1f} | "
                  f"PlantSolarUnits={total_solar:.1f} | PlantGovUnits={total_gov:.1f} | "
                  f"PlantRevenue={month_revenue:.1f} | PlantP&L={month_PL:.1f}")

            # --- Step 3: allocate to each customer, proportional to their share ---
            for partner in self.partners:
                share = partner.monthly_units / total_con

                gov_units_p = total_gov * share
                solar_units_p = partner.monthly_units - gov_units_p

                emi_share = self.emi * share
                om_share = self.om * share
                expense_share = emi_share + om_share

                revenue_p = solar_units_p * Tariffs.PPA_RATE
                profit_p = revenue_p - expense_share

                solbill = BillingEngine.solar_bill(partner, solar_units_p)
                govbill = BillingEngine.gov_bill(partner, gov_units_p)
                ogbill = BillingEngine.original_gov_bill(partner)
                saving = BillingEngine.saving_pct(solbill, govbill, ogbill)

                record = {
                    "month": month, "gross_gen": gross, "net_gen": net,
                    "bank": self.banking.bank, "solar_units": solar_units_p,
                    "gov_units": gov_units_p, "revenue": revenue_p,
                    "emi_share": emi_share, "om_share": om_share,
                    "expense_share": expense_share, "profit": profit_p,
                    "solbill": solbill, "govbill": govbill, "ogbill": ogbill,
                    "saving": saving,
                }
                partner.record_month(record)

                print(
                    f"  [{partner.name:<12}] Solar={solar_units_p:8.1f} | Gov={gov_units_p:7.1f} | "
                    f"Rev={revenue_p:9.1f} | EMIshr={emi_share:8.1f} | O&Mshr={om_share:7.1f} | "
                    f"ExpShr={expense_share:8.1f} | P&L={profit_p:9.1f} | "
                    f"SolBill={solbill:9.1f} | GovBill={govbill:8.1f} | OGBill={ogbill:8.1f} | "
                    f"Saving={saving * 100:5.1f}% |"
                )

    # -----------------------------------------------------------------
    def print_annual_summary(self) -> Dict[str, Dict]:
        """Same computation as v1, per customer. Now also returns the values
        as a dict (same expressions, same order) so a GUI can render them
        without recomputing any business rule."""
        print("\n\n################ ANNUAL SUMMARY (PER CUSTOMER) ################")
        out = {}
        for p in self.partners:
            a = p.annual
            avg_saving = (1 - (a["solbill"] + a["govbill"]) / a["ogbill"]) if a["ogbill"] else 0
            print(f"\n--- {p.name} ---")
            print(f"  Annual Solar Units  : {a['solar_units']:.1f}")
            print(f"  Annual Gov Units    : {a['gov_units']:.1f}")
            print(f"  Annual Revenue      : {a['revenue']:.1f}")
            print(f"  Annual Expense Share: {a['expense_share']:.1f}")
            print(f"  Annual Profit       : {a['profit']:.1f}")
            print(f"  Annual Saving       : {avg_saving * 100:.1f}%")
            print(f"  Fixed Charge (input): {p.fixed_charge:.1f}")
            captive_equity = None
            if p.is_captive:
                captive_equity = p.captive_equity_value(self.equity)
                print(f"  Captive Equity Value: {captive_equity:.1f}")

            out[p.name] = {
                "solar_units": a["solar_units"],
                "gov_units": a["gov_units"],
                "revenue": a["revenue"],
                "expense_share": a["expense_share"],
                "profit": a["profit"],
                "avg_saving_pct": avg_saving * 100,
                "fixed_charge": p.fixed_charge,
                "is_captive": p.is_captive,
                "captive_equity_value": captive_equity,
                "solbill_annual": a["solbill"],
                "govbill_annual": a["govbill"],
                "ogbill_annual": a["ogbill"],
                "monthly_records": p.monthly_records,
            }
        return out

    # -----------------------------------------------------------------
    def print_project_summary(self) -> Dict:
        """Same computation as v1, at plant level. Now also returns the
        values as a dict (same expressions, same order) for GUI use."""
        total_emi = self.emi * 12
        total_om = self.om * 12
        total_expense = total_emi + total_om
        bank_revenue = self.banking.bank * Tariffs.BANK_SETTLEMENT_RATE
        total_revenue = self.annual["revenue"] + bank_revenue
        total_profit = total_revenue - total_expense

        rated_capacity_basis = sum(self.generation) if self.generation else 0
        utilization = (self.annual["net_gen"] / rated_capacity_basis * 100) if rated_capacity_basis else 0
        roi = (total_profit / self.equity * 100) if self.equity else 0
        payback_years = (self.equity / total_profit) if total_profit > 0 else float("inf")

        print("\n\n################ OVERALL PROJECT SUMMARY ################")
        print(f"Plant Capacity     : {self.capacity_kw} kW")
        print(f"Max Customers Rule : {self.max_customers} (capacity_kW // 100, min 1)")
        print(f"Customers Added    : {len(self.partners)}")
        print(f"Total Generation   : {self.annual['gross_gen']:.1f}")
        print(f"Total Banked Units : {self.annual['banked_units']:.1f}")
        print(f"Total Gov Units    : {self.annual['gov_units']:.1f}")
        print(f"Total Revenue      : {total_revenue:.1f}")
        print(f"Total EMI          : {total_emi:.1f}")
        print(f"Total O&M          : {total_om:.1f}")
        print(f"Total Expenses     : {total_expense:.1f}")
        print(f"Total Profit       : {total_profit:.1f}")
        print(f"Plant Utilization  : {utilization:.1f}%")
        print(f"Banked Units Lapsed (31-Mar)      : {self.banking.bank:.1f}")
        print(f"Government Payout for Lapsed Bank : {bank_revenue:.1f}  (@ Rs.{Tariffs.BANK_SETTLEMENT_RATE}/unit)")
        print(f"ROI                : {roi:.1f}%")
        print(f"Payback Period     : {payback_years:.1f} years")

        return {
            "capacity_kw": self.capacity_kw,
            "max_customers": self.max_customers,
            "customers_added": len(self.partners),
            "total_generation": self.annual["gross_gen"],
            "total_banked_units": self.annual["banked_units"],
            "total_gov_units": self.annual["gov_units"],
            "total_revenue": total_revenue,
            "total_emi": total_emi,
            "total_om": total_om,
            "total_expense": total_expense,
            "total_profit": total_profit,
            "plant_utilization_pct": utilization,
            "bank_lapsed_units": self.banking.bank,
            "bank_lapsed_payout": bank_revenue,
            "roi_pct": roi,
            "payback_years": payback_years,
        }


# =============================================================================
# 6. MPPB/DISCOM TARIFF + NEW BILLING STRUCTURE (HV 3.1 / HV 3.2 / LV 4.1)
#    -- ADDITIVE ONLY --
#
#    Nothing above this line was touched. This section does not read from,
#    write to, or call Tariffs, PPAPartner, BankingEngine, BillingEngine,
#    or SolarPlant. It is a parallel, self-contained tariff/billing module
#    for the new MPPB/DISCOM consumer-category structure. Wire it into the
#    GUI/app layer wherever the new categories are needed; existing Solar/
#    PPA calculation flow (SolarPlant.run(), old BillingEngine, banking,
#    captive equity, P&L) is completely untouched and keeps working exactly
#    as before.
#
#    Captive/group-captive status is NOT recomputed here. Callers must pass
#    in the existing `PPAPartner.is_captive` value (driven by the existing
#    Tariffs.CAPTIVE_SHARE_MIN_PCT / CAPTIVE_SHARE_MAX_PCT / 26% rule), so
#    the "26% equity participation" logic lives in exactly one place.
# =============================================================================


class MPPBTariffConfig:
    """
    Centralized, editable configuration for the new MPPB/DISCOM consumer
    categories and tariff structure. All values below are DEFAULTS and are
    meant to be changed from a Settings page via the classmethods provided
    -- no formula in MPPBBillingEngine is hard-coded to these numbers.
    """

    # Consumer category -> allowed connection types
    CATEGORIES: Dict[str, List[str]] = {
        "HV_3.1": ["11kV", "33kV"],   # HV 3.1 -- Industrial
        "HV_3.2": ["11kV", "33kV"],   # HV 3.2 -- Non-Industrial
        "LV_4.1": ["Rural", "Urban"],  # LV 4.1
    }

    CATEGORY_LABELS = {
        "HV_3.1": "HV 3.1 - Industrial",
        "HV_3.2": "HV 3.2 - Non-Industrial",
        "LV_4.1": "LV 4.1",
    }

    # (category, connection) -> Rs/unit MPPB/DISCOM energy tariff
    TARIFF: Dict[tuple, float] = {
        ("HV_3.1", "11kV"): 6.34,
        ("HV_3.1", "33kV"): 6.14,
        ("HV_3.2", "11kV"): 6.70,
        ("HV_3.2", "33kV"): 6.50,
        ("LV_4.1", "Rural"): 7.05,
        ("LV_4.1", "Urban"): 7.15,
    }

    # (category, connection) -> Rs/kVA fixed-charge rate
    FIXED_CHARGE_RATE: Dict[tuple, float] = {
        ("HV_3.1", "11kV"): 430,
        ("HV_3.1", "33kV"): 389,
        ("HV_3.2", "11kV"): 510,
        ("HV_3.2", "33kV"): 480,
        ("LV_4.1", "Rural"): 245,
        ("LV_4.1", "Urban"): 320,
    }

    # Solar open-access charges (Rs/unit)
    WHEELING: Dict[str, float] = {"33kV": 0.17, "11kV": 0.69}
    TRANSMISSION: float = 0.46
    CSS: float = 1.49
    ADDITIONAL_SURCHARGE: float = 1.18

    # Electricity Duty: selectable, capped at 12%
    ELECTRICITY_DUTY_OPTIONS = (9, 10, 11, 12)
    ELECTRICITY_DUTY_PCT: float = 9

    # FPPAS -- applies only to MPPB/government units, never to solar
    FPPAS_PCT: float = 4.5

    # -----------------------------------------------------------------
    # Validation helpers (also used directly by callers/UI layers)
    # -----------------------------------------------------------------
    @classmethod
    def validate_category_connection(cls, category: str, connection: str):
        if category not in cls.CATEGORIES:
            raise ValueError(
                f"Invalid consumer category '{category}'. "
                f"Valid categories: {list(cls.CATEGORIES.keys())}"
            )
        if connection not in cls.CATEGORIES[category]:
            raise ValueError(
                f"Invalid connection '{connection}' for category '{category}'. "
                f"Valid connections: {cls.CATEGORIES[category]}"
            )

    @classmethod
    def validate_electricity_duty(cls, duty_pct: float):
        if duty_pct > 12:
            raise ValueError("Electricity Duty cannot exceed 12%.")
        if duty_pct not in cls.ELECTRICITY_DUTY_OPTIONS:
            raise ValueError(
                f"Electricity Duty must be one of {cls.ELECTRICITY_DUTY_OPTIONS} percent."
            )

    # -----------------------------------------------------------------
    # Getters -- 11kV/33kV and Rural/Urban automatically select the
    # correct rate because they are looked up by (category, connection).
    # -----------------------------------------------------------------
    @classmethod
    def get_tariff(cls, category: str, connection: str) -> float:
        cls.validate_category_connection(category, connection)
        return cls.TARIFF[(category, connection)]

    @classmethod
    def get_fixed_charge_rate(cls, category: str, connection: str) -> float:
        cls.validate_category_connection(category, connection)
        return cls.FIXED_CHARGE_RATE[(category, connection)]

    @classmethod
    def get_wheeling_rate(cls, kv_level) -> float:
        key = f"{kv_level}kV" if not isinstance(kv_level, str) else kv_level
        if key not in cls.WHEELING:
            raise ValueError(
                f"Invalid kV level for wheeling charge: '{kv_level}'. "
                f"Valid: {list(cls.WHEELING.keys())}"
            )
        return cls.WHEELING[key]

    # -----------------------------------------------------------------
    # Configuration setters -- values editable from a Settings page,
    # without touching any calculation logic below.
    # -----------------------------------------------------------------
    @classmethod
    def set_tariff(cls, category: str, connection: str, value: float):
        cls.validate_category_connection(category, connection)
        cls.TARIFF[(category, connection)] = value

    @classmethod
    def set_fixed_charge_rate(cls, category: str, connection: str, value: float):
        cls.validate_category_connection(category, connection)
        cls.FIXED_CHARGE_RATE[(category, connection)] = value

    @classmethod
    def set_electricity_duty_pct(cls, value: float):
        cls.validate_electricity_duty(value)
        cls.ELECTRICITY_DUTY_PCT = value

    @classmethod
    def configure(cls, **kwargs):
        """Bulk-set any plain (non-dict) attribute, e.g. FPPAS_PCT, TRANSMISSION,
        CSS, ADDITIONAL_SURCHARGE, ELECTRICITY_DUTY_PCT. For TARIFF /
        FIXED_CHARGE_RATE / WHEELING use the dedicated setters above so
        validation runs."""
        for k, v in kwargs.items():
            if hasattr(cls, k) and not isinstance(getattr(cls, k), dict):
                setattr(cls, k, v)


class MPPBBillingEngine:
    """
    New tariff & billing structure for HV 3.1 / HV 3.2 / LV 4.1 consumer
    categories. Solar and Government/MPEB calculations are kept completely
    independent, per spec, and are only added together in combined_bill().
    """

    # =================== GOVERNMENT / MPPB SIDE ===================
    @staticmethod
    def mppb_energy_charges(gov_units: float, mppb_tariff: float) -> float:
        """MPEB Energy Charges = Government Units x MPPB Tariff"""
        return gov_units * mppb_tariff

    @staticmethod
    def fppas(mppb_energy_charges: float, fppas_pct: float = None) -> float:
        """FPPAS = MPEB Energy Charges x FPPAS %  (government units only)"""
        pct = MPPBTariffConfig.FPPAS_PCT if fppas_pct is None else fppas_pct
        return mppb_energy_charges * (pct / 100)

    @staticmethod
    def mppb_electricity_duty(gov_units: float, mppb_tariff: float,
                               duty_pct: float = None) -> float:
        """MPEB Electricity Duty = Government Units x MPPB Tariff x Duty %"""
        pct = MPPBTariffConfig.ELECTRICITY_DUTY_PCT if duty_pct is None else duty_pct
        MPPBTariffConfig.validate_electricity_duty(pct)
        return gov_units * mppb_tariff * (pct / 100)

    @staticmethod
    def fixed_charge(contract_demand: float, fixed_charge_rate: float) -> float:
        """Fixed Charge = Contract Demand x Applicable Fixed Charge Rate"""
        return contract_demand * fixed_charge_rate

    @staticmethod
    def mppb_bill(gov_units: float, category: str, connection: str,
                  contract_demand: float, duty_pct: float = None,
                  fppas_pct: float = None, other_charges: float = 0.0) -> Dict:
        """Independent MPPB/Government bill. Contract Demand stays a
        customer-level input, passed in by the caller."""
        MPPBTariffConfig.validate_category_connection(category, connection)
        mppb_tariff = MPPBTariffConfig.get_tariff(category, connection)
        fixed_rate = MPPBTariffConfig.get_fixed_charge_rate(category, connection)

        energy_charges = MPPBBillingEngine.mppb_energy_charges(gov_units, mppb_tariff)
        fppas_amt = MPPBBillingEngine.fppas(energy_charges, fppas_pct)
        duty_amt = MPPBBillingEngine.mppb_electricity_duty(gov_units, mppb_tariff, duty_pct)
        fixed_amt = MPPBBillingEngine.fixed_charge(contract_demand, fixed_rate)

        total = energy_charges + fppas_amt + duty_amt + fixed_amt + other_charges

        return {
            "category": category,
            "connection": connection,
            "gov_units": gov_units,
            "mppb_tariff": mppb_tariff,
            "mppb_energy_charges": energy_charges,
            "fppas": fppas_amt,
            "mppb_electricity_duty": duty_amt,
            "contract_demand": contract_demand,
            "fixed_charge_rate": fixed_rate,
            "fixed_charge": fixed_amt,
            "other_charges": other_charges,
            "total_mppb_bill": total,
        }

    # =================== SOLAR / OPEN ACCESS SIDE ===================
    @staticmethod
    def open_access_charges(kv_level, is_captive: bool) -> Dict[str, float]:
        """
        Solar open-access charges. If the customer is eligible captive/
        group-captive (existing 26% equity rule, passed in via is_captive),
        CSS and Additional Surcharge become zero.
        """
        wheeling = MPPBTariffConfig.get_wheeling_rate(kv_level)
        transmission = MPPBTariffConfig.TRANSMISSION
        if is_captive:
            css = 0.0
            additional_surcharge = 0.0
        else:
            css = MPPBTariffConfig.CSS
            additional_surcharge = MPPBTariffConfig.ADDITIONAL_SURCHARGE
        return {
            "wheeling": wheeling,
            "transmission": transmission,
            "css": css,
            "additional_surcharge": additional_surcharge,
        }

    @staticmethod
    def landing_tariff(ppa_tariff: float, kv_level, is_captive: bool) -> float:
        """
        Landing Tariff = PPA Tariff + Wheeling + Transmission + CSS + Additional Surcharge
        For an eligible captive/group-captive customer, CSS and Additional
        Surcharge are zero, so this collapses to:
        Landing Tariff = PPA Tariff + Wheeling + Transmission
        """
        oa = MPPBBillingEngine.open_access_charges(kv_level, is_captive)
        return ppa_tariff + oa["wheeling"] + oa["transmission"] + oa["css"] + oa["additional_surcharge"]

    @staticmethod
    def solar_electricity_duty(solar_units: float, ppa_tariff: float,
                                duty_pct: float = None) -> float:
        """Solar Electricity Duty = Solar Units x PPA Tariff x Duty %"""
        pct = MPPBTariffConfig.ELECTRICITY_DUTY_PCT if duty_pct is None else duty_pct
        MPPBTariffConfig.validate_electricity_duty(pct)
        return solar_units * ppa_tariff * (pct / 100)

    @staticmethod
    def solar_bill(solar_units: float, ppa_tariff: float, kv_level, is_captive: bool,
                    duty_pct: float = None, other_charges: float = 0.0) -> Dict:
        """
        Independent solar bill. FPPAS is always zero here -- it never
        applies to solar/PPA units, per spec.
        """
        oa = MPPBBillingEngine.open_access_charges(kv_level, is_captive)

        ppa_energy_cost = solar_units * ppa_tariff
        wheeling_amt = solar_units * oa["wheeling"]
        transmission_amt = solar_units * oa["transmission"]
        css_amt = solar_units * oa["css"]
        additional_surcharge_amt = solar_units * oa["additional_surcharge"]
        duty_amt = MPPBBillingEngine.solar_electricity_duty(solar_units, ppa_tariff, duty_pct)
        solar_fppas = 0.0,  # FPPAS must NOT apply to solar/PPA units
        contract_demand=partner.contract_demand,   # NEW
        fixed_charge_rate=fixed_rate               # NEW

        total = (ppa_energy_cost + wheeling_amt + transmission_amt + css_amt
                 + additional_surcharge_amt + duty_amt + solar_fppas+ fixed_charge_rate + other_charges)

        return {
            "solar_units": solar_units,
            "ppa_tariff": ppa_tariff,
            "ppa_energy_cost": ppa_energy_cost,
            "wheeling": wheeling_amt,
            "transmission": transmission_amt,
            "css": css_amt,
            "additional_surcharge": additional_surcharge_amt,
            "solar_electricity_duty": duty_amt,
            "fppas": solar_fppas,
            "Fixes_Charges": fixed_charge_rate,
            "other_charges": other_charges,
            "landing_tariff_per_unit": MPPBBillingEngine.landing_tariff(ppa_tariff, kv_level, is_captive),
            "is_captive": is_captive,
            "total_solar_bill": total,
        }

    # =================== COMBINED ===================
    @staticmethod
    def combined_bill(solar_bill: Dict, mppb_bill: Dict) -> Dict:
        """Total Customer Bill = Solar Bill + MPPB/Government Bill.
        No charge is double-counted: each input dict is independently
        computed and simply summed here."""
        total = solar_bill["total_solar_bill"] + mppb_bill["total_mppb_bill"]
        return {
            "solar_bill_total": solar_bill["total_solar_bill"],
            "mppb_bill_total": mppb_bill["total_mppb_bill"],
            "total_customer_bill": total,
        }

    @staticmethod
    def customer_saving(original_mppb_bill: float, final_combined_bill: float) -> Dict:
        """
        Saving = Original MPPB/Government Bill - (Solar/Open Access Bill + Remaining MPPB/Government Bill)
        Saving % = Saving / Original Bill x 100
        """
        saving = original_mppb_bill - final_combined_bill
        saving_pct = (saving / original_mppb_bill * 100) if original_mppb_bill else 0.0
        return {
            "original_mppb_bill": original_mppb_bill,
            "final_combined_bill": final_combined_bill,
            "saving": saving,
            "saving_pct": saving_pct,
        }


# =============================================================================
# 7. GOVERNMENT / OG / SOLAR BILLING -- NEW FORMULA SET (ADDITIVE ONLY)
#    Peak hours = 5 PM - 10 PM. Uses MPPBTariffConfig for FPPAS %,
#    Electricity Duty %, and MPEB fixed-charge rate ("as mentioned in
#    setting"). Fully parallel/self-contained -- does not touch
#    MPPBBillingEngine, Tariffs, PPAPartner, BillingEngine, or SolarPlant.
# =============================================================================
class GovOGSolarBillingEngine:
    """
    government_bill()  -> peak-hour (5PM-10PM) units only, at govt tariff
    og_bill()           -> (solar units + govt peak units) x govt tariff
    solar_bill()        -> units x Landing Price (PPA+Wheeling+Transmission+CSS+Addl Surcharge)
    FPPAS applies to government_bill/og_bill only, never to solar_bill.
    """

    # ---------- shared pieces ----------
    @staticmethod
    def fixed_charge(contract_load: float, mpeb_fixed_charge_rate: float) -> float:
        """Fixed Charge = Contract Load x MPEB Fixed Charge Rate"""
        return contract_load * mpeb_fixed_charge_rate

    @staticmethod
    def fppas(energy_charges: float, fppas_pct: float = None) -> float:
        """FPPAS = Energy Charges x FPPAS %"""
        pct = MPPBTariffConfig.FPPAS_PCT if fppas_pct is None else fppas_pct
        return energy_charges * (pct / 100)

    @staticmethod
    def electricity_duty(energy_charges: float, duty_pct: float = None) -> float:
        """Electricity Duty = Energy Charges x Electricity Duty %"""
        pct = MPPBTariffConfig.ELECTRICITY_DUTY_PCT if duty_pct is None else duty_pct
        MPPBTariffConfig.validate_electricity_duty(pct)
        return energy_charges * (pct / 100)

    # ---------- 1. GOVERNMENT BILL (peak-hour units only) ----------
    @staticmethod
    def government_bill(peak_units: float, government_tariff: float,
                         contract_load: float = 0.0, mpeb_fixed_charge_rate: float = 0.0,
                         fppas_pct: float = None, duty_pct: float = None,
                         extra_charges: float = 0.0) -> Dict:
        """Peak-hour (5PM-10PM) Government bill. No Fixed Charge here --
        fixed charge is billed once on the OG/MPPB side, never repeated on
        the peak-hour-only government bill."""
        energy_charges = peak_units * government_tariff
        fppas_amt = GovOGSolarBillingEngine.fppas(energy_charges, fppas_pct)
        duty_amt = GovOGSolarBillingEngine.electricity_duty(energy_charges, duty_pct)
        total = energy_charges + fppas_amt + duty_amt + extra_charges
        return {
            "peak_units": peak_units, "government_tariff": government_tariff,
            "energy_charges": energy_charges, "fixed_charge": 0.0,
            "fppas": fppas_amt, "electricity_duty": duty_amt,
            "extra_charges": extra_charges, "total_government_bill": total,
        }

    # ---------- 2. OG BILL (solar units + government peak units) ----------
    @staticmethod
    def og_bill(solar_units: float, gov_peak_units: float, government_tariff: float,
                contract_load: float, mpeb_fixed_charge_rate: float,
                fppas_pct: float = None, duty_pct: float = None,
                extra_charges: float = 0.0) -> Dict:
        units_utilised = solar_units + gov_peak_units
        energy_charges = units_utilised * government_tariff
        fixed_amt = GovOGSolarBillingEngine.fixed_charge(contract_load, mpeb_fixed_charge_rate)
        fppas_amt = GovOGSolarBillingEngine.fppas(energy_charges, fppas_pct)
        duty_amt = GovOGSolarBillingEngine.electricity_duty(energy_charges, duty_pct)
        total = energy_charges + fixed_amt + fppas_amt + duty_amt + extra_charges
        return {
            "solar_units": solar_units, "gov_peak_units": gov_peak_units,
            "units_utilised": units_utilised, "government_tariff": government_tariff,
            "energy_charges": energy_charges, "fixed_charge": fixed_amt,
            "fppas": fppas_amt, "electricity_duty": duty_amt,
            "extra_charges": extra_charges, "total_og_bill": total,
        }

    # ---------- 3. SOLAR BILL (landing price, no FPPAS) ----------
    @staticmethod
    def landing_price(solar_ppa: float, wheeling: float, transmission: float,
                       css: float, additional_surcharge: float) -> float:
        """Landing Price = Solar PPA + Wheeling + Transmission + CSS + Additional Surcharge"""
        return solar_ppa + wheeling + transmission + css + additional_surcharge

    @staticmethod
    def solar_bill(units_utilised: float, solar_ppa: float, wheeling: float,
                    transmission: float, css: float, additional_surcharge: float,
                    contract_load: float, mpeb_fixed_charge_rate: float,
                    duty_pct: float = None, extra_charges: float = 0.0) -> Dict:
        landing = GovOGSolarBillingEngine.landing_price(
            solar_ppa, wheeling, transmission, css, additional_surcharge)
        energy_charges = units_utilised * landing
        fixed_amt = GovOGSolarBillingEngine.fixed_charge(contract_load, mpeb_fixed_charge_rate)
        duty_amt = GovOGSolarBillingEngine.electricity_duty(energy_charges, duty_pct)
        # FPPAS never applies to solar bill, per spec.
        total = energy_charges + fixed_amt + duty_amt + extra_charges
        return {
            "units_utilised": units_utilised, "landing_price": landing,
            "energy_charges": energy_charges, "fixed_charge": fixed_amt,
            "electricity_duty": duty_amt, "extra_charges": extra_charges,
            "total_solar_bill": total,
        }


# =============================================================================
# 8. TOTAL-PAYABLE-AFTER-SOLARIZATION, SAVINGS, CONSUMER FINANCIALS, EMI
#    -- ADDITIVE ONLY. Mirrors the bill_ledger.html frontend exactly.
#    Nothing above this line was touched.
# =============================================================================
class SolarizationSummaryEngine:
    """
    Combines GovOGSolarBillingEngine outputs into the two things the
    frontend shows: 'Total Payable After Solarization' and the
    before/after savings comparison (monthly + annual).
    """

    @staticmethod
    def total_payable_after_solarization(solar_bill: Dict, government_bill: Dict) -> Dict:
        """Total Payable After Solarization = Solar Bill + Government
        (peak-hour) Bill -- what the consumer actually pays each month
        once solarized."""
        solar_total = solar_bill["total_solar_bill"]
        gov_total = government_bill["total_government_bill"]
        total = solar_total + gov_total
        return {
            "solar_bill_total": solar_total,
            "government_bill_total": gov_total,
            "total_payable_after_solarization": total,
        }

    @staticmethod
    def savings(og_bill: Dict, after_solarization: Dict) -> Dict:
        """Before = OG Bill (baseline, pre-solarization). After = Total
        Payable After Solarization. Returns both monthly and annual (x12)
        savings in Rs and %."""
        og_total = og_bill["total_og_bill"]
        after_total = after_solarization["total_payable_after_solarization"]

        monthly_saving = og_total - after_total
        monthly_saving_pct = (monthly_saving / og_total * 100) if og_total else 0.0

        annual_og = og_total * 12
        annual_after = after_total * 12
        annual_saving = annual_og - annual_after
        annual_saving_pct = (annual_saving / annual_og * 100) if annual_og else 0.0

        return {
            "monthly_og_bill": og_total,
            "monthly_payable_after_solar": after_total,
            "monthly_saving": monthly_saving,
            "monthly_saving_pct": monthly_saving_pct,
            "annual_og_bill": annual_og,
            "annual_payable_after_solar": annual_after,
            "annual_saving": annual_saving,
            "annual_saving_pct": annual_saving_pct,
        }


class ConsumerFinancialsEngine:
    """
    Per-consumer Revenue / Expense / Profit -- Revenue = this consumer's
    solar units x PPA rate (what they pay the plant for solar energy).
    Expense = this consumer's EMI share + O&M share. Profit = Revenue -
    Expense. Both monthly and annual (x12) figures returned.
    """

    @staticmethod
    def revenue(solar_units: float, ppa_rate: float) -> float:
        """Revenue = Solar Units x PPA Rate"""
        return solar_units * ppa_rate

    @staticmethod
    def expense(emi_monthly: float, om_monthly: float) -> float:
        """Total Expense = EMI share + O&M share"""
        return emi_monthly + om_monthly

    @staticmethod
    def monthly_financials(solar_units: float, ppa_rate: float,
                            emi_monthly: float, om_monthly: float) -> Dict:
        revenue = ConsumerFinancialsEngine.revenue(solar_units, ppa_rate)
        expense = ConsumerFinancialsEngine.expense(emi_monthly, om_monthly)
        profit = revenue - expense
        return {
            "solar_units": solar_units, "ppa_rate": ppa_rate,
            "emi_monthly": emi_monthly, "om_monthly": om_monthly,
            "revenue_monthly": revenue, "expense_monthly": expense, "profit_monthly": profit,
            "revenue_annual": revenue * 12, "expense_annual": expense * 12, "profit_annual": profit * 12,
        }


class EMICalculator:
    """
    Standalone EMI calculator -- Project Cost + Tenure (years) + Annual
    Interest Rate % -> monthly EMI, total interest, total payment.
    Standard reducing-balance amortization formula:
        EMI = P x r x (1+r)^n / ((1+r)^n - 1)
    where r = monthly rate (annual_rate_pct / 12 / 100), n = tenure in months.
    If rate is 0, EMI = P / n (straight-line).
    """

    @staticmethod
    def calculate(project_cost: float, tenure_years: float, annual_rate_pct: float) -> Dict:
        n = max(1, round(tenure_years * 12))
        r = annual_rate_pct / 12 / 100

        if r == 0:
            emi = project_cost / n
        else:
            emi = project_cost * r * ((1 + r) ** n) / (((1 + r) ** n) - 1)

        total_payment = emi * n
        total_interest = total_payment - project_cost

        return {
            "project_cost": project_cost,
            "tenure_years": tenure_years,
            "tenure_months": n,
            "annual_rate_pct": annual_rate_pct,
            "emi_monthly": emi,
            "total_interest": total_interest,
            "total_payment": total_payment,
        }
