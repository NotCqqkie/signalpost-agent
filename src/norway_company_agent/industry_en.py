"""English labels for Norwegian SN2025 (NACE Rev. 2.1) industry codes, for plain-language synthesis.

`CODE_EN` covers the 120 most frequent five-digit codes (less 00.000 "unspecified") in the frozen 2025-filer universe (~87% of entities),
translated from the register's Norwegian label. `DIVISION_EN` covers every two-digit division. A code outside
`CODE_EN` falls back to its division label; the register's Norwegian label is always shown alongside.
"""
from __future__ import annotations

from typing import Any

CODE_EN = {
    "68.200": "renting and operating of own or leased real estate", "97.001": "housing cooperative or condominium activities",
    "70.200": "business and management consultancy", "64.323": "other own-account investment (holding/investment company)",
    "68.120": "development and sale of building projects", "68.110": "buying and selling of own real estate",
    "41.000": "construction of buildings", "71.129": "other technical consultancy", "62.100": "computer programming",
    "56.110": "restaurants", "62.200": "IT consultancy and IT systems management", "43.120": "site preparation and groundwork",
    "49.410": "freight transport by road", "68.320": "real estate management on a fee or contract basis",
    "71.121": "building engineering consultancy", "47.110": "general retail, mainly food (grocery stores)",
    "95.310": "repair and maintenance of motor vehicles", "69.202": "bookkeeping and accounting", "43.210": "electrical installation",
    "43.221": "plumbing", "96.210": "hairdressing and barbering", "47.810": "retail sale of motor vehicles",
    "94.991": "foundations supporting charitable and public-benefit purposes", "03.110": "marine fishing", "73.110": "advertising agency",
    "43.320": "joinery installation (carpentry)", "47.710": "retail sale of clothing", "85.597": "other education",
    "85.100": "pre-primary education and kindergartens", "46.640": "wholesale of other machinery and equipment",
    "86.230": "dental practice", "86.210": "general medical practice", "69.100": "legal services",
    "74.110": "industrial, product and fashion design", "74.990": "other professional, scientific and technical activities",
    "59.110": "film, video and television production", "71.110": "architecture", "81.100": "combined facilities support services",
    "43.340": "painting and glazing", "55.100": "hotels", "78.200": "temporary staffing and other HR services",
    "46.830": "wholesale of timber, building materials and sanitary equipment", "86.993": "other human health services",
    "81.210": "cleaning of buildings", "46.490": "wholesale of other household goods",
    "90.390": "other arts and entertainment support services", "86.221": "specialist medical practice (non-psychiatric)",
    "77.320": "renting of construction machinery and equipment", "85.510": "sports and recreation education",
    "47.520": "retail sale of hardware, building materials, paints and glass", "33.120": "repair and maintenance of machinery",
    "50.201": "international (foreign-going) cargo shipping", "94.992": "other membership organisations",
    "72.100": "research and development in natural sciences and engineering", "47.631": "retail sale of sporting goods",
    "43.990": "other specialised construction", "86.950": "physiotherapy and occupational therapy",
    "79.121": "excursion and event organisers", "46.500": "wholesale of information and communication equipment",
    "93.130": "fitness centres", "74.120": "graphic and visual communication design", "96.220": "beauty treatment",
    "35.121": "hydroelectric power generation", "43.410": "roofing", "55.200": "hostels and holiday apartments",
    "47.300": "retail sale of motor fuel", "74.200": "photography", "46.900": "non-specialised wholesale trade",
    "77.390": "renting of other machinery and equipment", "90.201": "music performance and entertainment",
    "25.530": "metal working (machining)", "82.990": "other business support services", "59.200": "music and sound recording and publishing",
    "56.210": "event catering", "85.531": "driving schools", "74.130": "interior design", "86.960": "traditional, complementary and alternative medicine",
    "47.559": "retail sale of other furnishing articles", "49.330": "on-demand passenger transport (taxi)",
    "46.630": "wholesale of mining, oil and gas and construction machinery", "46.340": "wholesale of beverages",
    "46.420": "wholesale of clothing and footwear", "75.000": "veterinary services", "47.120": "other non-specialised retail",
    "46.460": "wholesale of pharmaceutical and medical goods", "93.110": "operation of sports facilities",
    "47.820": "retail sale of motor vehicle parts and accessories", "47.761": "retail sale of flowers and plants",
    "68.310": "real estate agency and brokerage", "43.222": "refrigeration, heat pump and fireplace installation", "69.201": "auditing",
    "33.150": "repair and maintenance of ships and boats", "47.780": "other retail sale of new goods",
    "78.100": "recruitment and placement of workers", "55.300": "campsites", "10.710": "bakery (bread and fresh pastry)",
    "58.290": "other software publishing", "43.910": "masonry and bricklaying", "56.300": "bars",
    "77.110": "renting of cars and light vehicles", "86.992": "preventive health care", "73.300": "public relations and communication",
    "47.740": "retail sale of medical and orthopaedic goods", "47.551": "retail sale of furniture", "96.990": "other personal services",
    "81.300": "landscape and garden services", "47.270": "other retail sale of food", "47.750": "retail sale of cosmetics and toiletries",
    "43.330": "floor and wall covering", "64.312": "alternative investment funds (not securities funds)",
    "02.400": "support services to forestry", "47.510": "retail sale of textiles", "96.230": "day spa, sauna and steam bath",
    "93.190": "other sports activities", "31.000": "manufacture of furniture", "86.930": "psychology and psychotherapy (non-medical)",
    "93.291": "adventure and experience activities", "46.470": "wholesale of furniture, carpets and lighting",
    "47.770": "retail sale of watches, clocks and jewellery",
}

