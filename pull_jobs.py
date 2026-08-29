import json
import os
import re
import subprocess
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin
from xml.etree import ElementTree as ET

CSV_FILE = "jobs.csv"
RESUME_FILE = "resume.json"
AUDIT_FILE = "job_audit.log"
FEEDS = [
    "https://weworkremotely.com/remote-jobs.rss",
    "https://himalayas.app/jobs/rss",
    "https://www.realworkfromanywhere.com/rss.xml",
    "https://rss.app/feeds/0bUVsJgEz3RTTsKm.xml"
]
BIOSPACE_BASE_URL = "https://jobs.biospace.com/jobs/"
BIOSPACE_PAGES_PER_QUERY = 2
AMGEN_SEARCH_URL = "https://careers.amgen.com/en/search-jobs"
AMGEN_PAGES_PER_QUERY = 1
BMS_SEARCH_API_URL = "https://jobs.bms.com/api/pcsx/search"
BMS_POSITION_API_URL = "https://jobs.bms.com/api/pcsx/position_details"
BMS_PAGES_PER_QUERY = 2
BMS_PAGE_SIZE = 10
LILLY_SEARCH_URL = "https://careers.lilly.com/us/en/search-results"
LILLY_PAGES_PER_QUERY = 1

# THE UNIVERSAL TIMESTAMP: One ID for the entire run
################################
current_run_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

# Initialize counters for logging
new_hits = 0
reviewed_jobs = 0
rejected_jobs = 0


def dedupe_keep_order(items):
    seen = set()
    result = []
    for item in items:
        if item is None:
            continue
        normalized = str(item).strip()
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered not in seen:
            seen.add(lowered)
            result.append(normalized)
    return result


def normalize_text(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def contains_phrase(text, phrase):
    text_norm = f" {normalize_text(text)} "
    phrase_norm = normalize_text(phrase)
    if not phrase_norm:
        return False
    return f" {phrase_norm} " in text_norm


def strip_html(text):
    if not text:
        return ""
    return " ".join(re.sub(r"<[^>]+>", " ", text).split())


def clean_text(text):
    return strip_html(text).replace(",", " ")


def fetch_url(url):
    return subprocess.check_output(
        f'curl -A "Mozilla/5.0" -sL "{url}"',
        shell=True,
        text=True
    )


def extract_jobposting_json_ld(html):
    matches = re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html,
        re.S | re.I
    )
    for raw in matches:
        try:
            data = json.loads(raw.strip())
        except Exception:
            continue

        candidates = data if isinstance(data, list) else [data]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("@type") == "JobPosting":
                return candidate
    return {}


def extract_meta_content(html, property_name):
    patterns = [
        rf'<meta[^>]+property="{re.escape(property_name)}"[^>]+content="([^"]*)"',
        rf'<meta[^>]+name="{re.escape(property_name)}"[^>]+content="([^"]*)"'
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.I)
        if match:
            return match.group(1)
    return ""


def extract_json_after_key(text, key):
    idx = text.find(key)
    if idx == -1:
        return None
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(text[idx + len(key):])
        return obj
    except Exception:
        return None


def extract_lilly_search_jobs(html):
    search_data = extract_json_after_key(html, '"eagerLoadRefineSearch":')
    if not isinstance(search_data, dict):
        return []
    data = search_data.get("data", {})
    jobs = data.get("jobs", [])
    return jobs if isinstance(jobs, list) else []


# Load Resume Skills, Constraints & Preferences
with open(RESUME_FILE, "r") as f:
    resume = json.load(f)

personal_info = resume.get("personal_info", {})
technical_skills = resume.get("technical_skills", {})
job_tracker = resume.get("job_tracker", {})

prefs = personal_info.get("preferences", {})
loc = personal_info.get("location", {})
my_city = loc.get("city", "").lower()
my_country = loc.get("country", "").lower()

