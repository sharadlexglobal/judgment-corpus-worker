#!/usr/bin/env python3
"""
Indian High Court judgment corpus worker.

Source : s3://indian-high-court-judgments (AWS Open Data, CC-BY-4.0)
Flow   : stream a bench-year tar over HTTP -> pdftotext -> extract Acts +
         Sections + Articles by pattern -> write one JSONL per tar to R2.

Disk-light by design: the tar is consumed as a stream, so a 2 GB archive
never lands on disk. Resumable: a tar whose output already exists on R2 is
skipped, so the worker can be restarted freely.
"""
import os, re, io, sys, json, time, gzip, tarfile, subprocess, collections
import urllib.request, urllib.error, hashlib
import boto3
from botocore.config import Config

S3_PUBLIC = "https://indian-high-court-judgments.s3.ap-south-1.amazonaws.com/"
COURTS    = [c.strip() for c in os.environ.get("COURTS", "7_26").split(",") if c.strip()]
YEAR_FROM = int(os.environ.get("YEAR_FROM", "2000"))
YEAR_TO   = int(os.environ.get("YEAR_TO", "2026"))
OUT_PREFIX= os.environ.get("OUT_PREFIX", "judgment-corpus/v1")
R2_BUCKET = os.environ.get("R2_BUCKET", "sharadmcp")
PDFTOTEXT = os.environ.get("PDFTOTEXT", "pdftotext")
PROGRESS  = int(os.environ.get("PROGRESS_EVERY", "500"))

def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)

r2 = boto3.client(
    "s3",
    endpoint_url=os.environ["R2_ENDPOINT"],
    aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    config=Config(retries={"max_attempts": 5, "mode": "standard"}),
)

# ---------- extraction ----------
# Greedy on the leading capitalised words so "Prevention of Corruption Act"
# is captured whole rather than truncated to "Corruption Act".
ACT_RE = re.compile(
    r'\b((?:[A-Z][A-Za-z&./\'\-]*\s+|of\s+|and\s+|the\s+|for\s+|from\s+)*'
    r'(?:Act|Code|Ordinance))\b(?:[,\s]*(1[6-9]\d{2}|20\d{2}))?')
# Advocates overwhelmingly write the abbreviation, not the short title —
# "IPC" and "Cr.P.C." outnumber "Indian Penal Code" in real judgments, so an
# Act/Code/Ordinance-only pattern silently loses most criminal citations.
ABBR_RE = re.compile(
    r'\b('
    r'I\.?P\.?C\.?|Cr\.?\s?P\.?\s?C\.?|C\.?P\.?C\.?|N\.?I\.?\s?Act|'
    r'NDPS|POCSO|D\.?V\.?\s?Act|PMLA|SARFAESI|FEMA|RERA|IBC|MSMED|'
    r'P\.?C\.?\s?Act|TADA|UAPA|MCOCA|JJ\s?Act|SC/ST\s?Act|MV\s?Act|CGST|IGST'
    r')\b(?![a-z])')
ABBR_MAP = {
    'ipc': 'Indian Penal Code', 'crpc': 'Code of Criminal Procedure',
    'cpc': 'Code of Civil Procedure', 'niact': 'Negotiable Instruments Act',
    'ndps': 'NDPS Act', 'pocso': 'POCSO Act',
    'dvact': 'Protection of Women from Domestic Violence Act',
    'pmla': 'Prevention of Money Laundering Act', 'sarfaesi': 'SARFAESI Act',
    'fema': 'Foreign Exchange Management Act', 'rera': 'Real Estate (Regulation and Development) Act',
    'ibc': 'Insolvency and Bankruptcy Code', 'msmed': 'MSMED Act',
    'pcact': 'Prevention of Corruption Act', 'tada': 'TADA', 'uapa': 'UAPA',
    'mcoca': 'MCOCA', 'jjact': 'Juvenile Justice Act', 'scstact': 'SC/ST Act',
    'mvact': 'Motor Vehicles Act', 'cgst': 'CGST Act', 'igst': 'IGST Act',
}

SEC_RE = re.compile(
    r'\b(?:[Ss]ections?|[Ss]ecs?\.|u/s|U/[Ss])\s*'
    r'([0-9]{1,4}[A-Z]{0,3}(?:\s*\(\s*[0-9a-zA-Z]{1,3}\s*\))*)')
ART_RE = re.compile(r'\bArticles?\s+(\d{1,3}[A-Z]?)\b')
STOP = re.compile(
    r'^(?:above|said|aforesaid|present|impugned|same|this|that|new|old|principal|'
    r'parent|amending|relevant|instant|subject|chapter|part|schedule|clause|rule|'
    r'section|proviso|sub-section|explanation|order)\b', re.I)
