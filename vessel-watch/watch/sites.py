import re
import uuid
from urllib.parse import urlsplit
from .model import key


def add_website(config, title, url, vessels=()):
    title, url = title.strip(), url.strip()
    p = urlsplit(url)
    if not title or p.scheme != "https" or not p.hostname or p.username or p.password:
        raise ValueError("Enter a website name and an HTTPS tracking link without embedded credentials.")
    if any(s["url"] == url for s in config["sites"]):
        raise ValueError("That tracking link is already saved. Select the existing website instead.")
    keys = [key(v) for v in vessels if key(v)]
    clashes = [v for v in keys if v in config.get("vessel_overrides", {})]
    if clashes:
        raise ValueError("These vessels already have an explicit website assignment: " + ", ".join(clashes))
    sid = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:30] or "website"
    if any(s["id"] == sid for s in config["sites"]):
        sid += "-" + uuid.uuid4().hex[:6]
    site = {"id":sid,"name":title,"url":url,"enabled":False,"parser":"generic",
            "vessel_patterns":[],"carrier_patterns":[],"steps":[],
            "validation":"Saved — search/table setup and a successful live test required"}
    config["sites"].append(site)
    for vessel in keys:
        config.setdefault("vessel_overrides", {})[vessel] = sid
    return site
