import contextlib,io,copy,datetime,importlib.util,json,tempfile,unittest,urllib.error
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('refresh',Path(__file__).with_name('refresh.py'));mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
def snapshot():
 rows=[dict(name=n,position=i+1,points=3 if i==0 else 0,played=1,wins=1-i,draws=0,losses=i,goalsFor=1-i,goalsAgainst=i,difference=1-2*i) for i,n in enumerate(['A','B'])]
 return dict(schema=1,name='Test',clubs=[dict(name=n,players=[],source='',sourceLabel='',checked='') for n in ['A','B']],fixtures={'rounds':[{'matches':[dict(home='A',away='B',date='2026-10-01',score='1 – 0',played=True)]}]},live={'standings':rows},details={},coverage={'provider':'football-data'})
class ValidationTests(unittest.TestCase):
 def test_matching_table(self):c=snapshot();mod.validate(c,c)
 def test_one_game_provider_lag(self):
  c=snapshot();old=copy.deepcopy(c)
  for t in c['live']['standings']:t.update(played=2,draws=1,points=t['points']+1,goalsFor=t['goalsFor']+2,goalsAgainst=t['goalsAgainst']+2)
  mod.validate(c,old);self.assertEqual(c['coverage']['standingsAlignment'],'pending')
 def test_wrong_score_same_games_rejected(self):
  c=snapshot();old=copy.deepcopy(c);c['fixtures']['rounds'][0]['matches'][0]['score']='90 – 0'
  with self.assertRaises(AssertionError):mod.validate(c,old)
 def test_partial_table_rejected(self):
  c=snapshot();old=copy.deepcopy(c);c['live']['standings'].pop()
  with self.assertRaises(AssertionError):mod.validate(c,old)
 def test_season_in_matches_response(self):
  mod.fd_validate_season({'competition':{'code':'SA'},'matches':[{'season':{'startDate':'2026-08-01'}}]},'SA',2026)
 def test_season_in_standings_response(self):
  mod.fd_validate_season({'competition':{'code':'SA'},'season':{'startDate':'2026-08-01'}},'SA',2026)
 def test_wrong_season_rejected(self):
  with self.assertRaises(ValueError):mod.fd_validate_season({'competition':{'code':'SA'},'season':{'startDate':'2025-08-01'}},'SA',2026)
 def test_wrong_competition_rejected(self):
  with self.assertRaises(ValueError):mod.fd_validate_season({'competition':{'code':'PL'},'season':{'startDate':'2026-08-01'}},'SA',2026)
class RetentionTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.data=self.root/'dist/data';self.data.mkdir(parents=True);(self.root/'updater').mkdir();self.file='a'*16+'.json';self.cfg={'Test':{'provider':'football-data','file':self.file}};self.seed=snapshot();self.save()
 def save(self,age=0):
  (self.data/self.file).write_text(json.dumps(self.seed,separators=(',',':')))
  success=(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(hours=age)).isoformat()
  (self.data/'manifest.json').write_text(json.dumps({'competitions':{'Test':{'file':self.file,'lastSuccess':success,'state':'ok'}}}))
 def run_refresh(self,provider):
  with patch.object(mod,'ROOT',self.root),patch.object(mod,'DATA',self.data),patch.object(mod,'CFG',self.cfg),patch.object(mod,'football_data',provider),patch('sys.argv',['refresh']),patch.dict('os.environ',{'GITHUB_STEP_SUMMARY':str(self.root/'test-summary.md')}),contextlib.redirect_stderr(io.StringIO()),contextlib.redirect_stdout(io.StringIO()):return mod.main()
 def test_temporary_outage_preserves_then_recovers(self):
  def outage(*_):raise TimeoutError('source offline')
  self.assertEqual(self.run_refresh(outage),0)
  self.assertEqual(json.loads((self.data/self.file).read_text()),self.seed)
  self.assertEqual(json.loads((self.data/'manifest.json').read_text())['competitions']['Test']['state'],'error')
  self.assertEqual(self.run_refresh(lambda c,*_:c),0)
  state=json.loads((self.data/'manifest.json').read_text())['competitions']['Test'];self.assertEqual(state['state'],'ok');self.assertEqual(state['consecutiveFailures'],0)
 def test_prolonged_outage_alerts(self):
  self.save(age=7)
  def outage(*_):raise TimeoutError('source offline')
  self.assertEqual(self.run_refresh(outage),1)
 def test_internal_bug_alerts_immediately(self):
  def bug(*_):raise TypeError('programming error')
  self.assertEqual(self.run_refresh(bug),1)
 def test_partial_table_preserves_seed(self):
  def partial(c,*_):c['live']['standings'].pop();return c
  self.assertEqual(self.run_refresh(partial),0);self.assertEqual(json.loads((self.data/self.file).read_text()),self.seed)
class RetryTests(unittest.TestCase):
 def test_transient_http_error_retries(self):
  class Response:
   def __enter__(self):return self
   def __exit__(self,*_):pass
   def read(self):return b'{"ok":true}'
  error=urllib.error.HTTPError('https://example.invalid',503,'temporary',{},None)
  with patch.dict('os.environ',{'FOOTBALL_DATA_TOKEN':'test'}),patch.object(mod.time,'sleep'),patch.object(mod.urllib.request,'urlopen',side_effect=[error,Response()]) as call:
   self.assertEqual(mod.fd_get('SA','/matches'),{'ok':True});self.assertEqual(call.call_count,2)
 def test_auth_error_not_retried(self):
  error=urllib.error.HTTPError('https://example.invalid',401,'unauthorized',{},None)
  with patch.dict('os.environ',{'FOOTBALL_DATA_TOKEN':'test'}),patch.object(mod.time,'sleep'),patch.object(mod.urllib.request,'urlopen',side_effect=error) as call:
   with self.assertRaises(urllib.error.HTTPError):mod.fd_get('SA','/matches')
   self.assertEqual(call.call_count,1)
if __name__=='__main__':unittest.main()
