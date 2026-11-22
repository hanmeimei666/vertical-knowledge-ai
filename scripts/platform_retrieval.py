"""Replaceable retriever boundary; projections must never replace source records."""
from abc import ABC, abstractmethod


class Retriever(ABC):
    @abstractmethod
    def search(self,query='',filters=None,mode='AND',purpose='retrieve'):
        raise NotImplementedError


class KeywordRetriever(Retriever):
    def __init__(self,store):self.store=store

    def search(self,query='',filters=None,mode='AND',purpose='retrieve'):
        return self.store.retrieve(query,filters,mode,purpose)