skills = [s.lower() for s in technical_skills.get("skills", [])]
target_roles = [r.lower() for r in personal_info.get("target_roles", [])]
target_titles = [t.lower() for t in job_tracker.get("target_titles", target_roles)]
exclude_keywords = [k.lower() for k in job_tracker.get("exclude_keywords", [])]
target_companies = [c.lower() for c in job_tracker.get("target_companies", [])]
target_locations = [l.lower() for l in job_tracker.get("target_locations", [])]
title_keywords = [k.lower() for k in job_tracker.get("title_keywords", [])]
seniority_keywords = [k.lower() for k in job_tracker.get("seniority_keywords", [])]
biospace_queries = dedupe_keep_order(job_tracker.get("biospace_queries", title_keywords))
minimum_score = int(job_tracker.get("minimum_score", 1))

tracker_keyword_groups = [
    job_tracker.get("core_keywords", []),
    job_tracker.get("development_keywords", []),
    job_tracker.get("regulatory_keywords", []),
    job_tracker.get("toxicology_keywords", []),
    job_tracker.get("modality_keywords", []),
    job_tracker.get("specialty_keywords", [])
]
all_keywords = [k.lower() for group in tracker_keyword_groups for k in group] + skills + title_keywords
all_keywords = [k.lower() for k in dedupe_keep_order(all_keywords)]

normalized_target_titles = [normalize_text(t) for t in target_titles]
normalized_keywords = [normalize_text(k) for k in all_keywords]
normalized_exclude_keywords = [normalize_text(k) for k in exclude_keywords]
normalized_target_companies = [normalize_text(c) for c in target_companies]
normalized_target_locations = [normalize_text(l) for l in target_locations]
normalized_local_target_locations = [
    loc_name for loc_name in normalized_target_locations if loc_name not in {"remote", "anywhere", "wfh"}
]
normalized_title_keywords = [normalize_text(k) for k in title_keywords]
normalized_seniority_keywords = [normalize_text(k) for k in seniority_keywords]
normalized_my_city = normalize_text(my_city)
normalized_my_country = normalize_text(my_country)


def audit(status, title, link, details):
    safe_title = clean_text(title)
    detail_text = " | ".join(details) if details else "no details"
    with open(AUDIT_FILE, "a", encoding="utf-8") as f:
        f.write(f"{current_run_ts}\t{status}\t{safe_title}\t{link}\t{detail_text}\n")


def score_job(title, description, company=""):
    title_norm = normalize_text(title)
    desc_norm = normalize_text(description)
    company_norm = normalize_text(company)
    combined = " ".join(part for part in [title_norm, company_norm, desc_norm] if part).strip()

    reasons = []

    matched_excludes = [exclude for exclude in normalized_exclude_keywords if contains_phrase(title_norm, exclude)]
    if matched_excludes:
        reasons.append(f"excluded by keyword(s): {', '.join(matched_excludes[:5])}")
        return 0, "reject", reasons

    score = 0

    exact_title_hits = [target for target in normalized_target_titles if contains_phrase(title_norm, target)]
    broad_title_hits = [kw for kw in normalized_title_keywords if contains_phrase(title_norm, kw)]
    seniority_hits = [kw for kw in normalized_seniority_keywords if contains_phrase(title_norm, kw)]
    keyword_hits = [kw for kw in normalized_keywords if contains_phrase(combined, kw)]
    company_hits = [target for target in normalized_target_companies if contains_phrase(company_norm or combined, target)]

    if exact_title_hits:
        score += 45
        reasons.append(f"exact/near target title: {', '.join(exact_title_hits[:3])} (+45)")

    if broad_title_hits:
        score += 20
        reasons.append(f"domain title match: {', '.join(broad_title_hits[:4])} (+20)")

    if broad_title_hits and seniority_hits:
        score += 20
        reasons.append(f"seniority + domain title: {', '.join(seniority_hits[:3])} (+20)")
    elif seniority_hits and keyword_hits:
        score += 10
        reasons.append(f"senior role with relevant keywords: {', '.join(seniority_hits[:3])} (+10)")

    if keyword_hits:
        keyword_score = min(len(keyword_hits) * 6, 36)
        score += keyword_score
        reasons.append(f"keyword hits ({len(keyword_hits)}): {', '.join(keyword_hits[:6])} (+{keyword_score})")

    if company_hits:
        score += 10
        reasons.append(f"target company match: {', '.join(company_hits[:3])} (+10)")

    if score >= minimum_score:
        if exact_title_hits or (broad_title_hits and seniority_hits and len(keyword_hits) >= 3) or score >= max(minimum_score + 20, 70):
            match_tier = "strong"
        else:
            match_tier = "borderline"
    else:
        match_tier = "reject"

    reasons.append(f"match tier: {match_tier}")
    reasons.append(f"final score: {score}/{minimum_score}")
    return score, match_tier, reasons


