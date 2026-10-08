#!/usr/bin/env python3
"""Fictional microfinance loan book for the Fineract source environment.

Loads data through the Fineract REST API (never direct SQL), through the SSH tunnel.
All data is fictional: phone numbers use the unallocated 000 prefix, no BVNs are
generated, and account numbers are Fineract's own internal ones.

Bulk load (reproducible for the same --seed, --clients and --as-of on an empty DB):
    generate_data.py --clients 100
Live-write workload for Phase 3:
    generate_data.py --continuous --rate 6 --log logs/live-writes.jsonl

Credentials come from FINERACT_USER / FINERACT_PASSWORD; they are never written or printed.
"""

import argparse
import json
import os
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import requests
import urllib3
from faker import Faker

DEFAULT_BASE_URL = "https://localhost:8443/fineract-provider/api/v1"
LOCAL_HOSTS = {"localhost", "127.0.0.1"}
DATE_FMT = "%Y-%m-%d"
COMMON = {"locale": "en", "dateFormat": "yyyy-MM-dd"}

BRANCHES = [("IKJ", "Ikeja"), ("YAB", "Yaba"), ("SUR", "Surulere"), ("IBD", "Ibadan")]
OFFICERS_PER_BRANCH = 2

# shortName -> product definition. repaymentFrequencyType: 1 = weeks, 2 = months.
# interestType: 0 = declining balance, 1 = flat. Rates are per year (frequency type 3).
PRODUCTS = {
    "GML": dict(name="Group Microloan", repayments=16, every=1, freq_type=1, period_days=7,
                rate=30.0, interest_type=1, min_p=50_000, max_p=300_000, step=5_000),
    "SME": dict(name="SME Loan", repayments=12, every=1, freq_type=2, period_days=30,
                rate=24.0, interest_type=0, min_p=500_000, max_p=5_000_000, step=50_000),
}

# Client mix; the remainder get a single disbursed loan.
SHARE_NO_LOAN, SHARE_PIPELINE, SHARE_REPEAT = 0.05, 0.05, 0.25
# Repayment behaviour for disbursed loans.
PROFILES = [("on_time", 65), ("late", 15), ("partial", 10), ("default", 10)]


def d(s):
    return s.strftime(DATE_FMT)


# ---------------------------------------------------------------- API client

class Fineract:
    def __init__(self, base_url, tenant, user, password):
        host = urlparse(base_url).hostname
        if host not in LOCAL_HOSTS:
            sys.exit(f"Refusing to run against {host!r}: TLS verification is only disabled for localhost.")
        # Self-signed certificate on the tunnelled localhost endpoint only.
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.base = base_url.rstrip("/")
        self.s = requests.Session()
        self.s.verify = False
        self.s.auth = (user, password)
        self.s.headers.update({"Fineract-Platform-TenantId": tenant, "Content-Type": "application/json"})

    def call(self, method, path, body=None, **params):
        r = self.s.request(method, f"{self.base}/{path.lstrip('/')}", json=body, params=params or None, timeout=60)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:800]}")
        return r.json() if r.content else {}

    def get(self, path, **params):
        return self.call("GET", path, **params)

    def post(self, path, body, **params):
        return self.call("POST", path, {**COMMON, **body}, **params)

    def find_one(self, path, external_id):
        items = self.get(path, externalId=external_id).get("pageItems", [])
        return items[0] if items else None


# ---------------------------------------------------------------- plan (pure, seeded)

@dataclass
class LoanPlan:
    ext_id: str
    product: str
    principal: int
    submitted: date
    approved: date | None
    disbursed: date | None
    profile: str
    must_close: bool = False
    default_after: int = 0


@dataclass
class ClientPlan:
    ext_id: str
    first: str
    last: str
    mobile: str
    dob: date
    branch: str
    officer: int
    activated: date
    loans: list = field(default_factory=list)


def unique_mobile(rng, used):
    while True:
        m = "000" + f"{rng.randrange(10**8):08d}"
        if m not in used:
            used.add(m)
            return m


def pick_principal(rng, p):
    steps = (p["max_p"] - p["min_p"]) // p["step"]
    return p["min_p"] + rng.randint(0, steps) * p["step"]


def pick_profile(rng):
    names, weights = zip(*PROFILES)
    return rng.choices(names, weights)[0]


