import json
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "WalmartDealFinder/1.0 (local-demo; contact@example.com)"}


def get(url: str):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


# Geocode zip
zip_code = "90210"
geo = get(
    "https://nominatim.openstreetmap.org/search?"
    + urllib.parse.urlencode(
        {"postalcode": zip_code, "country": "US", "format": "json", "limit": 1}
    )
)
print("geo", geo[0]["lat"], geo[0]["lon"], geo[0]["display_name"])
lat, lon = float(geo[0]["lat"]), float(geo[0]["lon"])
time.sleep(1.1)

# Search Walmart near that point
q = urllib.parse.urlencode(
    {
        "q": "Walmart",
        "format": "json",
        "limit": 20,
        "countrycodes": "us",
        "viewbox": f"{lon-0.4},{lat+0.4},{lon+0.4},{lat-0.4}",
        "bounded": 1,
        "addressdetails": 1,
        "extratags": 1,
    }
)
stores = get("https://nominatim.openstreetmap.org/search?" + q)
print("stores", len(stores))
for s in stores[:10]:
    addr = s.get("address") or {}
    print(
        "-",
        s.get("display_name")[:100],
        "|",
        s.get("lat"),
        s.get("lon"),
        "|",
        addr.get("postcode"),
        (s.get("extratags") or {}).get("brand"),
    )
