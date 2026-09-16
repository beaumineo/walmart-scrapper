import json
import urllib.parse
import urllib.request

q = """[out:json][timeout:25];
(
  nwr["name"~"Walmart",i](around:20000,34.0938877,-118.4112858);
);
out center 15;"""

data = urllib.parse.urlencode({"data": q}).encode()
req = urllib.request.Request(
    "https://overpass-api.de/api/interpreter",
    data=data,
    headers={
        "User-Agent": "WalmartDealFinder/1.0 (demo)",
        "Accept": "application/json",
    },
)

try:
    with urllib.request.urlopen(req, timeout=40) as r:
        j = json.load(r)
        print("count", len(j.get("elements", [])))
        for e in j.get("elements", [])[:10]:
            tags = e.get("tags", {})
            lat = e.get("lat") or (e.get("center") or {}).get("lat")
            lon = e.get("lon") or (e.get("center") or {}).get("lon")
            print(tags.get("name"), tags.get("addr:city"), lat, lon)
except Exception as ex:
    print("ERR", type(ex).__name__, ex)