def build_plan(n_clients, seed, as_of):
    rng = random.Random(seed)
    fake = Faker("en_NG")
    fake.seed_instance(seed)
    used_mobiles, clients, loan_no = set(), [], 0

    def next_loan_id():
        nonlocal loan_no
        loan_no += 1
        return f"FMB-LN-{loan_no:06d}"

    def disbursed_loan(product, disb, profile, must_close=False):
        p = PRODUCTS[product]
        return LoanPlan(
            ext_id=next_loan_id(), product=product, principal=pick_principal(rng, p),
            submitted=disb - timedelta(days=rng.randint(3, 7)),
            approved=disb - timedelta(days=rng.randint(0, 2)), disbursed=disb,
            profile=profile, must_close=must_close,
            default_after=rng.randint(1, 4) if profile == "default" else 0)

    # Exact proportions, shuffled, so small runs keep the intended mix.
    counts = {k: round(n_clients * share) for k, share in
              (("none", SHARE_NO_LOAN), ("pipeline", SHARE_PIPELINE), ("repeat", SHARE_REPEAT))}
    kinds = [k for k, v in counts.items() for _ in range(v)]
    kinds += ["single"] * (n_clients - len(kinds))
    rng.shuffle(kinds)

    for i, kind in enumerate(kinds, start=1):
        branch = rng.choice(BRANCHES)[0]
        c = ClientPlan(
            ext_id=f"FMB-CL-{i:06d}", first=fake.first_name(), last=fake.last_name(),
            mobile=unique_mobile(rng, used_mobiles),
            dob=as_of - timedelta(days=rng.randint(21 * 365, 60 * 365)),
            branch=branch, officer=rng.randint(1, OFFICERS_PER_BRANCH), activated=as_of)
        if kind == "none":
            pass
        elif kind == "pipeline":
            sub = as_of - timedelta(days=rng.randint(1, 30))
            approved = sub + timedelta(days=rng.randint(0, 1)) if rng.random() < 0.5 else None
            c.loans.append(LoanPlan(next_loan_id(), "GML", pick_principal(rng, PRODUCTS["GML"]),
                                    sub, min(approved, as_of) if approved else None, None, "pipeline"))
        elif kind == "repeat":
            # First loan fully repaid and closed, second loan active.
            gml = PRODUCTS["GML"]
            first_disb = as_of - timedelta(days=rng.randint(200, 330))
            c.loans.append(disbursed_loan("GML", first_disb, "on_time", must_close=True))
            last_due = first_disb + timedelta(days=gml["repayments"] * gml["period_days"])
            product = "SME" if rng.random() < 0.25 else "GML"
            second = last_due + timedelta(days=rng.randint(7, 30))
            if second > as_of - timedelta(days=2 * PRODUCTS[product]["period_days"] + 1):
                product = "GML"
            second = min(second, as_of - timedelta(days=2 * PRODUCTS[product]["period_days"] + 1))
            c.loans.append(disbursed_loan(product, second, pick_profile(rng)))
        else:
            product = "SME" if rng.random() < 0.25 else "GML"
            latest = as_of - timedelta(days=2 * PRODUCTS[product]["period_days"] + 1)
            disb = latest - timedelta(days=rng.randint(0, 365 - (as_of - latest).days))
            c.loans.append(disbursed_loan(product, disb, pick_profile(rng)))
        if c.loans:
            c.activated = min(l.submitted for l in c.loans) - timedelta(days=rng.randint(7, 60))
        else:
            c.activated = as_of - timedelta(days=rng.randint(1, 365))
        clients.append(c)
    return clients, rng


def repayment_plan(rng, loan, periods, as_of):
    """Return [(date, amount)] for a disbursed loan, in date order, none after as_of."""
    out = []
    for n, (due, amount) in enumerate(periods, start=1):
        if loan.profile == "default" and n > loan.default_after:
            break
        if loan.profile == "late":
            when = due + timedelta(days=rng.randint(1, 30))
        else:
            when = due
        if loan.profile == "partial":
            amount = round(amount * rng.uniform(0.5, 0.9), 2)
        if when <= as_of:
            out.append((when, round(amount, 2)))
    return sorted(out, key=lambda x: x[0])


# ---------------------------------------------------------------- setup

