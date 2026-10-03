"""The demo teams and named personas (SPEC 13.1) plus name pools for the large seed.

Every role and the confidential rule are demonstrable:
- priya.lead / asha / rahul / dev.viewer / meera / admin are the accounts the SPEC names;
- every team has two leads, so four-eyes approval always has someone who is not the requester;
- Compliance items are confidential: farah (member) cannot see ones she does not own, and
  sameer (viewer on Compliance) sees none of them;
- several people hold different roles in different teams.
"""

from dataclasses import dataclass

DEMO_PASSWORD = "baton-demo"  # shown on the login page only when DEMO_MODE=true  # noqa: S105
EMAIL_DOMAIN = "baton.test"


@dataclass(frozen=True)
class TeamSpec:
    key: str
    name: str
    description: str


@dataclass(frozen=True)
class Persona:
    local: str  # email local part
    name: str
    memberships: tuple[tuple[str, str], ...] = ()  # (team key, role)
    is_admin: bool = False

    @property
    def email(self) -> str:
        return f"{self.local}@{EMAIL_DOMAIN}"


DEMO_TEAMS: tuple[TeamSpec, ...] = (
    TeamSpec("PAY", "Payments", "Payment processing, refunds, settlements and chargebacks."),
    TeamSpec("SUP", "Customer Support", "First line for customer and merchant issues."),
    TeamSpec("SRE", "Platform & SRE", "Production reliability, infrastructure and incidents."),
    TeamSpec("CMP", "Compliance", "KYC, AML, audits and data-protection requests."),
    TeamSpec("FIN", "Finance Ops", "Reconciliation, payouts and month-end close."),
    TeamSpec("ENG", "Core Engineering", "Product engineering for the core platform."),
)

DEMO_PERSONAS: tuple[Persona, ...] = (
    Persona("admin", "Admin User", is_admin=True),
    # Payments
    Persona("priya.lead", "Priya Nair", (("PAY", "lead"),)),
    Persona("vikram.lead", "Vikram Shah", (("PAY", "lead"),)),
    Persona("asha", "Asha Rao", (("PAY", "member"), ("FIN", "viewer"))),
    Persona("rahul", "Rahul Verma", (("PAY", "member"), ("SRE", "member"))),
    # Customer Support
    Persona("sunita.lead", "Sunita Kapoor", (("SUP", "lead"),)),
    Persona("imran.lead", "Imran Qureshi", (("SUP", "lead"),)),
    Persona("meera", "Meera Iyer", (("SUP", "member"), ("PAY", "viewer"))),
    Persona("kavya", "Kavya Menon", (("SUP", "member"),)),
    # Platform & SRE
    Persona("neha.lead", "Neha Joshi", (("SRE", "lead"),)),
    Persona("karthik.lead", "Karthik Subramanian", (("SRE", "lead"), ("ENG", "member"))),
    Persona("rohan", "Rohan Mehta", (("SRE", "member"),)),
    Persona("divya", "Divya Pillai", (("SRE", "member"),)),
    Persona("dev.viewer", "Dev Malhotra", (("PAY", "viewer"), ("SRE", "viewer"))),
    # Compliance
    Persona("ishaan.lead", "Ishaan Bose", (("CMP", "lead"),)),
    Persona("lakshmi.lead", "Lakshmi Narayan", (("CMP", "lead"),)),
    Persona("farah", "Farah Khan", (("CMP", "member"),)),
    # Finance Ops
    Persona("gaurav.lead", "Gaurav Singh", (("FIN", "lead"),)),
    Persona("pooja.lead", "Pooja Reddy", (("FIN", "lead"),)),
    Persona("sameer", "Sameer Ali", (("FIN", "member"), ("CMP", "viewer"))),
    # Core Engineering
    Persona("aditya.lead", "Aditya Rao", (("ENG", "lead"),)),
    Persona("shreya.lead", "Shreya Banerjee", (("ENG", "lead"),)),
    Persona("nikhil", "Nikhil Das", (("ENG", "member"),)),
    Persona("tanvi", "Tanvi Kulkarni", (("ENG", "member"),)),
)

# Teams that exist only in the large seed (34, so 6 + 34 = 40).
EXTRA_TEAMS: tuple[TeamSpec, ...] = tuple(
    TeamSpec(key, name, f"{name} team.")
    for key, name in (
        ("RSK", "Risk & Fraud"),
        ("MOB", "Merchant Onboarding"),
        ("DAT", "Data Platform"),
        ("MBL", "Mobile Engineering"),
        ("WEB", "Web Engineering"),
        ("SEC", "Security"),
        ("LGL", "Legal"),
        ("TRE", "Treasury"),
        ("PAYO", "Payouts"),
        ("CRD", "Cards"),
        ("FXD", "FX and Currency"),
        ("BIL", "Billing"),
        ("ACC", "Accounting"),
        ("QAT", "Quality Assurance"),
        ("DVX", "Developer Experience"),
        ("NET", "Network Operations"),
        ("ITS", "IT Support"),
        ("HRO", "People Operations"),
        ("PRC", "Procurement"),
        ("VND", "Vendor Management"),
        ("ANL", "Analytics"),
        ("MKT", "Marketing Operations"),
        ("PRT", "Partnerships"),
        ("INT", "Integrations"),
        ("API", "API Platform"),
        ("SDK", "SDK and Libraries"),
        ("DOC", "Documentation"),
        ("TRN", "Training"),
        ("DSP", "Disputes"),
        ("CHB", "Chargebacks"),
        ("LCL", "Localization"),
        ("REG", "Regulatory Reporting"),
        ("BCP", "Business Continuity"),
        ("CAP", "Capacity Planning"),
    )
)

FIRST_NAMES = [
    "Aarav",
    "Aditi",
    "Akash",
    "Ananya",
    "Anil",
    "Arjun",
    "Bhavna",
    "Chetan",
    "Deepa",
    "Dhruv",
    "Esha",
    "Farhan",
    "Gauri",
    "Harsh",
    "Isha",
    "Jatin",
    "Kabir",
    "Lata",
    "Manish",
    "Mira",
    "Naveen",
    "Nisha",
    "Omkar",
    "Pallavi",
    "Qasim",
    "Radhika",
    "Sanjay",
    "Tara",
    "Uday",
    "Varun",
    "Waseem",
    "Yash",
    "Zoya",
    "Amit",
    "Bela",
    "Chirag",
    "Disha",
    "Eshan",
    "Fatima",
    "Girish",
    "Hema",
    "Irfan",
    "Jaya",
    "Kiran",
    "Leela",
    "Mohan",
    "Nandini",
    "Om",
    "Pranav",
    "Rekha",
    "Sunil",
    "Trisha",
    "Usha",
    "Vivek",
    "Yamini",
]
LAST_NAMES = [
    "Agarwal",
    "Bansal",
    "Chawla",
    "Desai",
    "Eapen",
    "Fernandes",
    "Gupta",
    "Hegde",
    "Iyer",
    "Jain",
    "Kamath",
    "Lal",
    "Malik",
    "Nambiar",
    "Oberoi",
    "Patel",
    "Qadri",
    "Rastogi",
    "Sethi",
    "Trivedi",
    "Upadhyay",
    "Varma",
    "Wagle",
    "Xavier",
    "Yadav",
    "Zacharia",
    "Bhatt",
    "Chopra",
    "Dutta",
    "Gill",
    "Joshi",
    "Khanna",
    "Luthra",
    "Mishra",
    "Naidu",
    "Pandey",
    "Rangan",
    "Saxena",
    "Tiwari",
]
