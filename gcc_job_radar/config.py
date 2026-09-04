"""Configuration, seed company registry, and regex patterns for GCC Job Radar."""

import re
from gcc_job_radar.models import ATSProvider, CompanyConfig

# Curated registry of 70+ enterprise GCCs, global retail tech hubs, Fortune 500 tech centers in India
COMPANIES: list[CompanyConfig] = [
    # --- PHENOM / SUCCESSFACTORS: Middle Eastern & Global Conglomerates ---
    CompanyConfig(
        name="Majid Al Futtaim",
        provider=ATSProvider.PHENOM_SUCCESSFACTORS,
        board_token="careers.majidalfuttaim.com",
    ),

    # --- WORKDAY (CXS API): Fortune 500 Enterprise GCCs ---
    CompanyConfig(
        name="Walmart Global Tech",
        provider=ATSProvider.WORKDAY,
        board_token="walmart/WalmartExternal",
        cluster="3",
    ),
    CompanyConfig(
        name="Autodesk",
        provider=ATSProvider.WORKDAY,
        board_token="autodesk/Ext",
        cluster="3",
    ),
    CompanyConfig(
        name="Schneider Electric",
        provider=ATSProvider.WORKDAY,
        board_token="schneider_electric/schneider_external",
        cluster="3",
    ),
    CompanyConfig(
        name="Adobe",
        provider=ATSProvider.WORKDAY,
        board_token="adobe/external_experienced",
        cluster="5",
    ),
    CompanyConfig(
        name="Salesforce",
        provider=ATSProvider.WORKDAY,
        board_token="salesforce/External_Career_Site",
        cluster="1",
    ),

    # --- SMARTRECRUITERS: Enterprise GCCs, Retail & Global Conglomerates ---
    CompanyConfig(name="Wolters Kluwer", provider=ATSProvider.SMARTRECRUITERS, board_token="WoltersKluwer"),
    CompanyConfig(name="Trimble", provider=ATSProvider.SMARTRECRUITERS, board_token="Trimble"),
    CompanyConfig(name="Amadeus Labs", provider=ATSProvider.SMARTRECRUITERS, board_token="Amadeus"),
    CompanyConfig(name="Baker Hughes", provider=ATSProvider.SMARTRECRUITERS, board_token="BakerHughes"),
    CompanyConfig(name="Danfoss", provider=ATSProvider.SMARTRECRUITERS, board_token="Danfoss"),
    CompanyConfig(name="Schindler", provider=ATSProvider.SMARTRECRUITERS, board_token="Schindler"),
    CompanyConfig(name="SimCorp", provider=ATSProvider.SMARTRECRUITERS, board_token="SimCorp"),
    CompanyConfig(name="Worldline", provider=ATSProvider.SMARTRECRUITERS, board_token="Worldline"),
    CompanyConfig(name="Lowe's India", provider=ATSProvider.SMARTRECRUITERS, board_token="Lowes"),
    CompanyConfig(name="Giant Eagle", provider=ATSProvider.SMARTRECRUITERS, board_token="GiantEagle"),
    CompanyConfig(name="Delivery Hero", provider=ATSProvider.SMARTRECRUITERS, board_token="DeliveryHero"),
    CompanyConfig(name="Just Eat Takeaway", provider=ATSProvider.SMARTRECRUITERS, board_token="JustEatTakeawaycom"),
    CompanyConfig(name="Adyen", provider=ATSProvider.SMARTRECRUITERS, board_token="Adyen"),
    CompanyConfig(name="Avery Dennison", provider=ATSProvider.SMARTRECRUITERS, board_token="AveryDennison"),
    CompanyConfig(name="Collibra", provider=ATSProvider.SMARTRECRUITERS, board_token="Collibra"),
    CompanyConfig(name="SGS", provider=ATSProvider.SMARTRECRUITERS, board_token="SGS"),

    # --- GREENHOUSE: US/EU Tech Giants, Cloud & SaaS ---
    CompanyConfig(name="Databricks", provider=ATSProvider.GREENHOUSE, board_token="databricks"),
    CompanyConfig(name="Stripe", provider=ATSProvider.GREENHOUSE, board_token="stripe"),
    CompanyConfig(name="Figma", provider=ATSProvider.GREENHOUSE, board_token="figma"),
    CompanyConfig(name="GitLab", provider=ATSProvider.GREENHOUSE, board_token="gitlab"),
    CompanyConfig(name="Pinterest", provider=ATSProvider.GREENHOUSE, board_token="pinterest"),
    CompanyConfig(name="Rubrik", provider=ATSProvider.GREENHOUSE, board_token="rubrik"),
    CompanyConfig(name="Elastic", provider=ATSProvider.GREENHOUSE, board_token="elastic"),
    CompanyConfig(name="Cloudflare", provider=ATSProvider.GREENHOUSE, board_token="cloudflare"),
    CompanyConfig(name="Reddit", provider=ATSProvider.GREENHOUSE, board_token="reddit"),
    CompanyConfig(name="Couchbase", provider=ATSProvider.GREENHOUSE, board_token="couchbase"),
    CompanyConfig(name="DoorDash", provider=ATSProvider.GREENHOUSE, board_token="doordash"),
    CompanyConfig(name="Brex", provider=ATSProvider.GREENHOUSE, board_token="brex"),
    CompanyConfig(name="Toast", provider=ATSProvider.GREENHOUSE, board_token="toastworkplace"),
    CompanyConfig(name="Samsara", provider=ATSProvider.GREENHOUSE, board_token="samsara"),
    CompanyConfig(name="Flexport", provider=ATSProvider.GREENHOUSE, board_token="flexport"),
    CompanyConfig(name="PostHog", provider=ATSProvider.GREENHOUSE, board_token="posthog"),
    CompanyConfig(name="Deel", provider=ATSProvider.GREENHOUSE, board_token="deel"),
    CompanyConfig(name="Docker", provider=ATSProvider.GREENHOUSE, board_token="docker"),
    CompanyConfig(name="Coinbase", provider=ATSProvider.GREENHOUSE, board_token="coinbase"),
    CompanyConfig(name="Robinhood", provider=ATSProvider.GREENHOUSE, board_token="robinhood"),
    CompanyConfig(name="Miro", provider=ATSProvider.GREENHOUSE, board_token="miro"),
    CompanyConfig(name="Celonis", provider=ATSProvider.GREENHOUSE, board_token="celonis"),
    CompanyConfig(name="Personio", provider=ATSProvider.GREENHOUSE, board_token="personio"),
    CompanyConfig(name="GoCardless", provider=ATSProvider.GREENHOUSE, board_token="gocardless"),
    CompanyConfig(name="Wise", provider=ATSProvider.GREENHOUSE, board_token="transferwise"),
    CompanyConfig(name="Deliveroo", provider=ATSProvider.GREENHOUSE, board_token="deliveroo"),
    CompanyConfig(name="Canva", provider=ATSProvider.GREENHOUSE, board_token="canva"),
    CompanyConfig(name="Snyk", provider=ATSProvider.GREENHOUSE, board_token="snyk"),
    CompanyConfig(name="Thoughtworks", provider=ATSProvider.GREENHOUSE, board_token="thoughtworks"),
    CompanyConfig(name="Tesco", provider=ATSProvider.GREENHOUSE, board_token="tesco"),
    CompanyConfig(name="Target India", provider=ATSProvider.GREENHOUSE, board_token="target"),
    CompanyConfig(name="Siemens Healthineers", provider=ATSProvider.GREENHOUSE, board_token="siemenshealthineers"),

    # --- LEVER: FinTech & Distributed Tech Hubs ---
    CompanyConfig(name="Atlassian", provider=ATSProvider.LEVER, board_token="atlassian"),
    CompanyConfig(name="Ripple", provider=ATSProvider.LEVER, board_token="ripple"),
    CompanyConfig(name="Fullscript", provider=ATSProvider.LEVER, board_token="fullscript"),
    CompanyConfig(name="Kraken", provider=ATSProvider.LEVER, board_token="kraken"),
    CompanyConfig(name="Palantir", provider=ATSProvider.LEVER, board_token="palantir"),
    CompanyConfig(name="Revolut", provider=ATSProvider.LEVER, board_token="revolut"),
    CompanyConfig(name="Branch", provider=ATSProvider.LEVER, board_token="branch"),
    CompanyConfig(name="Checkr", provider=ATSProvider.LEVER, board_token="checkr"),

    # --- ASHBY: Next-Gen Tech & High-Growth Scaleups ---
    CompanyConfig(name="Linear", provider=ATSProvider.ASHBY, board_token="linear"),
    CompanyConfig(name="Ramp", provider=ATSProvider.ASHBY, board_token="ramp"),
    CompanyConfig(name="Synthesia", provider=ATSProvider.ASHBY, board_token="synthesia"),
    CompanyConfig(name="Monzo", provider=ATSProvider.ASHBY, board_token="monzo"),
    CompanyConfig(name="Notion", provider=ATSProvider.ASHBY, board_token="notion"),
    CompanyConfig(name="Snowflake", provider=ATSProvider.ASHBY, board_token="snowflake"),
    CompanyConfig(name="LlamaIndex", provider=ATSProvider.ASHBY, board_token="llamaindex"),
    CompanyConfig(name="Retool", provider=ATSProvider.ASHBY, board_token="retool"),
    CompanyConfig(name="Vercel", provider=ATSProvider.ASHBY, board_token="vercel"),
]