DIVISION_EN = {
    "01": "crop and animal production", "02": "forestry and logging", "03": "fishing and aquaculture", "05": "coal mining",
    "06": "extraction of crude petroleum and natural gas", "07": "mining of metal ores", "08": "other mining and quarrying",
    "09": "mining support services", "10": "food manufacturing", "11": "beverage manufacturing", "12": "tobacco products",
    "13": "textile manufacturing", "14": "clothing manufacturing", "15": "leather products", "16": "wood products",
    "17": "paper products", "18": "printing", "19": "refined petroleum products", "20": "chemicals", "21": "pharmaceuticals",
    "22": "rubber and plastic products", "23": "non-metallic mineral products (concrete, glass, stone)", "24": "basic metals",
    "25": "fabricated metal products", "26": "computer, electronic and optical products", "27": "electrical equipment",
    "28": "machinery and equipment", "29": "motor vehicles and trailers", "30": "ships, boats and other transport equipment",
    "31": "furniture", "32": "other manufacturing", "33": "repair and installation of machinery and equipment",
    "35": "electricity, gas, heating and cooling supply", "36": "water supply", "37": "sewerage",
    "38": "waste collection, treatment and recycling", "39": "remediation", "41": "construction of buildings",
    "42": "civil engineering", "43": "specialised construction trades", "46": "wholesale trade", "47": "retail trade",
    "49": "land transport", "50": "water transport (shipping)", "51": "air transport", "52": "warehousing and transport support",
    "53": "postal and courier services", "55": "accommodation", "56": "food and beverage service",
    "58": "publishing (including software)", "59": "film, video, TV and music production", "60": "broadcasting and content distribution",
    "61": "telecommunications", "62": "computer programming and IT consultancy", "63": "computing infrastructure and information services",
    "64": "financial services and holding companies", "65": "insurance and pension funding",
    "66": "services auxiliary to finance and insurance", "68": "real estate", "69": "legal and accounting services",
    "70": "head offices and management consultancy", "71": "architecture, engineering and technical testing",
    "72": "scientific research and development", "73": "advertising, market research and public relations",
    "74": "other professional and technical services (design, photography and more)", "75": "veterinary services",
    "77": "rental and leasing", "78": "employment and staffing services", "79": "travel agencies, tour operators and events",
    "80": "security and investigation", "81": "building services, cleaning and landscaping", "82": "office and business support services",
    "84": "public administration", "85": "education", "86": "human health services", "87": "residential care",
    "88": "social work without accommodation", "90": "arts and entertainment", "91": "libraries, archives, museums and culture",
    "92": "gambling and betting", "93": "sports, amusement and recreation", "94": "membership organisations and foundations",
    "95": "repair of motor vehicles, computers and household goods", "96": "personal services",
    "97": "housing cooperatives and condominiums", "98": "household production for own use", "99": "extraterritorial organisations",
}


def english_industry(code: Any) -> tuple[str | None, str]:
    """(English label, precision) for a code: precision is "code", "division" or "none"."""
    c = str(code or "").strip()
    if c in CODE_EN:
        return CODE_EN[c], "code"
    if len(c) >= 2 and c[:2] in DIVISION_EN and c[:2] != "00":
        return DIVISION_EN[c[:2]], "division"
    return None, "none"