LEAD = re.compile(r'^(?:the|and|or|under|of|in|by|per|for|from|to|said|a|an)\s+', re.I)

def clean_act(name, year):
    n = re.sub(r'\s+', ' ', name).strip(" .,;:")
    prev = None
    while prev != n:                       # strip connectives until stable
        prev = n
        n = LEAD.sub('', n).strip(" .,;:")
    if STOP.match(n):
        return None
    w = n.split()
    if len(w) < 2:                         # "Act" / "Code" alone carries no meaning
        return None
    if not w[0][:1].isupper():             # a real short title starts capitalised
        return None
    if len(w) > 9:
        w = w[-9:]
    n = " ".join(w)
    if len(n) < 8:
        return None
    return f"{n}, {year}" if year else n

def parse(text):
    acts = collections.Counter()
    for m in ACT_RE.finditer(text):
        c = clean_act(m.group(1), m.group(2))
        if c:
            acts[c] += 1
    for m in ABBR_RE.finditer(text):
        key = re.sub(r'[^a-z]', '', m.group(1).lower())
        full = ABBR_MAP.get(key)
        if full:
            acts[full] += 1
    secs = {re.sub(r'\s+', '', s) for s in SEC_RE.findall(text)}
    arts = {"Art." + a for a in ART_RE.findall(text)}
    return acts, sorted(secs | arts)

# ---------- what kind of document is this? ----------
# Length alone is a poor guide: a 3,400-character order can be a reasoned
# judgment, and a 19,000-character one can be a pure listing order. The text
# itself says which — a judgment is reserved and pronounced, is delivered by a
# Judge (never a Registrar), is written in numbered paragraphs, and reasons.
RESERVED  = re.compile(r'RESERVED\s+ON|PRONOUNCED\s+ON|DATE\s+OF\s+RESERV|'
                       r'Judgment\s+reserved|Judgment\s+delivered|DATE\s+OF\s+DECISION', re.I)
HDR_JUDG  = re.compile(r'^\s*(?:JUDGMENT|J\s*U\s*D\s*G\s*M\s*E\s*N\s*T)\s*$', re.I | re.M)
REGISTRAR = re.compile(r'CORAM:.{0,80}REGISTRAR', re.I | re.S)
JUSTICE   = re.compile(r"HON'?BLE\s+(?:MR\.?|MS\.?|MRS\.?|DR\.?)?\s*JUSTICE", re.I)
PARA_RE   = re.compile(r'^\s{0,6}(\d{1,3})\.\s', re.M)
PAGES_RE  = re.compile(r'Page\s+\d+\s+of\s+(\d+)', re.I)
REASON_RE = re.compile(r'\b(?:we are of the (?:considered )?(?:view|opinion)|'
                       r'in our (?:considered )?(?:view|opinion)|it is well settled|held that|'
                       r'the ratio|laid down in|relied upon|having considered|'
                       r'for the foregoing reasons|in the light of the above)\b', re.I)
PRECED_RE = re.compile(r'\(\d{4}\)\s*\d+\s*SCC\b|\bAIR\s+\d{4}\s+SC\b|\b\d{4}\s+SCC\s+\d+', re.I)
LISTING_RE= re.compile(r'\b(?:list (?:the matter|it) (?:on|before)|renotify|re-notify|adjourn|'
                       r'stands? over|next date of hearing|for arguments on)\b', re.I)

def doc_score(t):
    paras = [int(m.group(1)) for m in PARA_RE.finditer(t)]
    pages = [int(m.group(1)) for m in PAGES_RE.finditer(t)]
    max_para = max(paras) if paras else 0
    npages   = max(pages) if pages else 0
    reasoning  = len(REASON_RE.findall(t))
    precedents = len(PRECED_RE.findall(t))
    listing    = len(LISTING_RE.findall(t))
    s = 0
    if REGISTRAR.search(t):  s -= 6
    if RESERVED.search(t):   s += 5
    if HDR_JUDG.search(t):   s += 4
    if JUSTICE.search(t):    s += 1
    if max_para >= 10:       s += 3
    elif max_para >= 5:      s += 1
    if npages >= 10:         s += 2
    elif npages >= 5:        s += 1
    s += min(reasoning, 4) + min(precedents, 3) - min(listing, 3)
    if len(t) < 2000:        s -= 3
    elif len(t) > 20000:     s += 2
    kind = "judgment" if s >= 6 else ("order" if s >= 1 else "listing")
    return kind, s, {"para": max_para, "pages": npages, "reason": reasoning,
                     "prec": precedents, "listing": listing,
                     "reserved": bool(RESERVED.search(t))}

