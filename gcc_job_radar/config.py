"""Configuration, seed company registry, and regex patterns for GCC Job Radar."""

import re
from gcc_job_radar.models import ATSProvider, CompanyConfig

# Curated registry of 150+ enterprise GCCs, global retail tech hubs, Fortune 500 tech centers in India
COMPANIES: list[CompanyConfig] = [
    # --- PHENOM / SUCCESSFACTORS: Middle Eastern & Global Conglomerates ---
    CompanyConfig(
        name="Majid Al Futtaim",
        provider=ATSProvider.PHENOM_SUCCESSFACTORS,
        board_token="careers.majidalfuttaim.com",
    ),

    # --- WORKDAY (CXS API): Fortune 500 Enterprise GCCs ---
    CompanyConfig(name="Walmart Global Tech", provider=ATSProvider.WORKDAY, board_token="walmart/WalmartExternal", cluster="3"),
    CompanyConfig(name="Autodesk", provider=ATSProvider.WORKDAY, board_token="autodesk/Ext", cluster="3"),
    CompanyConfig(name="Schneider Electric", provider=ATSProvider.WORKDAY, board_token="schneider_electric/schneider_external", cluster="3"),
    CompanyConfig(name="Adobe", provider=ATSProvider.WORKDAY, board_token="adobe/external_experienced", cluster="5"),
    CompanyConfig(name="Salesforce", provider=ATSProvider.WORKDAY, board_token="salesforce/External_Career_Site", cluster="1"),
    CompanyConfig(name="Maersk", provider=ATSProvider.WORKDAY, board_token="maersk/Maersk_Careers", cluster="3"),
    CompanyConfig(name="Micron", provider=ATSProvider.WORKDAY, board_token="micron/External", cluster="1"),
    CompanyConfig(name="Philips", provider=ATSProvider.WORKDAY, board_token="philips/jobs-and-careers", cluster="3"),
    CompanyConfig(name="NVIDIA", provider=ATSProvider.WORKDAY, board_token="nvidia/NVIDIAExternalCareerSite", cluster="5"),
    CompanyConfig(name="Caterpillar", provider=ATSProvider.WORKDAY, board_token="cat/CaterpillarCareers", cluster="5"),
    CompanyConfig(name="BrowserStack", provider=ATSProvider.WORKDAY, board_token="browserstack/External", cluster="3"),
    CompanyConfig(name="Qualcomm", provider=ATSProvider.WORKDAY, board_token="qualcomm/External", cluster="12"),

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
    CompanyConfig(name="Honeywell", provider=ATSProvider.SMARTRECRUITERS, board_token="Honeywell"),
    CompanyConfig(name="Bosch", provider=ATSProvider.SMARTRECRUITERS, board_token="BoschGroup"),
    CompanyConfig(name="ABB", provider=ATSProvider.SMARTRECRUITERS, board_token="ABB"),
    CompanyConfig(name="Western Digital", provider=ATSProvider.SMARTRECRUITERS, board_token="WesternDigital"),
    CompanyConfig(name="PhonePe", provider=ATSProvider.SMARTRECRUITERS, board_token="PHONEPELIMITED"),
    CompanyConfig(name="Pine Labs", provider=ATSProvider.SMARTRECRUITERS, board_token="PineLabs"),
    CompanyConfig(name="Splunk", provider=ATSProvider.SMARTRECRUITERS, board_token="Splunk"),
    CompanyConfig(name="Experian", provider=ATSProvider.SMARTRECRUITERS, board_token="Experian"),
    CompanyConfig(name="Continental", provider=ATSProvider.SMARTRECRUITERS, board_token="Continental"),
    CompanyConfig(name="Ubisoft", provider=ATSProvider.SMARTRECRUITERS, board_token="Ubisoft2"),
    CompanyConfig(name="John Deere", provider=ATSProvider.SMARTRECRUITERS, board_token="JohnDeere"),
    CompanyConfig(name="Thales", provider=ATSProvider.SMARTRECRUITERS, board_token="Thales"),
    CompanyConfig(name="Publicis Sapient", provider=ATSProvider.SMARTRECRUITERS, board_token="PublicisSapient"),

    # --- LEVER: FinTech & Distributed Tech Hubs ---
    CompanyConfig(name="Atlassian", provider=ATSProvider.LEVER, board_token="atlassian"),
    CompanyConfig(name="Ripple", provider=ATSProvider.LEVER, board_token="ripple"),
    CompanyConfig(name="Fullscript", provider=ATSProvider.LEVER, board_token="fullscript"),
    CompanyConfig(name="Kraken", provider=ATSProvider.LEVER, board_token="kraken"),
    CompanyConfig(name="Palantir", provider=ATSProvider.LEVER, board_token="palantir"),
    CompanyConfig(name="Revolut", provider=ATSProvider.LEVER, board_token="revolut"),
    CompanyConfig(name="Branch", provider=ATSProvider.LEVER, board_token="branch"),
    CompanyConfig(name="Checkr", provider=ATSProvider.LEVER, board_token="checkr"),
    CompanyConfig(name="CRED", provider=ATSProvider.LEVER, board_token="cred"),
    CompanyConfig(name="Meesho", provider=ATSProvider.LEVER, board_token="meesho"),
    CompanyConfig(name="Fi Money", provider=ATSProvider.LEVER, board_token="fi"),

    # --- ASHBY: Next-Gen Tech, AI & High-Growth Scaleups ---
    CompanyConfig(name="Linear", provider=ATSProvider.ASHBY, board_token="linear"),
    CompanyConfig(name="Ramp", provider=ATSProvider.ASHBY, board_token="ramp"),
    CompanyConfig(name="Synthesia", provider=ATSProvider.ASHBY, board_token="synthesia"),
    CompanyConfig(name="Monzo", provider=ATSProvider.ASHBY, board_token="monzo"),
    CompanyConfig(name="Notion", provider=ATSProvider.ASHBY, board_token="notion"),
    CompanyConfig(name="Snowflake", provider=ATSProvider.ASHBY, board_token="snowflake"),
    CompanyConfig(name="LlamaIndex", provider=ATSProvider.ASHBY, board_token="llamaindex"),
    CompanyConfig(name="Retool", provider=ATSProvider.ASHBY, board_token="retool"),
    CompanyConfig(name="Vercel", provider=ATSProvider.ASHBY, board_token="vercel"),
    CompanyConfig(name="Confluent", provider=ATSProvider.ASHBY, board_token="confluent"),
    CompanyConfig(name="Modal", provider=ATSProvider.ASHBY, board_token="modal"),
    CompanyConfig(name="Perplexity", provider=ATSProvider.ASHBY, board_token="perplexity"),
    CompanyConfig(name="Supabase", provider=ATSProvider.ASHBY, board_token="supabase"),
    CompanyConfig(name="OpenAI", provider=ATSProvider.ASHBY, board_token="openai"),
    CompanyConfig(name="ElevenLabs", provider=ATSProvider.ASHBY, board_token="elevenlabs"),
    CompanyConfig(name="Cognition AI", provider=ATSProvider.ASHBY, board_token="cognition"),
    CompanyConfig(name="Character AI", provider=ATSProvider.ASHBY, board_token="character"),
    CompanyConfig(name="Replit", provider=ATSProvider.ASHBY, board_token="replit"),
    CompanyConfig(name="Warp", provider=ATSProvider.ASHBY, board_token="warp"),
    CompanyConfig(name="Pika", provider=ATSProvider.ASHBY, board_token="pika"),
    CompanyConfig(name="Cursor", provider=ATSProvider.ASHBY, board_token="cursor"),
    CompanyConfig(name="Cohere", provider=ATSProvider.ASHBY, board_token="cohere"),
    CompanyConfig(name="LangChain", provider=ATSProvider.ASHBY, board_token="langchain"),
    CompanyConfig(name="Deepgram", provider=ATSProvider.ASHBY, board_token="deepgram"),
    CompanyConfig(name="Exa", provider=ATSProvider.ASHBY, board_token="exa"),
    CompanyConfig(name="Render", provider=ATSProvider.ASHBY, board_token="render"),
    CompanyConfig(name="Cartesia", provider=ATSProvider.ASHBY, board_token="cartesia"),
    CompanyConfig(name="Braintrust", provider=ATSProvider.ASHBY, board_token="braintrust"),
    CompanyConfig(name="Tavily", provider=ATSProvider.ASHBY, board_token="tavily"),
    CompanyConfig(name="Midjourney", provider=ATSProvider.ASHBY, board_token="midjourney"),
    CompanyConfig(name="Resend", provider=ATSProvider.ASHBY, board_token="resend"),
    CompanyConfig(name="Bubble", provider=ATSProvider.ASHBY, board_token="bubble"),
    CompanyConfig(name="Railway", provider=ATSProvider.ASHBY, board_token="railway"),
    CompanyConfig(name="Neon", provider=ATSProvider.ASHBY, board_token="neon"),
    CompanyConfig(name="Navi", provider=ATSProvider.ASHBY, board_token="navi"),

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
    CompanyConfig(name="Razorpay", provider=ATSProvider.GREENHOUSE, board_token="razorpaysoftwareprivatelimited"),
    CompanyConfig(name="Groww", provider=ATSProvider.GREENHOUSE, board_token="groww"),
    CompanyConfig(name="Tower Research", provider=ATSProvider.GREENHOUSE, board_token="towerresearchcapital"),
    CompanyConfig(name="WorldQuant", provider=ATSProvider.GREENHOUSE, board_token="worldquant"),
    CompanyConfig(name="FalconX", provider=ATSProvider.GREENHOUSE, board_token="falconx"),
    CompanyConfig(name="DE Shaw", provider=ATSProvider.GREENHOUSE, board_token="arcesiumllc"),
    CompanyConfig(name="Jane Street", provider=ATSProvider.GREENHOUSE, board_token="janestreet"),
    CompanyConfig(name="Jump Trading", provider=ATSProvider.GREENHOUSE, board_token="jumptrading"),
    CompanyConfig(name="Akuna Capital", provider=ATSProvider.GREENHOUSE, board_token="akunacapital"),
    CompanyConfig(name="Point72", provider=ATSProvider.GREENHOUSE, board_token="point72"),
    CompanyConfig(name="Millennium", provider=ATSProvider.GREENHOUSE, board_token="millennium"),
    CompanyConfig(name="Affirm", provider=ATSProvider.GREENHOUSE, board_token="affirm"),
    CompanyConfig(name="Chime", provider=ATSProvider.GREENHOUSE, board_token="chime"),
    CompanyConfig(name="Block", provider=ATSProvider.GREENHOUSE, board_token="block"),
    CompanyConfig(name="MongoDB", provider=ATSProvider.GREENHOUSE, board_token="mongodb"),
    CompanyConfig(name="Twilio", provider=ATSProvider.GREENHOUSE, board_token="twilio"),
    CompanyConfig(name="Datadog", provider=ATSProvider.GREENHOUSE, board_token="datadog"),
    CompanyConfig(name="New Relic", provider=ATSProvider.GREENHOUSE, board_token="newrelic"),
    CompanyConfig(name="Okta", provider=ATSProvider.GREENHOUSE, board_token="okta"),
    CompanyConfig(name="Postman", provider=ATSProvider.GREENHOUSE, board_token="postman"),
    CompanyConfig(name="Scale AI", provider=ATSProvider.GREENHOUSE, board_token="scaleai"),
    CompanyConfig(name="Glean", provider=ATSProvider.GREENHOUSE, board_token="gleanwork"),
    CompanyConfig(name="Rippling", provider=ATSProvider.GREENHOUSE, board_token="rippling"),
    CompanyConfig(name="Weights & Biases", provider=ATSProvider.GREENHOUSE, board_token="coreweave"),
    CompanyConfig(name="HashiCorp", provider=ATSProvider.GREENHOUSE, board_token="hashicorp"),
    CompanyConfig(name="Hasura", provider=ATSProvider.GREENHOUSE, board_token="hasura"),
    CompanyConfig(name="PagerDuty", provider=ATSProvider.GREENHOUSE, board_token="pagerduty"),
    CompanyConfig(name="Fivetran", provider=ATSProvider.GREENHOUSE, board_token="fivetran"),
    CompanyConfig(name="Cockroach Labs", provider=ATSProvider.GREENHOUSE, board_token="cockroachlabs"),
    CompanyConfig(name="Airbnb", provider=ATSProvider.GREENHOUSE, board_token="airbnb"),
    CompanyConfig(name="Lyft", provider=ATSProvider.GREENHOUSE, board_token="lyft"),
    CompanyConfig(name="Twitch", provider=ATSProvider.GREENHOUSE, board_token="twitch"),
    CompanyConfig(name="Pure Storage", provider=ATSProvider.GREENHOUSE, board_token="purestorage"),
    CompanyConfig(name="Netskope", provider=ATSProvider.GREENHOUSE, board_token="netskope"),
    CompanyConfig(name="Zscaler", provider=ATSProvider.GREENHOUSE, board_token="zscaler"),
    CompanyConfig(name="Grafana Labs", provider=ATSProvider.GREENHOUSE, board_token="grafanalabs"),
    CompanyConfig(name="Workato", provider=ATSProvider.GREENHOUSE, board_token="workato"),
    CompanyConfig(name="Braze", provider=ATSProvider.GREENHOUSE, board_token="braze"),
    CompanyConfig(name="Klaviyo", provider=ATSProvider.GREENHOUSE, board_token="klaviyo"),
    CompanyConfig(name="Qualtrics", provider=ATSProvider.GREENHOUSE, board_token="qualtrics"),
    CompanyConfig(name="Dropbox", provider=ATSProvider.GREENHOUSE, board_token="dropbox"),
    CompanyConfig(name="Smartsheet", provider=ATSProvider.GREENHOUSE, board_token="smartsheet"),
    CompanyConfig(name="Zuora", provider=ATSProvider.GREENHOUSE, board_token="zuora"),
    CompanyConfig(name="Intercom", provider=ATSProvider.GREENHOUSE, board_token="intercom"),
    CompanyConfig(name="Duolingo", provider=ATSProvider.GREENHOUSE, board_token="duolingo"),
    CompanyConfig(name="Hightouch", provider=ATSProvider.GREENHOUSE, board_token="hightouch"),
    CompanyConfig(name="Mixpanel", provider=ATSProvider.GREENHOUSE, board_token="mixpanel"),
    CompanyConfig(name="Cribl", provider=ATSProvider.GREENHOUSE, board_token="cribl"),
    CompanyConfig(name="LaunchDarkly", provider=ATSProvider.GREENHOUSE, board_token="launchdarkly"),
    CompanyConfig(name="Amplitude", provider=ATSProvider.GREENHOUSE, board_token="amplitude"),
    CompanyConfig(name="Algolia", provider=ATSProvider.GREENHOUSE, board_token="algolia"),
    CompanyConfig(name="Webflow", provider=ATSProvider.GREENHOUSE, board_token="webflow"),
    CompanyConfig(name="Starburst", provider=ATSProvider.GREENHOUSE, board_token="starburst"),
    CompanyConfig(name="Coursera", provider=ATSProvider.GREENHOUSE, board_token="coursera"),
    CompanyConfig(name="Airtable", provider=ATSProvider.GREENHOUSE, board_token="airtable"),
    CompanyConfig(name="Yugabyte", provider=ATSProvider.GREENHOUSE, board_token="yugabyte"),
    CompanyConfig(name="Udemy", provider=ATSProvider.GREENHOUSE, board_token="udemy"),
    CompanyConfig(name="PlanetScale", provider=ATSProvider.GREENHOUSE, board_token="planetscale"),
    CompanyConfig(name="Dialpad", provider=ATSProvider.GREENHOUSE, board_token="dialpad"),
    CompanyConfig(name="Vonage", provider=ATSProvider.GREENHOUSE, board_token="vonage"),
    CompanyConfig(name="Fastly", provider=ATSProvider.GREENHOUSE, board_token="fastly"),
    CompanyConfig(name="Neo4j", provider=ATSProvider.GREENHOUSE, board_token="neo4j"),
    CompanyConfig(name="Customer.io", provider=ATSProvider.GREENHOUSE, board_token="customerio"),
    CompanyConfig(name="Dataiku", provider=ATSProvider.GREENHOUSE, board_token="dataiku"),
    CompanyConfig(name="Iterable", provider=ATSProvider.GREENHOUSE, board_token="iterable"),
    CompanyConfig(name="NICE", provider=ATSProvider.GREENHOUSE, board_token="nice"),
    CompanyConfig(name="Sumo Logic", provider=ATSProvider.GREENHOUSE, board_token="sumologic"),
    CompanyConfig(name="InMobi", provider=ATSProvider.GREENHOUSE, board_token="inmobi"),
    CompanyConfig(name="Slice", provider=ATSProvider.GREENHOUSE, board_token="slice"),
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