def ensure_currency(api):
    selected = [c["code"] for c in api.get("currencies").get("selectedCurrencyOptions", [])]
    if "NGN" not in selected:
        api.call("PUT", "currencies", {"currencies": selected + ["NGN"]})
        print("currency  NGN enabled")


def ensure_offices(api, opening):
    existing = {o.get("externalId"): o["id"] for o in api.get("offices")}
    ids = {}
    for code, name in BRANCHES:
        ext = f"FMB-OF-{code}"
        if ext not in existing:
            existing[ext] = api.post("offices", {"name": f"{name} Branch", "parentId": 1,
                                                 "openingDate": d(opening), "externalId": ext})["resourceId"]
            print(f"office    {ext} created")
        ids[code] = existing[ext]
    return ids


def ensure_staff(api, office_ids, joined, fake):
    existing = {s.get("externalId"): s["id"] for s in api.get("staff", status="all")}
    ids = {}
    for code, office_id in office_ids.items():
        for n in range(1, OFFICERS_PER_BRANCH + 1):
            ext = f"FMB-ST-{code}-{n}"
            if ext not in existing:
                existing[ext] = api.post("staff", {
                    "officeId": office_id, "firstname": fake.first_name(), "lastname": fake.last_name(),
                    "isLoanOfficer": True, "isActive": True, "externalId": ext,
                    "joiningDate": d(joined)})["resourceId"]
                print(f"staff     {ext} created")
            ids[(code, n)] = existing[ext]
    return ids


def ensure_products(api):
    existing = {p["shortName"]: p["id"] for p in api.get("loanproducts")}
    for short, p in PRODUCTS.items():
        if short in existing:
            continue
        existing[short] = api.post("loanproducts", {
            "name": p["name"], "shortName": short, "externalId": f"FMB-LP-{short}",
            "currencyCode": "NGN", "digitsAfterDecimal": 2, "inMultiplesOf": 0,
            "principal": p["min_p"], "minPrincipal": p["min_p"], "maxPrincipal": p["max_p"],
            "numberOfRepayments": p["repayments"], "repaymentEvery": p["every"],
            "repaymentFrequencyType": p["freq_type"],
            "interestRatePerPeriod": p["rate"], "interestRateFrequencyType": 3,
            "amortizationType": 1, "interestType": p["interest_type"], "interestCalculationPeriodType": 1,
            "transactionProcessingStrategyCode": "mifos-standard-strategy",
            "daysInYearType": 1, "daysInMonthType": 1, "isInterestRecalculationEnabled": False,
            "accountingRule": 1})["resourceId"]
        print(f"product   {short} created")
    return existing


# ---------------------------------------------------------------- clients and loans

def create_client(api, c, office_id, staff_id):
    found = api.find_one("clients", c.ext_id)
    if found:
        return found["id"], False
    cid = api.post("clients", {
        "officeId": office_id, "staffId": staff_id, "legalFormId": 1,
        "firstname": c.first, "lastname": c.last, "mobileNo": c.mobile, "externalId": c.ext_id,
        "dateOfBirth": d(c.dob), "active": True, "activationDate": d(c.activated),
        "submittedOnDate": d(c.activated)})["clientId"]
    return cid, True


def loan_terms(product_id, p):
    return {
        "productId": product_id, "loanType": "individual",
        "loanTermFrequency": p["repayments"] * p["every"], "loanTermFrequencyType": p["freq_type"],
        "numberOfRepayments": p["repayments"], "repaymentEvery": p["every"],
        "repaymentFrequencyType": p["freq_type"], "interestRatePerPeriod": p["rate"],
        "amortizationType": 1, "interestType": p["interest_type"], "interestCalculationPeriodType": 1,
        "transactionProcessingStrategyCode": "mifos-standard-strategy"}


def schedule(api, loan_id):
    periods = api.get(f"loans/{loan_id}", associations="repaymentSchedule")["repaymentSchedule"]["periods"]
    return [(date(*p["dueDate"]), p["totalDueForPeriod"]) for p in periods if p.get("period")]


def repay(api, loan_id, when, amount):
    api.post(f"loans/{loan_id}/transactions",
             {"transactionDate": d(when), "transactionAmount": amount}, command="repayment")