# Fetch Existing Job Posting Links From the CSV
existing_links = set()
if os.path.exists(CSV_FILE):
    with open(CSV_FILE, "r", encoding="utf-8") as f:
        existing_links = {line.strip().split(",")[-1] for line in f if "," in line}

# Initialize CSV File if Doesn't Exist
if not os.path.exists(CSV_FILE):
    with open(CSV_FILE, "w", encoding="utf-8") as f:
        f.write("time,source,match_tier,score,title,company,location,description,link\n")

# Mark start of audit run
with open(AUDIT_FILE, "a", encoding="utf-8") as f:
    f.write(f"\n=== RUN {current_run_ts} ===\n")


def process_job(link, title_text, desc_text, source, company_text="", location_text=""):
    global new_hits, reviewed_jobs, rejected_jobs

    if not link or link in existing_links:
        return

    reviewed_jobs += 1

    location_context = normalize_text(" ".join([
        title_text or "",
        company_text or "",
        location_text or "",
        desc_text[:1500] if desc_text else ""
    ]))

    is_remote = any(re.search(fr'\b{word}\b', location_context) for word in ["remote", "anywhere", "wfh"])
    is_local = any(
        contains_phrase(location_context, place)
        for place in normalized_local_target_locations + [normalized_my_city, normalized_my_country]
        if place
    )

    regions = ["us", "usa", "united states", "uk", "united kingdom", "canada", "europe", "americas"]
    allowed_regions = set(filter(None, normalized_local_target_locations + [normalized_my_country]))
    lockouts = [f"{r} only" for r in regions if normalize_text(r) not in allowed_regions]
    lockouts += [f"remote {r}" for r in regions if normalize_text(r) not in allowed_regions]
    is_locked_out = any(lock in location_context for lock in lockouts)

    location_ok = False
    location_reasons = []
    if prefs.get("relocation", False):
        location_ok = True
        location_reasons.append("relocation allowed")
    else:
        if prefs.get("remote") and is_remote and not is_locked_out:
            location_ok = True
            location_reasons.append("remote match")
        if prefs.get("hybrid") and is_local:
            location_ok = True
            location_reasons.append("local/hybrid location match")
        if is_locked_out:
            location_reasons.append("locked out by region restriction")

    if not location_ok:
        rejected_jobs += 1
        audit("REJECT", title_text, link, [f"source: {source}"] + (location_reasons or ["location mismatch"]))
        return

    score, match_tier, score_reasons = score_job(title_text, desc_text, company_text)
    if score >= minimum_score:
        safe_title = clean_text(title_text)
        safe_company = clean_text(company_text)
        safe_location = clean_text(location_text)
        safe_desc = clean_text(desc_text)[:1500]

        with open(CSV_FILE, "a", encoding="utf-8") as f:
            f.write(
                f'{current_run_ts},{clean_text(source)},{match_tier},{score},{safe_title},{safe_company},{safe_location},{safe_desc},{link}\n'
            )

        audit("ACCEPT", title_text, link, [f"source: {source}"] + location_reasons + score_reasons)
        existing_links.add(link)
        new_hits += 1
    else:
        rejected_jobs += 1
        audit("REJECT", title_text, link, [f"source: {source}"] + location_reasons + score_reasons)


# Parse RSS URLs
for url in FEEDS:
    try:
        xml_raw = fetch_url(url)
        root = ET.fromstring(xml_raw)
        items = root.findall(".//{*}item")

        for item in items:
            link = item.findtext(".//{*}link")
            title_text = item.findtext(".//{*}title") or ""
            desc_text = item.findtext(".//{*}description") or item.findtext(".//{*}content") or ""
            process_job(link, title_text, desc_text, "rss")
    except Exception as e:
        audit("ERROR", url, url, [f"source: rss", f"feed parse failure: {e}"])
        continue


