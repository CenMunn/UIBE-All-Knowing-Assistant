"""独立于HTTP读取超时的阶段截止；迟到返回只进私有队列。"""
import time
from queue import Queue, Empty
from threading import RLock, Thread

class RoundTimeout(ValueError):
    pass

class SearchStageTimeout(RoundTimeout):
    pass

class RoundControl:
    def __init__(self,*,thinking,clock=time.monotonic,limits=None):
        self.clock=clock;self.started=clock();self.stage_started={}
        self.limits={'search':30.,'model':120. if thinking else 45.,'total':180.}|(limits or {})
        self.lock=RLock();self.reason='';self.finished=False;self.closers=[];self.search_expired=False
    def remaining(self,stage):
        with self.lock:
            self.check()
            if stage=='search' and self.search_expired:raise SearchStageTimeout('联网检索达到时限，未核实最新信息。')
            self.stage_started.setdefault(stage,self.clock())
            left=min(self.limits['total']-(self.clock()-self.started),
                     self.limits[stage]-(self.clock()-self.stage_started[stage]))
            if left<=0:
                self.cancel('请求达到时限，本轮未完成；服务商可能仍处理并计费。')
                raise RoundTimeout(self.reason)
            return left
    def check(self):
        with self.lock:
            expired=self.clock()-self.started>=self.limits['total'] or any(
                s!='search' and self.clock()-t>=self.limits[s] for s,t in self.stage_started.items())
            if expired and not self.reason:self.cancel('请求达到时限，本轮未完成；服务商可能仍处理并计费。')
            if self.reason:raise RoundTimeout(self.reason)
            if 'search' in self.stage_started and self.clock()-self.stage_started['search']>=self.limits['search']:
                self.stage_started.pop('search');self.search_expired=True
                callbacks,self.closers=self.closers,[]
                for fn in callbacks:self._close(fn)
                raise SearchStageTimeout('联网检索达到时限，未核实最新信息。')
    def register_close(self,callback):
        with self.lock:
            if self.reason:self._close(callback)
            elif callback not in self.closers:self.closers.append(callback)
    @staticmethod
    def _close(fn):
        def safe():
            try:fn()
            except Exception:pass
        Thread(target=safe,daemon=True).start()
    def cancel(self,reason):
        with self.lock:
            if not self.reason:
                self.reason=reason
                callbacks,self.closers=self.closers,[]
                for fn in callbacks:self._close(fn)
    def try_finish(self):
        with self.lock:
            if self.finished:return False
            self.finished=True;self.closers=[]
            return True
    def end_stage(self,stage):
        with self.lock:
            self.check();self.stage_started.pop(stage,None)
    def call(self,stage,fn):
        self.remaining(stage)
        result=Queue(maxsize=1)
        def run():
            try:result.put((True,fn()))
            except BaseException as e:result.put((False,e))
        Thread(target=run,daemon=True).start()
        while True:
            left=self.remaining(stage)
            try:ok,value=result.get(timeout=min(.05,left))
            except Empty:continue
            self.check()
            if ok:return value
            raise value
