"""Realistic titles, descriptions, comments and reasons for seeded work."""

import random

SERVICES = [
    "checkout-api",
    "payments-gateway",
    "ledger-service",
    "refund-worker",
    "merchant-portal",
    "notification-service",
    "risk-engine",
    "settlement-batch",
    "auth-service",
    "search-index",
]
PARTNERS = ["Axis Bank", "HDFC acquirer", "Visa", "Mastercard", "UPI switch", "Razorpay bridge"]
REGIONS = ["ap-south-1", "eu-west-1", "us-east-1", "ap-southeast-1"]
QUEUES = ["payout-events", "refund-requests", "webhook-delivery", "kyc-checks", "email-outbox"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September"]
DOMAINS = ["pay.baton.test", "merchant.baton.test", "api.baton.test", "status.baton.test"]
TEAM_NAMES = ["Payments", "Finance Ops", "Core Engineering", "Customer Support", "Platform & SRE"]

TITLES: dict[str, tuple[str, ...]] = {
    "incident": (
        "p99 latency spike on {svc}",
        "{svc} returning 5xx after deploy {ver}",
        "Elevated error rate on {svc} in {region}",
        "Queue backlog growing on {queue}",
        "Database connection pool exhausted on {svc}",
        "Intermittent timeouts calling {partner}",
    ),
    "customer_issue": (
        "Customer {cust} charged twice for order {order}",
        "Merchant {merchant} cannot see settlements for {date}",
        "Login loop for customer {cust} on the mobile app",
        "Refund not received for order {order}",
        "Invoice PDF missing line items for {merchant}",
        "Webhook retries failing for merchant {merchant}",
    ),
    "payment_investigation": (
        "Refund stuck for order {order}",
        "Settlement mismatch of {amount} for merchant {merchant}",
        "Chargeback {cb} awaiting evidence",
        "Duplicate payout detected for {merchant}",
        "Payment {txn} captured but order not confirmed",
        "FX rate discrepancy on batch {batch}",
    ),
    "engineering": (
        "Add idempotency keys to the {svc} refund endpoint",
        "Migrate {svc} to the new queue client",
        "Flaky integration test in the {svc} pipeline",
        "Upgrade {svc} to Python 3.12",
        "Reduce cold start time for {svc}",
        "Rate-limit per-merchant calls to {svc}",
    ),
    "compliance_request": (
        "KYC re-verification for merchant {merchant}",
        "Data retention review for {svc} logs",
        "GDPR erasure request {ticket}",
        "AML alert review for account {acct}",
        "Quarterly access recertification for {team}",
        "Audit evidence request {ticket} from the external auditor",
    ),
    "ops_task": (
        "Rotate API credentials for {partner}",
        "Reconcile {date} settlement file with the bank statement",
        "Onboard {merchant} to payouts",
        "Renew the TLS certificate for {domain}",
        "Clean up stale feature flags in {svc}",
        "Prepare the {month} month-end close checklist",
    ),
}

DESCRIPTIONS: dict[str, tuple[str, ...]] = {
    "incident": (
        "Alerts fired at {time} for **{svc}**. Error budget for the month is at risk.\n\n"
        "- Impact: a share of checkout requests fail or time out\n"
        "- Suspected cause: recent deploy `{ver}`\n"
        "- Dashboard: see the latency panel for {region}",
        "{svc} is degraded. On-call acknowledged and is looking at saturation.\n\n"
        "Next: confirm whether rolling back `{ver}` restores latency.",
    ),
    "customer_issue": (
        "Reported by the customer through chat.\n\n"
        "- Customer: {cust}\n- Order: {order}\n- Merchant: {merchant}\n\n"
        "They have contacted us twice already and are waiting for an update.",
        "Merchant **{merchant}** wrote in about a problem since {date}. "
        "Screenshots are attached to the support ticket.",
    ),
    "payment_investigation": (
        "Order {order} shows a captured payment ({txn}) but the refund never left our side.\n\n"
        "1. Check the ledger entries for the order\n2. Compare with the acquirer file\n"
        "3. Re-issue the refund if it is safe",
        "Settlement for {merchant} is short by {amount}.\n\n"
        "```\nexpected  settled  diff\n```\nNeeds a ledger and acquirer comparison before anything is reversed.",
    ),
    "engineering": (
        "Technical debt item for **{svc}**.\n\n"
        "- [ ] design note\n- [ ] implementation\n- [ ] rollout behind a flag\n- [ ] remove old path",
        "Spike and fix for {svc}. Acceptance: no regression on the p95 dashboards.",
    ),
    "compliance_request": (
        "Request {ticket}. Handle under the confidentiality policy: do not discuss in public channels.\n\n"
        "Subject: **{merchant}** / account {acct}. Deadline is set by the regulator.",
        "Periodic review required by policy. Evidence must be attached before sign-off.",
    ),
    "ops_task": (
        "Routine operational task.\n\n- Owner to confirm the window\n- Update the runbook afterwards\n"
        "- Notify the partner team when done",
        "Needed for the {month} cycle. Checklist lives in the shared runbook.",
    ),
}

COMMENTS = (
    "Looking at this now.",
    "I can reproduce it on staging.",
    "Waiting for the bank to confirm; will update when they reply.",
    "Pulled the ledger entries, they look consistent on our side.",
    "Can someone from {team} confirm the expected behaviour?",
    "Raised priority because the merchant escalated.",
    "This overlaps with an earlier request, linking it here.",
    "Fix is in review.",
    "Deployed to production, monitoring for an hour.",
    "No further errors since the change. Planning to resolve.",
    "Customer confirmed it works now.",
    "Needs approval before we can proceed.",
    "Handing over, I am out from tomorrow.",
    "Added the evidence to the shared folder.",
    "Checked the logs for order {order}: the retry never fired.",
    "I think this is the same root cause as last week.",
    "Updated the runbook with the steps we took.",
    "Following up with the partner today.",
    "Blocked on credentials, requested through IT.",
    "Double-checked the amounts, they match the statement.",
)

BLOCK_REASONS = (
    "Waiting on bank response",
    "Waiting on the vendor to ship a fix",
    "Blocked on credentials from IT",
    "Waiting for the merchant to reply",
    "Needs a decision from legal",
)
RESOLUTION_NOTES = (
    "Fixed and verified in production.",
    "Refund re-issued and confirmed by the acquirer.",
    "Configuration corrected; monitoring shows normal behaviour.",
    "Reconciled; the difference was a timing issue.",
    "Completed as requested; runbook updated.",
    "Customer confirmed the issue is gone.",
)
CLOSE_REASONS = {
    "wont_do": ("Not worth the effort this quarter.", "Out of scope for this team."),
    "cannot_reproduce": ("Could not reproduce after three attempts.", "No longer happening."),
    "duplicate": ("Same as an existing request.", "Already being handled elsewhere."),
    "done": ("Confirmed fixed, closing.", "No reply from the requester, closing as done."),
}
REOPEN_REASONS = ("Problem is back.", "Fix did not hold.", "More cases found.")
LOWER_PRIORITY_REASONS = (
    "Impact is smaller than first thought.",
    "Workaround in place, no longer urgent.",
)
TRANSFER_REASONS = ("Belongs to another team.", "Routed to the wrong team.", "Needs their tooling.")
APPROVAL_REQUEST_NOTES = ("Ready for review.", "Please check the amounts before I proceed.", None)
APPROVE_NOTES = ("Reviewed, looks right.", "Approved.", "Checked against the ledger.")
REJECT_NOTES = ("Amount does not match the ledger.", "Needs more evidence.", "Wrong account.")
CONFIDENTIAL_REASONS = ("Contains customer data.", "Handled under the incident policy.")
APPROVAL_OFF_REASONS = ("Routine task, approval adds no value.",)


def pick[T](rng: random.Random, pool: tuple[T, ...]) -> T:
    return pool[rng.randrange(len(pool))]


def _fill(rng: random.Random, template: str) -> str:
    values = {
        "svc": pick(rng, tuple(SERVICES)),
        "ver": f"v{rng.randint(2, 4)}.{rng.randint(0, 30)}.{rng.randint(0, 9)}",
        "region": pick(rng, tuple(REGIONS)),
        "queue": pick(rng, tuple(QUEUES)),
        "partner": pick(rng, tuple(PARTNERS)),
        "cust": f"C-{rng.randint(1000, 99999)}",
        "order": str(rng.randint(10000, 99999)),
        "merchant": f"M-{rng.randint(1000, 9999)}",
        "date": f"{rng.randint(1, 28)} {pick(rng, tuple(MONTHS))}",
        "amount": f"INR {rng.randint(500, 250000):,}",
        "cb": f"CB-{rng.randint(10000, 99999)}",
        "txn": f"TXN{rng.randint(10**7, 10**8 - 1)}",
        "batch": f"B{rng.randint(100, 999)}",
        "ticket": f"REQ-{rng.randint(1000, 9999)}",
        "acct": f"ACC-{rng.randint(100000, 999999)}",
        "team": pick(rng, tuple(TEAM_NAMES)),
        "domain": pick(rng, tuple(DOMAINS)),
        "month": pick(rng, tuple(MONTHS)),
        "time": f"{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d} UTC",
    }
    return template.format(**values)


def make_title_and_description(rng: random.Random, item_type: str) -> tuple[str, str]:
    return _fill(rng, pick(rng, TITLES[item_type])), _fill(rng, pick(rng, DESCRIPTIONS[item_type]))


def make_comment(rng: random.Random) -> str:
    return _fill(rng, pick(rng, COMMENTS))


def edited_title(rng: random.Random, title: str) -> str:
    suffix = pick(rng, (" (updated)", " - needs follow-up", " [customer escalated]"))
    if len(title) + len(suffix) > 200:
        title = title[: 200 - len(suffix)]
    return title + suffix


def edited_description(rng: random.Random, description: str) -> str:
    note = pick(
        rng,
        (
            "Update: the customer added more detail.",
            "Update: scope widened after the first look.",
            "Update: corrected the order reference.",
        ),
    )
    return f"{description}\n\n{note}"
