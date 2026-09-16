import httpx

c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=300)
print("health", c.get("/health").json())
print("fetching stores for 90810 (street geocode on first run)...")
s = c.get(
    "/api/stores", params={"zip": "90810", "radius_miles": 25, "limit": 15}
).json()
print("count", s["count"])
for x in s["stores"]:
    mark = ""
    if x.get("store_id") == "4101" or "South St" in (x.get("address") or ""):
        mark = "  <== target"
    print(
        f"{x['distance_miles']:>5} mi  #{x['store_id']}  "
        f"{x['address']}, {x['city']}{mark}"
    )
