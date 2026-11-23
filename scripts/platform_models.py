"""Vendor-neutral capability boundary; only deterministic planning is implemented."""
from abc import ABC, abstractmethod


class ModelCapabilityAdapter(ABC):
    @abstractmethod
    def invoke(self, capability, request, evidence):
        raise NotImplementedError


class ExtractivePlanner(ModelCapabilityAdapter):
    def invoke(self, capability, request, evidence):
        if capability != 'content_planning':
            raise ValueError('planned: no model adapter for ' + capability)
        selected=[e['knowledge']['id'] for e in evidence['entries']]
        return {'task_understanding':request['goal'],'knowledge_scope':selected,
                'structure':[{'knowledge_id':id_,'purpose':'synthetic arrangement'} for id_ in selected],
                'missing_information':evidence['gaps'],'suggested_formats':request['outputs'],
                'tools':['export.draft'],'review_required':True,
                'risks':['No automatic professional applicability; review pending']}
