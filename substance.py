"""
Does this document carry law, whatever it is called?

The old three-way stamp leaned on "reserved and pronounced", which a same-day
quashing order under s.482 never carries — and that is a large part of a High
Court's real criminal work. So the stamp is kept, but four independent marks
are added that say what is INSIDE the document, letting a reader filter on
substance instead of trusting one label.
"""
import re

CITES_PRECEDENT = re.compile(
    r'\b(?:\(\d{4}\)\s*\d+\s*SCC|\bAIR\s+\d{4}\s+(?:SC|SCC)|\d{4}\s+SCC\s+\d+|'
    r'\b\d{4}\s*\(\d+\)\s*(?:SCC|SCALE|DLT|Crimes)|'
    r'(?:Hon\'?ble\s+)?Supreme\s+Court\s+(?:has\s+)?(?:held|observed|laid\s+down|set\s+down)|'
    r'\btitled\s+[A-Z][A-Za-z.\s]+\s+v(?:s|\.)\.?\s+[A-Z]|'
    r'\brelied\s+(?:up)?on\s+(?:the\s+)?(?:decision|judgment|ratio)|'
    r'\bin\s+[A-Z][A-Za-z.&\s]{2,40}\s+v(?:s|\.)\.?\s+[A-Z][A-Za-z.&\s]{2,40},?\s*(?:the\s+)?(?:Supreme\s+Court|this\s+Court)\b)', re.I)

INTERPRETS_LAW = re.compile(
    r'\b(?:the\s+)?(?:pre-?condition|proviso|ingredients?|scope|ambit|purport|'
    r'true\s+construction|plain\s+language|legislative\s+intent)\s+(?:of|to|for)\b|'
    r'\b(?:section|s\.)\s*\d+[A-Z]?(?:\(\d+\))?\s+(?:contemplates|envisages|requires|'
    r'postulates|mandates|permits|does\s+not\s+permit|has\s+to\s+be\s+read)\b|'
    r'\bcannot\s+be\s+sustained\b|\bis\s+bad\s+in\s+law\b|\bwithout\s+jurisdiction\b|'
    r'\bmere\s+change\s+of\s+opinion\b|\bprinciples?\s+(?:by\s+which|for\s+exercise\s+of)\b', re.I)

GRANTS_RELIEF = re.compile(
    r'\b(?:are|is)\s+(?:hereby\s+)?quashed\b|\bstands?\s+quashed\b|'
    r'\b(?:petition|appeal|application|revision)\s+is\s+(?:allowed|dismissed|disposed)\b|'
    r'\bset\s+aside\b|\bbail\s+is\s+(?:granted|rejected)\b|\bconviction\s+is\s+(?:upheld|set\s+aside)\b|'
    r'\bdelay\s+.{0,20}is\s+condoned\b|\bdirected\s+to\b', re.I)

PURE_ADJOURNMENT = re.compile(
    r'\b(?:list|re-?list|renotify|re-?notify)\s+(?:the\s+)?(?:matter|case|it)\b|'
    r'\bnext\s+date\s+of\s+hearing\b|\bstands?\s+adjourned\b|\bfor\s+arguments\s+on\b', re.I)

REGISTRAR = re.compile(r'CORAM:.{0,80}REGISTRAR', re.I | re.S)

def marks(text):
    """Four independent marks — a reader can filter on any of them."""
    t = text or ""
    has_prec = bool(CITES_PRECEDENT.search(t))
    has_law  = bool(INTERPRETS_LAW.search(t))
    has_reli = bool(GRANTS_RELIEF.search(t))
    adjourn  = bool(PURE_ADJOURNMENT.search(t)) and len(t) < 4000
    registrar= bool(REGISTRAR.search(t))
    # "substantive" = the court actually applied law to reach a result
    substantive = (has_prec or has_law) and has_reli and not registrar
    return {"cites_precedent": has_prec, "interprets_law": has_law,
            "grants_relief": has_reli, "mere_adjournment": adjourn,
            "by_registrar": registrar, "substantive": substantive}

if __name__ == "__main__":
    import subprocess, urllib.request
    S3="https://indian-high-court-judgments.s3.ap-south-1.amazonaws.com/data/pdf/year=2017/court=7_26/bench=dhcdb/"
    tests=[("aadesh-1 (498A FIR quash)","DLHC013198022017_1_2017-07-31.pdf"),
           ("aadesh-2 (Income Tax notice)","DLHC011820072009_1_2017-01-10.pdf"),
           ("aadesh-3 (482 + Narinder Singh)","DLHC011337832017_1_2017-05-19.pdf"),
           ("parchi (listing)","DLHC012771932017_1_2017-08-04.pdf")]
    for name,f in tests:
        d=urllib.request.urlopen(S3+f,timeout=60).read()
        t=subprocess.run(["pdftotext","-q","-","-"],input=d,capture_output=True).stdout.decode("utf8","ignore")
        m=marks(t)
        flags=" ".join(k for k,v in m.items() if v and k!="substantive")
        print(f"{name:34} substantive={str(m['substantive']):5}  [{flags}]")
