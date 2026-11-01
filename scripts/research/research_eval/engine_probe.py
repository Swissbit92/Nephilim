#!/usr/bin/env python3
"""Score each SearXNG engine on a few questions from this IP, on a throwaway container.

The score is term overlap, which only picks candidates. Answers still need a blind test.

    python engine_probe.py <scratch_dir>
"""
import json,re,sys,time,subprocess,urllib.request,urllib.parse,collections,os
S=sys.argv[1]
Q=["height of the Matterhorn","Swiss National Bank policy rate","latest Formula 1 race winner","Ledger Nano X review","Ollama latest release","train Zurich to Geneva duration"]
STOP=set("a an and are as at be but by can did do does for from has have how i in is it its me my of on or the this to was we what when where which who why will with would you your".split())
def terms(t): return {w for w in re.findall(r"[^\W_]{2,}",t.lower())}-STOP
sec=re.search(r'secret_key: "(.*?)"',open('/Users/swissbit./nephilim-ecosystem/nephilim/docker/searxng/settings.yml').read()).group(1)
names=['bing','duckduckgo','google','startpage','mojeek','qwant','presearch','privacywall','swisscows','dogpile','tonline','startpagina','searchrockit','luxxle','findborg','resulthunter','tusksearch','fireball','wikipedia','wiby','marginalia','stract','seznam','naver','baidu','yahoo','aol','ecosia','metager','searchmysite','crowdview','yep']
cfg=f'''use_default_settings:
  engines:
    remove: [brave, brave.images, brave.videos, brave.news, braveapi, yandex, yandex images, yandex music]
server:
  secret_key: "{sec}"
  limiter: false
  bind_address: "0.0.0.0"
search:
  formats: [html, json]
  safe_search: 0
outgoing:
  request_timeout: 6.0
  retries: 1
engines:
'''+''.join(f'  - name: {n}\n    disabled: false\n' for n in names)
p=S+'/s_all.yml'; open(p,'w').write(cfg); os.chmod(p,0o644)
subprocess.run(['docker','rm','-f','sx-e'],capture_output=True)
subprocess.run(['docker','run','-d','--name','sx-e','-p','127.0.0.1:8893:8080','-v',f'{p}:/etc/searxng/settings.yml:ro','searxng/searxng@sha256:f4aa660c54d062dedb268be84e821ea24e39f48c7a496af8dd02cc9eedb93847'],capture_output=True)
time.sleep(16)
c=json.load(urllib.request.urlopen('http://127.0.0.1:8893/config'))
have=[e['name'] for e in c['engines'] if e['enabled'] and 'general' in e.get('categories',[])]
print('enabled general',len(have),have[:40],flush=True)
rows=[]
for e in have:
    good=tot=0;err=set();top=0
    for q in Q:
        try:
            d=json.load(urllib.request.urlopen('http://127.0.0.1:8893/search?'+urllib.parse.urlencode(dict(q=q,format='json',engines=e,safesearch=0)),timeout=25))
        except Exception as x: err.add(type(x).__name__); continue
        res=d['results'][:5]; want=terms(q)
        h=[len(want&terms(x.get('title','')+' '+x.get('content','')))>=max(1,len(want)//2) for x in res]
        good+=sum(h);tot+=len(res);top+=bool(h and h[0])
        for u in d.get('unresponsive_engines',[]): err.add(u[1][:20])
        time.sleep(1.5)
    rows.append((good,tot,top,e,sorted(err)[:2])); print(e.ljust(16),f'{good}/{tot}','top1',f'{top}/{len(Q)}',sorted(err)[:2],flush=True)
subprocess.run(['docker','rm','-f','sx-e'],capture_output=True)
