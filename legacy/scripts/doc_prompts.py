"""
Claude system prompts for document extraction.
Each prompt targets a specific document type and instructs Claude
to output structured JSON matching the prices + sentiment schema.
"""

# ── Shared constants ──────────────────────────────────────────────────────────

PRODUCTS = ["Palm", "Soybean", "Other Edible Oils", "Energy", "Biodiesel"]
GEOGRAPHIES = ["Asia", "America", "EU", "Africa"]
SUB_GEOGRAPHIES = [
    "Malaysia", "Indonesia", "China", "India", "Philippines", "Bangladesh",
    "Pakistan", "Thailand", "Vietnam",
    "USA", "Brazil", "Argentina", "Canada",
    "EU", "UK", "Turkey",
    "Nigeria", "Kenya", "Egypt", "South Africa",
    "Global",
]

# ── Shared output schema description (included in all prompts) ────────────────

_SCHEMA = """
You MUST output valid JSON only — no explanation, no markdown fences.

Output format:
{
  "source_document": "<filename>",
  "document_date": "YYYY-MM-DD",
  "prices": [
    {
      "product": "<one of: Palm, Soybean, Other Edible Oils, Energy, Biodiesel>",
      "geography": "<one of: Asia, America, EU, Africa>",
      "sub_geography": "<specific country or 'Global'>",
      "price_type": "<one of: spot, forward, forecast, support, resistance, target>",
      "value": <number>,
      "unit": "<e.g. USD/MT, MYR/MT, cents/lb, EUR/MT>",
      "change": <number or null>,
      "tenor": "<e.g. prompt, M1, M2, Q1, 2026, or null>",
      "broker": "<broker name or null>",
      "raw_text": "<exact sentence/phrase from document>"
    }
  ],
  "sentiment": [
    {
      "product": "<one of: Palm, Soybean, Other Edible Oils, Energy, Biodiesel>",
      "geography": "<one of: Asia, America, EU, Africa>",
      "sentiment": "<one of: bullish, bearish, neutral>",
      "confidence": <0.0 to 1.0>,
      "theme": "<e.g. supply_tightness, demand_weakness, export_strong, policy, weather, production_increase, crush_margin, inventory_draw, logistics, biodiesel_mandate>",
      "timeframe": "<one of: short_term, medium_term, long_term>",
      "summary": "<one-sentence summary of the signal>",
      "raw_text": "<exact sentence/phrase from document>"
    }
  ]
}
"""

# ── Shared rules ──────────────────────────────────────────────────────────────

_RULES = """
Rules:
- Extract ALL price data points and sentiment signals. Be thorough.
- Never infer or hallucinate — only extract what is explicitly stated.
- If a date is unclear, use the document date.
- Map every product to the closest category: Palm (includes CPO, olein, stearin, PFAD, PKO, palm kernel),
  Soybean (includes soybean oil, soybean meal, soybeans, soy crush),
  Other Edible Oils (includes sunflower, rapeseed, canola, groundnut, cottonseed, rice bran, coconut, tallow),
  Energy (includes crude oil, gasoil, heating oil, naphtha, natural gas),
  Biodiesel (includes FAME, UCOME, RME, HVO, SAF, POME, UCO, biofuel mandates).
- Map geography: Asia (Malaysia, Indonesia, China, India, Philippines, etc.),
  America (USA, Brazil, Argentina, Canada),
  EU (European countries, UK, Turkey),
  Africa (Nigeria, Kenya, Egypt, etc.).
  Use 'Global' as sub_geography when region-specific is not clear.
- For prices: extract the numeric value, unit, and context. Include price changes if mentioned.
- For sentiment: assess the tone (bullish/bearish/neutral), assign a theme tag, and rate confidence 0.0-1.0.
"""

# ── Document-type-specific prompts ────────────────────────────────────────────

BROKER_COMMENTARY = f"""You are extracting structured commodity market intelligence from a broker commentary document.
These documents are narrative-style reports from commodity brokers containing market views, price forecasts,
trade recommendations, and supply/demand commentary. They often embed specific price levels within prose.

Focus on:
- Price levels, targets, support/resistance levels mentioned in the text
- Broker's directional view (bullish/bearish) and rationale
- Supply/demand drivers mentioned (weather, policy, export pace, crush margins, etc.)
- Forward-looking statements and forecasts
- Cross-commodity comparisons and spread views

{_RULES}
{_SCHEMA}"""

OIL_WORLD = f"""You are extracting structured commodity market intelligence from an Oil World report.
Oil World is a benchmark reference for global oils and fats markets. These reports contain:
- Detailed supply/demand balance tables
- Production, export, import, and stock estimates
- Price assessments for multiple commodities across geographies
- Forecasts and revisions to prior estimates

Focus on:
- All price data in tables and text (spot prices, FOB/CIF values, forward prices)
- Supply/demand balance numbers (production, exports, imports, crushing, stocks)
- Revisions to estimates (up/down vs prior forecast)
- Key narratives about market direction
- Cross-geography comparisons

{_RULES}
{_SCHEMA}"""

ENERGY_BIOFUELS = f"""You are extracting structured commodity market intelligence from an energy/biofuels report.
These reports cover biodiesel, renewable fuels, energy markets, and their intersection with vegetable oils.

Focus on:
- Biodiesel prices (FAME, UCOME, RME, HVO, SAF, POME)
- Feedstock prices (UCO, tallow, palm oil, rapeseed, soybean oil)
- Energy prices (crude, gasoil, heating oil)
- Mandate/policy changes affecting biofuel demand
- Blending margins and arbitrage opportunities
- Regional demand patterns (EU mandates, US RFS, Indonesia B-series, etc.)

{_RULES}
{_SCHEMA}"""

# ── Classification prompt ─────────────────────────────────────────────────────

CLASSIFY_DOCUMENT = """Classify this document into one of these categories based on the first page text.
Reply with ONLY the category name, nothing else.

Categories:
- broker_commentary (narrative market commentary from a broker or trading house)
- oil_world (Oil World benchmark report with supply/demand tables)
- energy (energy, biofuels, biodiesel, or renewable fuels report)
- unknown (does not fit any category)

First page text:
{text}"""

# ── Prompt lookup ─────────────────────────────────────────────────────────────

PROMPTS = {
    "broker_commentary": BROKER_COMMENTARY,
    "oil_world": OIL_WORLD,
    "energy": ENERGY_BIOFUELS,
}
