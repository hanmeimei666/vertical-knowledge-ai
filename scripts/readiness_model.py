"""The local planner speaks a small schema and cannot select or invoke arbitrary tools."""
from abc import ABC, abstractmethod
from platform_contracts import validate

PLAN_SCHEMA = {
    'type':'object', 'required':['units','objectives','tools','interaction'],
    'additionalProperties':False,
    'properties':{
        'units':{'type':'array','items':{'type':'string'}},
        'objectives':{'type':'array','items':{'type':'string'}},
        'tools':{'type':'array','items':{'type':'string'}},
        'interaction':{'type':'object','required':['prompt','answer','feedback'],
                       'additionalProperties':False,
                       'properties':{key:{'type':'string'} for key in ('prompt','answer','feedback')}}}}


class ModelProvider(ABC):
    @abstractmethod
    def plan(self, request, evidence):
        """Plan from data; no role, callable, credentials or side-effect channel."""


class SyntheticModel(ModelProvider):
    def plan(self, request, evidence):
        return {'units':[e['knowledge']['id'] for e in evidence['entries']],
                'objectives':['Identify a source and its evidence location'],
                'tools':['documents','interactive'] + (['media'] if request.get('timeline') else []),
                'interaction':{'prompt':'Which field traces a claim back to a source?',
                               'answer':'locator','feedback':'The locator identifies the source coordinate.'}}


def validate_plan(plan, evidence, allowed_tools):
    validate(plan,PLAN_SCHEMA)
    allowed_ids = {e['knowledge']['id'] for e in evidence['entries']}
    if (not plan['units'] or len(set(plan['units']))!=len(plan['units']) or not set(plan['units'])<=allowed_ids
            or set(plan['tools'])!=set(allowed_tools)):
        raise ValueError('Model plan exceeds authorized evidence or tool set')
    if any(len(v)>2000 for v in [*plan['objectives'],*plan['interaction'].values()]):
        raise ValueError('Oversize model output')
    return plan