# Strict entry-level tech title positive pattern
INCLUDE_TITLE_PATTERN: re.Pattern[str] = re.compile(
    r"""
    (?ix)
    \b(
        # SDE variants
        sde[- ]?(?:1|i)\b |
        software\s+(?:development\s+)?engineer[- ]?(?:1|i)\b |
        software\s+developer[- ]?(?:1|i)\b |
        # Associate / Junior roles
        associate\s+(?:software\s+|systems?\s+|qa\s+|test\s+|cloud\s+|data\s+|backend\s+|frontend\s+|fullstack\s+)?engineer\b |
        associate\s+(?:software\s+)?developer\b |
        junior\s+(?:software\s+|systems?\s+|qa\s+|test\s+|cloud\s+|data\s+|backend\s+|frontend\s+|fullstack\s+)?engineer\b |
        junior\s+(?:software\s+)?developer\b |
        # Graduate / Fresher / Entry Level
        graduate\s+(?:software\s+|systems?\s+)?engineer\b |
        graduate\s+(?:software\s+)?developer\b |
        graduate\s+technical\s+(?:intern|trainee|program)\b |
        fresher\b |
        entry[- ]level\s+(?:software\s+|systems?\s+)?(?:engineer|developer)\b |
        # MTS variants
        (?:member\s+of\s+technical\s+staff|mts)[- ]?(?:1|i)\b |
        # Tech Intern roles
        (?:software|engineering|developer|tech|swe|data|qa|backend|frontend)\s+intern\b |
        intern[- ]software\s+engineer\b
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Strict disqualification pattern (senior, staff, lead, higher numeral levels, non-tech)
EXCLUDE_TITLE_PATTERN: re.Pattern[str] = re.compile(
    r"""
    (?ix)
    \b(
        # Seniority levels
        senior|sr\.?|lead|principal|staff|distinguished|fellow|
        director|architect|manager|head\s+of|tech\s+lead|executive|vp|vice\s+president|
        # Higher level numerals (strict word boundaries to avoid sub-matching UI, IV in words, etc.)
        ii|iii|iv|v|vi|2|3|4|5|6|
        # Non-engineering / non-tech professions
        sales|marketing|hr|recruiter|recruiting|talent|account\s+executive|
        customer\s+support|customer\s+success|customer\s+experience|support\s+specialist|
        operations|finance|legal|compliance|business\s+development|bdr|sdr
    )\b
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Indian tech hubs and remote India locations
LOCATION_PATTERN: re.Pattern[str] = re.compile(
    r"""
    (?ix)
    \b(
        bengaluru|bangalore|
        hyderabad|secunderabad|
        pune|
        gurgaon|gurugram|noida|delhi|new\s+delhi|ncr|
        mumbai|navi\s+mumbai|thane|
        chennai|madras|
        india
    )\b
    """,
    re.VERBOSE | re.IGNORECASE,
)