def create_loan(api, rng, loan, client_id, officer_id, product_ids, as_of, stats):
    if api.find_one("loans", loan.ext_id):
        stats["loans_skipped"] += 1
        return
    p = PRODUCTS[loan.product]
    loan_id = api.post("loans", {
        **loan_terms(product_ids[loan.product], p), "clientId": client_id, "loanOfficerId": officer_id,
        "externalId": loan.ext_id, "principal": loan.principal,
        "submittedOnDate": d(loan.submitted),
        "expectedDisbursementDate": d(loan.disbursed or loan.approved or loan.submitted)})["loanId"]
    stats["loans_created"] += 1
    if loan.approved:
        api.post(f"loans/{loan_id}", {"approvedOnDate": d(loan.approved)}, command="approve")
    if not loan.disbursed:
        return
    api.post(f"loans/{loan_id}", {"actualDisbursementDate": d(loan.disbursed),
                                  "transactionAmount": loan.principal}, command="disburse")
    payments = repayment_plan(rng, loan, schedule(api, loan_id), as_of)
    for when, amount in payments:
        repay(api, loan_id, when, amount)
        stats["repayments"] += 1
    if loan.must_close:
        left = api.get(f"loans/{loan_id}")["summary"]["totalOutstanding"]
        if left > 0:
            repay(api, loan_id, payments[-1][0], left)
            stats["repayments"] += 1


def bulk_load(api, args):
    as_of = args.as_of
    print(f"seed={args.seed} as_of={d(as_of)} clients={args.clients} (provisional volume)")
    plan, rng = build_plan(args.clients, args.seed, as_of)
    setup_fake = Faker("en_NG")
    setup_fake.seed_instance(args.seed + 1)
    opening = as_of - timedelta(days=3 * 365)

    ensure_currency(api)
    office_ids = ensure_offices(api, opening)
    staff_ids = ensure_staff(api, office_ids, opening, setup_fake)
    product_ids = ensure_products(api)

    stats = Counter()
    for c in plan:
        staff_id = staff_ids[(c.branch, c.officer)]
        client_id, created = create_client(api, c, office_ids[c.branch], staff_id)
        stats["clients_created" if created else "clients_skipped"] += 1
        for loan in c.loans:
            create_loan(api, rng, loan, client_id, staff_id, product_ids, as_of, stats)
        print(f"client    {c.ext_id} {'created' if created else 'exists '} loans={len(c.loans)}")
    print_summary(api, stats)


def print_summary(api, stats):
    statuses = Counter()
    offset = 0
    while True:
        page = api.get("loans", limit=200, offset=offset)
        items = page.get("pageItems", [])
        statuses.update(i["status"]["value"] for i in items)
        offset += len(items)
        if not items or offset >= page.get("totalFilteredRecords", 0):
            break
    print("--- summary")
    for k in sorted(stats):
        print(f"{k:16} {stats[k]}")
    for k, v in sorted(statuses.items()):
        print(f"loans[{k}] {v}")


# ---------------------------------------------------------------- continuous mode

def active_loans(api):
    ids, offset = [], 0
    while True:
        page = api.get("loans", limit=200, offset=offset)
        items = page.get("pageItems", [])
        ids += [i["id"] for i in items if i["status"].get("active")]
        offset += len(items)
        if not items or offset >= page.get("totalFilteredRecords", 0):
            return ids


