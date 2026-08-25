"""Pull every linked bill PDF and run the parser over all of them."""
from __future__ import annotations
import re, sys, os
import requests
from bs4 import BeautifulSoup
sys.path.insert(0,'app')
import parsers
from recon_water import BASE, LOGIN_FORM, LOGIN_POST, UA, OUT, load_env, mask
env=load_env(); s=requests.Session(); s.headers["User-Agent"]=UA
tok=BeautifulSoup(s.get(LOGIN_FORM,timeout=30).text,"html.parser").find("input",{"name":"_csrf"})["value"]
s.post(LOGIN_POST,data={"_csrf":tok,"username":env["TAMPA_USER"],"password":env["TAMPA_PASS"],
      "submit":"Sign In"},headers={"Referer":LOGIN_FORM},timeout=30)
tr=s.get(BASE+"/css/account/accountTransaction",timeout=30).text
ids=sorted(set(re.findall(r"billPrint/retrieve/(\d+)",tr)))
print(f"{len(ids)} bill documents linked\n")
os.makedirs(os.path.join(OUT,"bills"),exist_ok=True)
rows=[]
for i,bid in enumerate(ids,1):
    fp=os.path.join(OUT,"bills",f"bill_{i:02d}.pdf")
    if not os.path.exists(fp):
        r=s.get(f"{BASE}/css//billPrint/retrieve/{bid}",timeout=60)
        open(fp,"wb").write(r.content)
    try:
        txt=parsers.extract_pdf_text(fp)
        b=parsers.parse_bill_pdf_text(txt)
    except Exception as e:
        print(f"  bill {i:02d}: PARSE ERROR {e}"); continue
    rows.append(b)
print(f"{'bill date':<12} {'CCF':>4} {'tiers':>5} {'water$':>8} {'ww$':>7} {'trash$':>7} {'1off$':>7} {'new$':>9} {'due$':>9} rec")
for b in rows:
    tiers=len(b['tiers']); new=b.get('new_charges'); due=b.get('amount_due')
    oneoff=(b.get('deposit_cost') or 0)+(b.get('fee_cost') or 0)
    rec = "OK" if (new and due and abs(new-due)<0.02) else "MISMATCH"
    print(f"{str(b.get('bill_date')):<12} {b.get('water_ccf') or 0:>4.0f} {tiers:>5} "
          f"{b.get('water_total_cost') or 0:>8.2f} {b.get('wastewater_total_cost') or 0:>7.2f} "
          f"{b.get('solid_waste_cost') or 0:>7.2f} {oneoff:>7.2f} {new or 0:>9.2f} {due or 0:>9.2f} {rec}")
bad=[b for b in rows if not (b.get('new_charges') and b.get('amount_due') and abs(b['new_charges']-b['amount_due'])<0.02)]
print(f"\n{len(rows)-len(bad)}/{len(rows)} bills reconcile")