# ---------- s3 helpers ----------
def list_keys(prefix):
    """List the public bucket without credentials."""
    keys, token = [], None
    while True:
        url = f"{S3_PUBLIC}?list-type=2&prefix={urllib.parse.quote(prefix)}&max-keys=1000"
        if token:
            url += "&continuation-token=" + urllib.parse.quote(token)
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "judgment-corpus/1.0"}),
                timeout=90) as r:
            xml = r.read().decode("utf8", "ignore")
        keys += re.findall(r"<Key>([^<]+)</Key>", xml)
        m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", xml)
        if not m:
            break
        token = m.group(1)
    return keys

def r2_exists(key):
    try:
        r2.head_object(Bucket=R2_BUCKET, Key=key)
        return True
    except Exception:
        return False

# ---------- main ----------
def process_tar(tar_key):
    out_key = f"{OUT_PREFIX}/{tar_key.replace('data/tar/','').replace('/data.tar','')}.jsonl.gz"
    if r2_exists(out_key):
        log("SKIP (done):", tar_key)
        return 0, collections.Counter()
    buf, acts_all = io.BytesIO(), collections.Counter()
    kinds = collections.Counter()
    gz = gzip.GzipFile(fileobj=buf, mode="wb")
    n = empty = 0
    nbytes = 0
    t0 = time.time()
    req = urllib.request.Request(S3_PUBLIC + urllib.parse.quote(tar_key),
                                 headers={"User-Agent": "judgment-corpus/1.0"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        tf = tarfile.open(fileobj=resp, mode="r|")
        for m in tf:
            if not m.isfile() or not m.name.lower().endswith(".pdf"):
                continue
            data = tf.extractfile(m).read()
            nbytes += len(data)
            try:
                p = subprocess.run([PDFTOTEXT, "-q", "-", "-"], input=data,
                                   capture_output=True, timeout=120)
                txt = p.stdout.decode("utf8", "ignore")
            except Exception as e:
                txt = ""
            if len(txt) < 200:
                empty += 1
            acts, secs = parse(txt)
            acts_all.update(acts)
            kind, score, sig = doc_score(txt)
            kinds[kind] += 1
            gz.write((json.dumps({
                "file": os.path.basename(m.name),
                "chars": len(txt),
                "kind": kind,
                "score": score,
                "sig": sig,
                "thash": hashlib.sha1(re.sub(r"\s+", " ", txt).strip().lower()
                                      .encode()).hexdigest()[:16],
                "acts": acts.most_common(10),
                "secs": secs[:60],
            }, ensure_ascii=False) + "\n").encode())
            n += 1
            if n % PROGRESS == 0:
                el = time.time() - t0
                log(f"   {tar_key.split('year=')[1][:20]} {n} docs {n/el:.0f}/s "
                    f"{nbytes/1e6:.0f}MB empty={empty}")
    gz.close()
    body = buf.getvalue()
    r2.put_object(Bucket=R2_BUCKET, Key=out_key, Body=body,
                  ContentType="application/gzip")
    el = time.time() - t0
    log(f"OK {tar_key} -> {out_key} | {n} docs {el:.0f}s {n/max(el,1):.1f}/s "
        f"empty={empty} out={len(body)/1e6:.1f}MB | "
        f"judgments={kinds['judgment']} orders={kinds['order']} listings={kinds['listing']}")
    return n, acts_all

def main():
    log("worker start | courts:", COURTS, "| years:", YEAR_FROM, "-", YEAR_TO)
    tars = []
    for court in COURTS:
        for year in range(YEAR_FROM, YEAR_TO + 1):
            tars += [k for k in list_keys(f"data/tar/year={year}/court={court}/")
                     if k.endswith("/data.tar")]
    log(f"{len(tars)} bench-year archives queued")
    total, grand = 0, collections.Counter()
    for i, t in enumerate(tars, 1):
        try:
            n, acts = process_tar(t)
            total += n
            grand.update(acts)
        except Exception as e:
            log("ERROR on", t, type(e).__name__, str(e)[:200])
        if i % 5 == 0 or i == len(tars):
            r2.put_object(Bucket=R2_BUCKET,
                          Key=f"{OUT_PREFIX}/_act_frequency.json",
                          Body=json.dumps(grand.most_common(3000), ensure_ascii=False,
                                          indent=1).encode(),
                          ContentType="application/json")
            log(f"progress {i}/{len(tars)} archives | {total} docs | "
                f"{len(grand)} distinct acts")
    log("ALL DONE | docs:", total, "| distinct acts:", len(grand))
    for k, v in grand.most_common(40):
        log(f"   {v:7d}  {k}")

if __name__ == "__main__":
    import urllib.parse
    main()