# Parse BioSpace search results for biotech/pharma roles
biospace_seen_links = set()
for query in biospace_queries:
    for page in range(1, BIOSPACE_PAGES_PER_QUERY + 1):
        try:
            page_path = "" if page == 1 else f"{page}/"
            search_url = f"{BIOSPACE_BASE_URL}{page_path}?keywords={quote_plus(query)}"
            html = fetch_url(search_url)
            raw_links = re.findall(r'href="\s*([^"]*/job/\d+/[^"]+)"', html, re.I)
            job_links = dedupe_keep_order(urljoin(search_url, link.strip()) for link in raw_links)

            for link in job_links:
                if link in biospace_seen_links or link in existing_links:
                    continue
                biospace_seen_links.add(link)

                detail_html = fetch_url(link)
                posting = extract_jobposting_json_ld(detail_html)

                title_text = posting.get("title") or extract_meta_content(detail_html, "og:title") or extract_meta_content(detail_html, "description")
                desc_text = strip_html(posting.get("description", "")) or extract_meta_content(detail_html, "og:description") or extract_meta_content(detail_html, "description")

                company = ""
                hiring_org = posting.get("hiringOrganization", {})
                if isinstance(hiring_org, dict):
                    company = hiring_org.get("name", "")

                location_parts = []
                job_locations = posting.get("jobLocation", [])
                if isinstance(job_locations, dict):
                    job_locations = [job_locations]
                for job_location in job_locations:
                    address = job_location.get("address", {}) if isinstance(job_location, dict) else {}
                    if isinstance(address, dict):
                        location_parts.extend([
                            address.get("addressLocality", ""),
                            address.get("addressRegion", ""),
                            address.get("addressCountry", "")
                        ])
                location_text = ", ".join(part for part in dedupe_keep_order(location_parts) if part)

                process_job(link, title_text, desc_text, f"biospace:{query}", company, location_text)
        except Exception as e:
            audit("ERROR", search_url, search_url, [f"source: biospace", f"query: {query}", f"search failure: {e}"])
            continue


# Parse direct Amgen search results
amgen_seen_links = set()
for query in biospace_queries:
    for page in range(1, AMGEN_PAGES_PER_QUERY + 1):
        try:
            search_url = f"{AMGEN_SEARCH_URL}?k={quote_plus(query)}"
            if page > 1:
                search_url += f"&pg={page}"
            html = fetch_url(search_url)
            raw_links = re.findall(r'href="\s*([^"]*/en/job/[^"]+)"', html, re.I)
            job_links = dedupe_keep_order(urljoin(search_url, link.strip()) for link in raw_links)

            for link in job_links:
                if link in amgen_seen_links or link in existing_links:
                    continue
                amgen_seen_links.add(link)

                detail_html = fetch_url(link)
                posting = extract_jobposting_json_ld(detail_html)

                title_text = posting.get("title") or extract_meta_content(detail_html, "og:title")
                desc_text = strip_html(posting.get("description", "")) or extract_meta_content(detail_html, "og:description") or extract_meta_content(detail_html, "description")

                company = ""
                hiring_org = posting.get("hiringOrganization", {})
                if isinstance(hiring_org, dict):
                    company = hiring_org.get("name", "")

                location_parts = []
                job_locations = posting.get("jobLocation", [])
                if isinstance(job_locations, dict):
                    job_locations = [job_locations]
                for job_location in job_locations:
                    address = job_location.get("address", {}) if isinstance(job_location, dict) else {}
                    if isinstance(address, dict):
                        location_parts.extend([
                            address.get("addressLocality", ""),
                            address.get("addressRegion", ""),
                            address.get("addressCountry", "")
                        ])
                location_text = ", ".join(part for part in dedupe_keep_order(location_parts) if part)

                process_job(link, title_text, desc_text, f"amgen:{query}", company or "Amgen", location_text)
        except Exception as e:
            audit("ERROR", search_url, search_url, [f"source: amgen", f"query: {query}", f"search failure: {e}"])
            continue


