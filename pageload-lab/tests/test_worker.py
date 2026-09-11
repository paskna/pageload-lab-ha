import asyncio
from datetime import datetime, timezone

import pytest

from app.browser import VisitResult
from app.models import RequestRecord, Test
from app.proxy_manager import ProxyManager
from app.reporting import ReportingService
from app.security import SecretBox
from app.worker import TestRunnerManager


class FakeEngine:
    def __init__(self, delay=.01, fail_status=None):
        self.delay=delay; self.fail_status=fail_status; self.active_sessions=0; self.calls=0
    async def visit(self,url,mode,stay,proxy,callback=None):
        self.calls+=1;self.active_sessions+=1;start=datetime.now(timezone.utc)
        await asyncio.sleep(self.delay)
        self.active_sessions-=1;code=self.fail_status or 200;success=code<400
        return VisitResult(start,datetime.now(timezone.utc),code,success,load_time_ms=25,stay_time_seconds=stay,error_type=None if success else 'http_status')


def manager(database,tmp_path,engine):
    proxies=ProxyManager(database.session_factory,SecretBox(tmp_path/'key'))
    return TestRunnerManager(database.session_factory,engine,proxies,ReportingService(database.session_factory))


def create_test(database, **values):
    defaults=dict(name='Worker',url='https://93.184.216.34',mode='http',frequency_per_minute=600,frequency_mode='even',duration_mode='requests',max_requests=5,max_concurrency=2,reporting_enabled=True,status='ready')
    defaults.update(values)
    with database.session() as session:
        row=Test(**defaults);session.add(row);session.flush();return row.id


async def wait_done(database,test_id,timeout=3):
    for _ in range(int(timeout/.02)):
        with database.session() as session:
            if session.get(Test,test_id).status not in {'running','paused','waiting_capacity'}: return
        await asyncio.sleep(.02)
    raise AssertionError('runner did not finish')


async def test_exact_max_requests_and_persisted_results(database,tmp_path):
    engine=FakeEngine();runner=manager(database,tmp_path,engine);test_id=create_test(database)
    await runner.start(test_id);await wait_done(database,test_id)
    with database.session() as session:
        test=session.get(Test,test_id)
        assert test.status=='completed' and test.total_requests==5 and test.successful_requests==5
        assert session.query(RequestRecord).filter_by(test_id=test_id).count()==5
    assert engine.calls==5


async def test_pause_resume_and_stop(database,tmp_path):
    engine=FakeEngine(.04);runner=manager(database,tmp_path,engine);test_id=create_test(database,max_requests=50,frequency_per_minute=300)
    await runner.start(test_id);await asyncio.sleep(.25);await runner.pause(test_id);await asyncio.sleep(.15)
    paused=engine.calls;await asyncio.sleep(.15);assert engine.calls==paused
    await runner.resume(test_id);await asyncio.sleep(.25);await runner.stop(test_id)
    with database.session() as session: assert session.get(Test,test_id).status=='stopped'


async def test_abort_conditions(database,tmp_path):
    engine=FakeEngine(fail_status=500);runner=manager(database,tmp_path,engine);test_id=create_test(database,max_requests=20,abort_consecutive_errors=2)
    await runner.start(test_id);await wait_done(database,test_id)
    with database.session() as session:
        test=session.get(Test,test_id);assert test.status=='stopped';assert 'aufeinanderfolgende' in test.status_message;assert test.total_requests>=2


@pytest.mark.parametrize('field,value,result',[
    ('abort_on_429',True,429),('abort_on_503',True,503),('abort_timeouts',1,None),('abort_load_seconds',.001,None),('abort_error_rate_percent',20,None)
])
def test_all_abort_condition_branches(field,value,result):
    test=Test(name='x',url='https://example.com',total_requests=1,failed_requests=1,timeout_count=1,consecutive_errors=1,**{field:value})
    visit=VisitResult(datetime.now(timezone.utc),datetime.now(timezone.utc),result,False,load_time_ms=10,error_type='timeout')
    assert TestRunnerManager._abort_reason(test,visit)
