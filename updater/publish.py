"""Publish verified snapshots first and the manifest last. Credentials stay in env."""
import datetime,hashlib,json,os,pathlib,urllib.request,urllib.error,time,ssl
ROOT=pathlib.Path(__file__).resolve().parents[1]
def put_with_retry(request,opener=None,sleep=None):
 # Repeating the same authenticated PUT is idempotent; never retry auth/data errors.
 opener=opener or urllib.request.urlopen;sleep=sleep or time.sleep
 for attempt in range(3):
  try:
   with opener(request,timeout=60) as response:
    if response.status!=200:raise RuntimeError('Publication rejected')
   return
  except urllib.error.HTTPError as error:
   if error.code not in (408,429,500,502,503,504) or attempt==2:raise
  except (urllib.error.URLError,TimeoutError,ConnectionError,ssl.SSLError):
   if attempt==2:raise
  sleep(2**(attempt+1))

def publish(send=None):
 data=ROOT/'dist/data';manifest=json.loads((data/'manifest.json').read_text())
 if send is None:
  endpoint=os.environ['QUISPORT_UPDATE_URL'].rstrip('/');token=os.environ['QUISPORT_UPDATE_TOKEN']
  if not endpoint.startswith('https://'):raise ValueError('HTTPS required')
  def send(name,body):
   req=urllib.request.Request(endpoint+'/__updates/'+name,data=body,method='PUT',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','User-Agent':'QuiSport-Updater/1.0'})
   put_with_retry(req)
 remote=None
 for competition,state in list(manifest['competitions'].items()):
  if state.get('provider')=='sportscore' and state.get('state')!='ok':
   if remote is None:
    with urllib.request.urlopen(urllib.request.Request('https://quisport.bonafedetablet.workers.dev/data/manifest.json',headers={'User-Agent':'QuiSport-Updater/1.0'}),timeout=30) as response:remote=json.load(response)
   previous=remote['competitions'][competition].copy();previous.update(state='error',lastError=state.get('lastError'),lastAttempt=state.get('lastAttempt'));manifest['competitions'][competition]=previous;continue
  filename=state['file']
  if len(filename)!=21 or not filename.endswith('.json') or any(c not in '0123456789abcdef' for c in filename[:16]):raise ValueError('Invalid snapshot filename')
  body=(data/filename).read_bytes()
  if state.get('provider')=='openligadb':
   extra=json.loads(body)['coverage']['openDataFile'];export=(data/extra).read_bytes()
   if hashlib.sha256(export).hexdigest()[:16]+'.json'!=extra:raise ValueError('Invalid open database checksum')
   send(extra,export)
  version=hashlib.sha256(body).hexdigest()[:16]+'.json'
  send(version,body);state['file']=version
 f1=data.parent/'f1-data.json'
 if f1.exists():send('f1-current.json',f1.read_bytes())
 manifest.update(schedulerReady=True,publishedAt=datetime.datetime.now(datetime.timezone.utc).isoformat(),intervalMinutes=60)
 send('manifest.json',json.dumps(manifest,ensure_ascii=False,separators=(',',':')).encode())
 print('Pubblicazione completata')
if __name__=='__main__':publish()
