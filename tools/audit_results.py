import json,sys,collections,re
sys.path.insert(0,'.')
from pathlib import Path
from pyperplan.pddl.parser import Parser
from pyperplan import grounding
from src.validate import validate_text, default_mapping_for, parse_plan
from src.pddl_utils import domain_for
from src import run_llm as R
C=collections.Counter; out=collections.defaultdict(C)
tasks={}
def task(variant,prob):
    k=(variant,prob)
    if k not in tasks:
        p=Parser(str(domain_for(variant)),str(Path('problems',variant,prob+'.pddl'))); d=p.parse_domain(); pr=p.parse_problem(d)
        t=grounding.ground(pr, remove_statics_from_initial_state=False, remove_irrelevant_operators=False)
        tasks[k]=(t,{o.name:o for o in t.operators})
    return tasks[k]
def indep(variant,prob,text):
    """independent check with pyperplan's own grounded operators: returns (valid, first_bad_step)"""
    t,ops=task(variant,prob); s=t.initial_state; steps=parse_plan(text)
    if not steps: return False,0
    for i,st in enumerate(steps,1):
        op=ops.get('('+st['raw']+')')
        if op is None or not op.applicable(s): return False,i
        s=op.apply(s)
    return t.goal_reached(s), None
roots=[('paid',Path('results/raw'))]+[(f'pilot/{d.name}',d/'raw') for d in sorted(Path('pilot').glob('round*'))]
disagree=[]; reps=collections.defaultdict(dict)
for tag,root in roots:
    for f in sorted(root.rglob('rep*_round*.json')):
        r=json.loads(f.read_text()); m=r['model_label']; v=r['variant']
        pf=Path('problems',v,r['problem']+'.pddl')
        if tag.startswith('pilot'):
            # pilot rounds 1-3 used older problem/domain files: only rounds 4-5 match the current files
            if tag not in ('pilot/round4','pilot/round5'): continue
        dom=domain_for(v); res=validate_text(dom,pf,r['response_text'],default_mapping_for(dom))
        iv,ibad=indep(v,r['problem'],r['response_text'])
        out[(tag,m)]['replies']+=1
        out[(tag,m)]['valid']+=res['valid']
        if iv!=res['valid'] or (not res['valid'] and res['first_error_step'] and ibad!=res['first_error_step']):
            disagree.append((str(f),res['valid'],res['first_error_step'],iv,ibad))
        if tag!='paid': continue
        txt=r['response_text']; n=len(parse_plan(txt)); ct=r['usage'].get('completion_tokens') or 0
        if n==0: out[(tag,m)]['no_plan']+=1
        if ct>=3950: out[(tag,m)]['hit_length_cap']+=1
        if ct > len(txt)/2.2+60: out[(tag,m)]['tokens_much_more_than_text(hidden thinking?)']+=1
        if '```plan' not in txt and n>0: out[(tag,m)]['plan_without_fence']+=1
        if re.sub(r'```plan.*?```','',txt,flags=re.S).strip() and n>0 and '```plan' in txt and txt.count('```')>=2: out[(tag,m)]['extra_text_around_plan']+=1
        if r['returned_model'] and r['model_id'] not in r['returned_model'] and r['returned_model'] not in r['model_id']: out[(tag,m)]['returned_model:'+str(r['returned_model'])]+=1
        out[(tag,m)]['cost_source:'+str(r['cost_source'])]+=1
        if res['error_category']=='syntax' and n>0: out[(tag,m)]['syntax:'+re.sub(r"'[^']*'",'X',res['error_message'].split('): ')[-1])[:38]]+=1
        lines=[s['raw'] for s in parse_plan(txt)]
        if n>8 and len(set(lines))<0.5*n: out[(tag,m)]['repetitive_plan(loop)']+=1
        reps[(m,v,r['problem'],r['round'])][r['rep']]=txt
        # conversation integrity: round k has 2k+1 messages, alternating, first = direct prompt
        msgs=r['messages']; k=r['round']
        if len(msgs)!=2*k+1 or [x['role'] for x in msgs]!=(['user','assistant']*k+['user']): out[(tag,m)]['BAD_conversation_shape']+=1
        if k>0:
            prev=json.loads((f.parent/f"rep{r['rep']}_round{k-1}.json").read_text())
            pres=validate_text(dom,pf,prev['response_text'],default_mapping_for(dom))
            if msgs[-2]['content']!=prev['response_text'] or (pres['error_message'] or '') not in msgs[-1]['content']: out[(tag,m)]['BAD_feedback_mismatch']+=1
same=C()
for (m,v,p,rnd),d in reps.items():
    if rnd==0 and len(d)==2: same[m+(' identical' if d[0]==d[1] else ' different')]+=1
for k in sorted(out): print(k, dict(out[k]))
print('repeat 1 vs 2 first answers:',dict(same))
print('DISAGREEMENTS with independent checker:',len(disagree)); [print(' ',d) for d in disagree[:8]]