def continuous(api, args):
    rng = random.Random(args.seed + 7)
    fake = Faker("en_NG")
    fake.seed_instance(args.seed + 7)
    run = datetime.now().strftime("%Y%m%d%H%M%S")
    product_ids = ensure_products(api)
    offices = {o.get("externalId"): o["id"] for o in api.get("offices")}
    officers = [(s["officeId"], s["id"]) for s in api.get("staff") if s.get("isLoanOfficer")
                and str(s.get("externalId", "")).startswith("FMB-ST-")]
    clients = [c["id"] for c in api.get("clients", limit=1000).get("pageItems", [])]
    loans = active_loans(api)
    if not officers or not offices:
        sys.exit("No FMB offices/officers found; run the bulk load first.")
    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    interval, n, stats = 60.0 / args.rate, 0, Counter()
    used_mobiles = set()
    print(f"continuous: rate={args.rate}/min active_loans={len(loans)} log={log_path}")

    def log(op, **kw):
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), "op": op, **kw}
        with log_path.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        stats[op] += 1
        print(json.dumps(rec))

    def new_loan(client_id, office_staff):
        nonlocal n
        n += 1
        product = "SME" if rng.random() < 0.25 else "GML"
        p, today = PRODUCTS[product], date.today()
        principal = pick_principal(rng, p)
        loan_id = api.post("loans", {
            **loan_terms(product_ids[product], p), "clientId": client_id, "loanOfficerId": office_staff[1],
            "externalId": f"LW-LN-{run}-{n:05d}", "principal": principal,
            "submittedOnDate": d(today), "expectedDisbursementDate": d(today)})["loanId"]
        api.post(f"loans/{loan_id}", {"approvedOnDate": d(today)}, command="approve")
        api.post(f"loans/{loan_id}", {"actualDisbursementDate": d(today), "transactionAmount": principal},
                 command="disburse")
        loans.append(loan_id)
        return loan_id, principal

    try:
        while True:
            op = rng.choices(["repayment", "new_client_loan", "repeat_loan"], [70, 20, 10])[0]
            try:
                if op == "repayment" and loans:
                    loan_id = rng.choice(loans)
                    loan = api.get(f"loans/{loan_id}", associations="repaymentSchedule")
                    due = next((pp["totalOutstandingForPeriod"] for pp in loan["repaymentSchedule"]["periods"]
                                if pp.get("period") and pp.get("totalOutstandingForPeriod", 0) > 0), 0)
                    amount = round(min(due, loan["summary"]["totalOutstanding"]), 2)
                    if amount <= 0:
                        loans.remove(loan_id)
                        continue
                    repay(api, loan_id, date.today(), amount)
                    if api.get(f"loans/{loan_id}")["status"]["value"] != "Active":
                        loans.remove(loan_id)
                    log("repayment", loan_id=loan_id, amount=amount)
                elif op == "new_client_loan":
                    n += 1
                    office_staff = rng.choice(officers)
                    today = date.today()
                    client_id = api.post("clients", {
                        "officeId": office_staff[0], "staffId": office_staff[1], "legalFormId": 1,
                        "firstname": fake.first_name(), "lastname": fake.last_name(),
                        "mobileNo": unique_mobile(rng, used_mobiles), "externalId": f"LW-CL-{run}-{n:05d}",
                        "dateOfBirth": d(today - timedelta(days=rng.randint(21 * 365, 60 * 365))),
                        "active": True, "activationDate": d(today), "submittedOnDate": d(today)})["clientId"]
                    clients.append(client_id)
                    loan_id, principal = new_loan(client_id, office_staff)
                    log("new_client_loan", client_id=client_id, loan_id=loan_id, amount=principal)
                elif op == "repeat_loan" and clients:
                    client_id = rng.choice(clients)
                    client = api.get(f"clients/{client_id}")
                    staff = client.get("staffId") or rng.choice(officers)[1]
                    loan_id, principal = new_loan(client_id, (client["officeId"], staff))
                    log("repeat_loan", client_id=client_id, loan_id=loan_id, amount=principal)
            except RuntimeError as e:
                stats["errors"] += 1
                print(f"error: {e}", file=sys.stderr)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("--- stopped")
        for k, v in sorted(stats.items()):
            print(f"{k:16} {v}")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clients", type=int, default=100, help="number of clients to load (default 100, provisional)")
    ap.add_argument("--seed", type=int, default=20261008)
    ap.add_argument("--as-of", type=lambda s: datetime.strptime(s, DATE_FMT).date(), default=date.today(),
                    help="anchor date for backdated activity (default today)")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--tenant", default="default")
    ap.add_argument("--continuous", action="store_true", help="run the live-write workload until Ctrl+C")
    ap.add_argument("--rate", type=float, default=6.0, help="continuous operations per minute")
    ap.add_argument("--log", default="logs/live-writes.jsonl", help="continuous-mode JSONL write log")
    args = ap.parse_args()

    if args.as_of > date.today():
        sys.exit("--as-of cannot be in the future (Fineract rejects future-dated transactions).")
    user, password = os.environ.get("FINERACT_USER"), os.environ.get("FINERACT_PASSWORD")
    if not user or not password:
        sys.exit("Set FINERACT_USER and FINERACT_PASSWORD in the environment.")

    api = Fineract(args.base_url, args.tenant, user, password)
    if args.continuous:
        continuous(api, args)
    else:
        bulk_load(api, args)


if __name__ == "__main__":
    main()