# Parse direct BMS search results via Eightfold API
bms_seen_links = set()
for query in biospace_queries:
    for page in range(BMS_PAGES_PER_QUERY):
        search_url = f"{BMS_SEARCH_API_URL}?domain=bms.com&query={quote_plus(query)}&location=&start={page * BMS_PAGE_SIZE}&"
        try:
            payload = json.loads(fetch_url(search_url))
            positions = payload.get("data", {}).get("positions", [])

            for position in positions:
                position_id = position.get("id")
                relative_link = position.get("positionUrl") or (f"/careers/job/{position_id}" if position_id else "")
                link = urljoin("https://jobs.bms.com", relative_link)
                if not position_id or link in bms_seen_links or link in existing_links:
                    continue
                bms_seen_links.add(link)

                detail_url = f"{BMS_POSITION_API_URL}?position_id={position_id}&domain=bms.com&hl=en"
                detail_payload = json.loads(fetch_url(detail_url))
                details = detail_payload.get("data", {})

                title_text = details.get("name") or position.get("name") or ""
                desc_text = strip_html(details.get("jobDescription", ""))
                company_text = "Bristol Myers Squibb"
                location_values = details.get("standardizedLocations") or details.get("locations") or position.get("standardizedLocations") or position.get("locations") or []
                location_text = ", ".join(dedupe_keep_order(location_values)) if isinstance(location_values, list) else str(location_values)

                process_job(link, title_text, desc_text, f"bms:{query}", company_text, location_text)
        except Exception as e:
            audit("ERROR", search_url, search_url, [f"source: bms", f"query: {query}", f"search failure: {e}"])
            continue


# Parse direct Lilly search results
lilly_seen_links = set()
for query in biospace_queries:
    for page in range(LILLY_PAGES_PER_QUERY):
        search_url = f"{LILLY_SEARCH_URL}?keywords={quote_plus(query)}"
        if page > 0:
            search_url += f"&from={page * 10}&s=1"
        try:
            search_html = fetch_url(search_url)
            jobs = extract_lilly_search_jobs(search_html)

            for job in jobs:
                apply_url = job.get("applyUrl", "")
                link = re.sub(r"/apply/?$", "", apply_url) if apply_url else ""
                if not link:
                    req_id = job.get("reqId") or job.get("jobId") or ""
                    title_slug = re.sub(r"[^a-z0-9]+", "-", (job.get("title") or "").lower()).strip("-")
                    if req_id:
                        link = f"https://careers.lilly.com/us/en/job/{req_id}/{title_slug}"
                if not link or link in lilly_seen_links or link in existing_links:
                    continue
                lilly_seen_links.add(link)

                title_text = job.get("title", "")
                company_text = "Eli Lilly and Company"
                location_text = job.get("location") or job.get("cityStateCountry") or ", ".join(job.get("multi_location", []))
                desc_text = job.get("descriptionTeaser", "")

                try:
                    detail_html = fetch_url(link)
                    posting = extract_jobposting_json_ld(detail_html)
                    title_text = posting.get("title") or title_text
                    desc_text = strip_html(posting.get("description", "")) or desc_text
                    hiring_org = posting.get("hiringOrganization", {})
                    if isinstance(hiring_org, dict):
                        company_text = hiring_org.get("name", company_text)
                    job_location = posting.get("jobLocation", {})
                    if isinstance(job_location, list) and job_location:
                        job_location = job_location[0]
                    if isinstance(job_location, dict):
                        address = job_location.get("address", {})
                        if isinstance(address, dict):
                            location_text = ", ".join(dedupe_keep_order([
                                address.get("addressLocality", ""),
                                address.get("addressRegion", ""),
                                address.get("addressCountry", "")
                            ])) or location_text
                except Exception as detail_error:
                    audit("ERROR", title_text or link, link, [f"source: lilly", f"detail fetch failure: {detail_error}"])

                process_job(link, title_text, desc_text, f"lilly:{query}", company_text, location_text)
        except Exception as e:
            audit("ERROR", search_url, search_url, [f"source: lilly", f"query: {query}", f"search failure: {e}"])
            continue


# STRATEGIC AUDIT PRINT
if new_hits > 0:
    print(f"Jobs: Found and added {new_hits} new listings after reviewing {reviewed_jobs} jobs.")
else:
    print(f"Jobs: No new matches found after reviewing {reviewed_jobs} jobs.")
print(f"Audit: {rejected_jobs} rejected. See {AUDIT_FILE} for acceptance/rejection reasons.")
