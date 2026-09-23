import json
from pathlib import Path
from .config import ROOT

class Budget:
    def __init__(self,total=12):self.total=total;self.used=0;self.per_skill={};self.events=[]
    def consume(self,skill,tool):
        if tool not in skill['allowed_tools']:raise PermissionError('当前角色无权调用该工具')
        count=self.per_skill.get(skill['name'],0)
        if self.used>=self.total or count>=skill['max_calls']:raise ValueError('工具调用预算已用尽')
        self.used+=1;self.per_skill[skill['name']]=count+1
        self.events.append({'skill':skill['name'],'tool':tool,'sequence':self.used})

class SkillRegistry:
    def __init__(self):
        self.registry={}
        for path in (ROOT/'skills').glob('*/config.json'):
            config=json.loads(path.read_text());self.registry[config['name']]=(config,path.parent)
    def list(self):return [{'name':c['name'],'description':c['description']} for c,p in self.registry.values()]
    def load(self,name):
        c,p=self.registry[name]
        return {**c,'instructions':(p/'SKILL.md').read_text()}
