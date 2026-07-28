"""
Order and Rule — how the Civil Procedure Code is actually cited.

Practitioners do not plead "section 151 CPC" and stop there; the working parts
of the Code are its Orders and Rules — Order VII Rule 11, Order XXXIX Rules 1
and 2 — written in Roman numerals. A section-only reader misses the entire
civil side. Same for the Delhi High Court (Original Side) Rules and the several
Acts that carry Schedules.
"""
import re

ROMAN = {"i":1,"ii":2,"iii":3,"iv":4,"v":5,"vi":6,"vii":7,"viii":8,"ix":9,"x":10,
         "xi":11,"xii":12,"xiii":13,"xiv":14,"xv":15,"xvi":16,"xvii":17,"xviii":18,
         "xix":19,"xx":20,"xxi":21,"xxii":22,"xxiii":23,"xxiv":24,"xxv":25,"xxvi":26,
         "xxvii":27,"xxviii":28,"xxix":29,"xxx":30,"xxxi":31,"xxxii":32,"xxxiii":33,
         "xxxiv":34,"xxxv":35,"xxxvi":36,"xxxvii":37,"xxxviii":38,"xxxix":39,"xl":40,
         "xli":41,"xlii":42,"xliii":43,"xliv":44,"xlv":45,"xlvi":46,"xlvii":47,
         "xlviii":48,"xlix":49,"l":50,"li":51}

def _num(tok):
    t = tok.strip().lower().rstrip('.')
    if t.isdigit(): return int(t)
    return ROMAN.get(t)

# "Order XXXVII Rule 3", "Order 7 Rule 11(a)", "Order VI Rules 17 and 18"
ORDER_RULE = re.compile(
    r'\bOrder[\s\-]*([IVXLivxl]{1,7}|\d{1,2})\s*(?:of\s+(?:the\s+)?CPC\s*)?[,\s]*'
    r'Rules?[\s\-]*([0-9]{1,3}[A-Z]?(?:\s*\(\s*[0-9a-z]{1,3}\s*\))*'
    r'(?:\s*(?:,|and|&|to)\s*[0-9]{1,3}[A-Z]?)*)', re.I)

# "Order XXXVII CPC" — the Order alone, no Rule
ORDER_ONLY = re.compile(
    r'\bOrder[\s\-]*([IVXLivxl]{1,7}|\d{1,2})\b(?!\s*[,\s]*Rules?)'
    r'(?=[^.]{0,24}\b(?:CPC|C\.P\.C|Code of Civil Procedure)\b)', re.I)

SCHEDULE = re.compile(
    r'\b(First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth|Ninth|Tenth|[IVX]{1,4})\s+Schedule\b', re.I)

def _rules(blob):
    out = []
    for part in re.split(r',|\band\b|&|to', blob):
        p = re.sub(r'\s+', '', part).strip(' .')
        if re.match(r'^\d', p): out.append(p)
    return out

def order_rules(text):
    """['O7R11', 'O39R1', 'O37'] — one token per Order/Rule pleaded."""
    found = set()
    for o, rules in ORDER_RULE.findall(text):
        n = _num(o)
        if not n: continue
        for r in _rules(rules):
            found.add(f"O{n}R{r}")
    for o in ORDER_ONLY.findall(text):
        n = _num(o)
        if n and not any(t.startswith(f"O{n}R") for t in found):
            found.add(f"O{n}")
    return sorted(found, key=lambda s: (int(re.match(r'O(\d+)', s).group(1)), s))

def schedules(text):
    return sorted({f"Sch.{m.group(1).title()}" for m in SCHEDULE.finditer(text)})

if __name__ == "__main__":
    t = ("This is an application under Order XXXVII Rule 3 CPC read with Order XXXVIII Rule 5. "
         "The defendant moved under Order VII Rule 11(d) and Order VI Rules 17 and 18. "
         "A summary suit under Order XXXVII CPC was filed. Order 39 Rule 1 & 2 was pressed. "
         "See also the Second Schedule.")
    print("  Order/Rule :", order_rules(t))
    print("  Schedule   :", schedules(t))
