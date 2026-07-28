"""
Bind a section to the Act it was written with.

A judgment routinely cites several statutes, so collecting all its sections
into one list and pairing that list with every Act invents things nobody said:
"section 498A of the DV Act". Sections are therefore read from the phrase that
carries them — "Sections 12 and 18 of the DV Act" — and only attached to that
Act. Sections with no Act named nearby are kept separately as unbound, never
guessed at.
"""
import re

# "Sections 12, 18 and 20 of the Protection of Women from Domestic Violence Act, 2005"
BOUND = re.compile(
    r'(?:[Ss]ections?|[Ss]ecs?\.|[Uu]/[Ss]\.?)\s*'
    r'([0-9]{1,4}-?[A-Z]{0,3}(?:\s*\(\s*[0-9a-zA-Z]{1,3}\s*\))*'
    r'(?:\s*(?:,|and|&|to|/)\s*[0-9]{1,4}-?[A-Z]{0,3}(?:\s*\(\s*[0-9a-zA-Z]{1,3}\s*\))*)*)'
    r'\s+(?:of|under)\s+(?:the\s+)?'
    r'([A-Z][A-Za-z\s,.\'()\-/]{2,80}?(?:Act|Code|Ordinance|Rules|Constitution)(?:,?\s*\d{4})?)')

# the reverse order judges also use: "the DV Act, Section 12"
BOUND_REV = re.compile(
    r'\b([A-Z][A-Za-z\s,.\'()\-/]{2,80}?(?:Act|Code|Ordinance)(?:,?\s*\d{4})?)\s*[,;]?\s*'
    r'(?:[Ss]ections?|[Ss]ecs?\.)\s*([0-9]{1,4}-?[A-Z]{0,3}(?:\(\d+\))?)')

# abbreviations attached directly: "u/s 138 NI Act", "Section 482 Cr.P.C."
BOUND_ABBR = re.compile(
    r'(?:[Ss]ections?|[Ss]ecs?\.|[Uu]/[Ss]\.?)\s*'
    r'([0-9]{1,4}-?[A-Z]{0,3}(?:\s*\(\s*[0-9a-zA-Z]{1,3}\s*\))*'
    r'(?:\s*(?:,|and|&|/)\s*[0-9]{1,4}-?[A-Z]{0,3})*)'
    r'\s*(?:of\s+)?(?:the\s+)?'
    r'(IPC|I\.P\.C\.?|Cr\.?\s?P\.?\s?C\.?|CrPC|C\.?P\.?C\.?|CPC|N\.?I\.?\s+Act|NI\s+Act|'
    r'NDPS(?:\s+Act)?|POCSO(?:\s+Act)?|D\.?V\.?\s+Act|P\.?C\.?\s+Act|MV\s+Act|'
    r'M\.?V\.?\s+Act|IT\s+Act|I\.?T\.?\s+Act|Arms\s+Act|SARFAESI|PMLA|IBC)\b')

ANY_SEC = re.compile(r'\b(?:[Ss]ections?|[Ss]ecs?\.|[Uu]/[Ss]\.?)\s*([0-9]{1,4}-?[A-Z]{0,3}(?:\(\d+\))?)')
ARTICLE = re.compile(r'\bArticles?\s+(\d{1,3}[A-Z]?)\b')

def _split(seclist):
    out = []
    for part in re.split(r',|\band\b|&|/', seclist):
        p = re.sub(r'\s+', '', part).strip(' .').replace('-', '')   # 498-A -> 498A
        if re.match(r'^\d', p): out.append(p)
    return out

def bound_sections(text):
    """{act_surface_form: [sections]} plus the sections nobody tied to an Act."""
    bound = {}
    def add(act, secs):
        a = re.sub(r'\s+', ' ', act).strip(' .,;')
        if not a: return
        bound.setdefault(a, set()).update(secs)

    for secs, act in BOUND.findall(text):
        add(act, _split(secs))
    for act, sec in BOUND_REV.findall(text):
        add(act, _split(sec))
    for secs, abbr in BOUND_ABBR.findall(text):
        add(abbr, _split(secs))

    claimed = {s for v in bound.values() for s in v}
    loose = {s for s in ANY_SEC.findall(text) if re.sub(r'\s+','',s) not in claimed}
    arts  = {"Art." + a for a in ARTICLE.findall(text)}
    if arts:
        add("Constitution of India", arts)
    return {k: sorted(v) for k, v in bound.items()}, sorted(loose)[:30]

if __name__ == "__main__":
    t = ("This is a petition under Section 482 Cr.P.C. for quashing of FIR No.66/2014, "
         "under Sections 498-A/406 IPC. The wife also moved an application under "
         "Sections 12, 18 and 20 of the Protection of Women from Domestic Violence Act, 2005, "
         "and relied on Article 21 of the Constitution. Section 125 was also invoked.")
    b, loose = bound_sections(t)
    for k, v in b.items(): print(f"  {k[:56]:58} -> {v}")
    print(f"  (kisi kanoon se nahi juda) -> {loose}")
