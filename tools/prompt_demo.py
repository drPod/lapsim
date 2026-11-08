#!/usr/bin/env python3
"""Ask local Nemotron for scenario tool decisions, validate and dispatch them.
This is explicitly a prompted scenario demo, not autonomous contact detection.
"""
import argparse
import base64
import json
import sys
import time
import urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from director.config import load
from director.engine import Engine
from director.tools import TOOLS
from director.sinks import RestyleSink


def request(url, body=None, timeout=90):
    data=None if body is None else json.dumps(body).encode()
    req=urllib.request.Request(url,data=data,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default='/tmp/submission/nemotron-decision.json')
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    prompt='For this prompted simulator demonstration, introduce moderate bleeding at the gallbladder bed and have the attending ask the trainee to apply pressure. Choose the appropriate tools and arguments. This is a simulated training scenario, not a real patient.'
    with urllib.request.urlopen('http://127.0.0.1:8090/frame/raw.jpg',timeout=5) as r:
        raw=base64.b64encode(r.read()).decode()
    system='/no_think\nYou are the scenario director of a laparoscopic simulator. Return only JSON with a tool_calls array. Each item must have name and arguments. Choose at most two calls from this tool catalog: '+json.dumps(TOOLS)+'\nUse severity 0.65 for moderate bleeding. Do not describe actions as already executed.'
    body={'model':'nemotron-nano-vl','messages':[{'role':'system','content':system},{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+raw}},{'type':'text','text':prompt}]}], 'temperature':0,'max_tokens':400,'response_format':{'type':'json_object'}}
    t0=time.perf_counter(); response=request('http://127.0.0.1:8001/v1/chat/completions',body)
    message=response['choices'][0]['message']; content=message.get('content') or ''
    content=content.split('</think>')[-1].strip()
    decision=json.loads(content[content.index('{'):content.rindex('}')+1])
    calls=decision.get('tool_calls',[])
    result={'mode':'prompted scenario demonstration','model':response.get('model'),'prompt':prompt,'inference_s':round(time.perf_counter()-t0,3),'model_response':response,'tool_calls':calls,'dispatch':[]}
    cfg=load(Path(__file__).resolve().parents[1]/'config.yaml'); engine=Engine(cfg)
    if args.execute:
        for call in calls:
            call['source']='director'
            reason=engine.validate(time.time(),call)
            if reason: raise ValueError('Tool rejected: '+reason)
            events=engine.execute(time.time(),call)
            for ev in events:
                vr=request('http://127.0.0.1:8787/api/events',ev,timeout=5)
                translated=RestyleSink.translate(ev)
                restyle=request('http://127.0.0.1:8090/events',translated,timeout=5) if translated else None
                result['dispatch'].append({'event':ev,'vr':vr,'restyle':restyle})
    Path(args.output).write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)

if __name__=='__main__':main()
