#!/usr/bin/env python3
"""G0: the research path on frozen Brave evidence, and on live SearXNG as Gwen would use it.

Run with SEARXNG_BASE_URL at a test instance. Results stay outside this public repo.
"""
import json,os,sys,time
from types import SimpleNamespace
from dataclasses import asdict
sys.path.insert(0,'.'); sys.path.insert(0,'scripts/research/research_eval')
from capture import SYSTEM, chat
from src.coordinator import startup
from src.coordinator.config import get_settings
from src.coordinator.services.research_service import ResearchService, unsupported_figures
from src.coordinator.services.web_fetch_service import fetch_url
from src.coordinator.tools import executor_bindings
from src.coordinator.tools.synthesis_prompts import build_synthesis_prompt
F=os.path.expanduser('~/nephilim-eval/research/v0.10.1/')
os.makedirs(os.path.expanduser('~/nephilim-eval/research/g0'),exist_ok=True)
OUT=os.path.expanduser('~/nephilim-eval/research/g0/arms.json')
rows=json.load(open(F+'fixtures.json')); get_settings.cache_clear()
search=executor_bindings._make_search_executor('web_search')
prompt=build_synthesis_prompt(SYSTEM,True,research=True); out={}
def answer(p,q):
    if not p.usable: return {'answer':'','chars':0,'fetched':0,'unsupported':0,'evidence':'','searches':p.searches,'urls':[]}
    ev=p.render(); a=chat(prompt,f"{ev}\n\nQuestion: {q}")
    return {'answer':a,'chars':p.chars,'fetched':sum(s.fetched for s in p.sources),'unsupported':len(unsupported_figures(a,ev,q)),'evidence':ev,'searches':p.searches,'urls':[s.url for s in p.sources[:5]]}
for r in rows:
    res=[SimpleNamespace(**x) for x in r['results']]; n={'c':0}
    def s(_):
        n['c']+=1; return res if n['c']==1 else []
    brave=answer(ResearchService(s,lambda u:r['pages'].get(u,'ERROR')).run(r['query']),r['query'])
    t=time.monotonic()
    sx=answer(ResearchService(lambda a:search(a,{'nsfw':True}),fetch_url).run(r['query']),r['query']); sx['seconds']=round(time.monotonic()-t,1)
    out[r['id']]={'query':r['query'],'kind':r['kind'],'brave':brave,'searxng':sx}
    json.dump(out,open(OUT,'w'),indent=0); print(r['id'],sx['searches'],sx['fetched'],sx['chars'],flush=True); time.sleep(3)
