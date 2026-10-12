"""Publish verified snapshots first and the manifest last. Credentials stay in env."""
import datetime,hashlib,json,os,pathlib,urllib.request
ROOT=pathlib.Path(__file__).resolve().parents[1]
def publish(send=None):
 data=ROOT/'dist/data';manifest=json.loads((data/'manifest.json').read_text())
 if send is None:
  endpoint=os.environ['QUISPORT_UPDATE_URL'].rstrip('/');token=os.environ['QUISPORT_UPDATE_TOKEN']
  if not endpoint.startswith('https://'):raise ValueError('HTTPS required')
  def send(name,body):
   req=urllib.request.Request(endpoint+'/__updates/'+name,data=body,method='PUT',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','User-Agent':'QuiSport-Updater/1.0'})
   with urllib.request.urlopen(req,timeout=60) as response:
    if response.status!=200:raise RuntimeError('Publication rejected')
 for state in manifest['competitions'].values():
  filename=state['file']
  if len(filename)!=21 or not filename.endswith('.json') or any(c not in '0123456789abcdef' for c in filename[:16]):raise ValueError('Invalid snapshot filename')
  body=(data/filename).read_bytes()
  if state.get('provider')=='openligadb':
   extra=json.loads(body)['coverage']['openDataFile'];export=(data/extra).read_bytes()
   if hashlib.sha256(export).hexdigest()[:16]+'.json'!=extra:raise ValueError('Invalid open database checksum')
   send(extra,export)
  version=hashlib.sha256(body).hexdigest()[:16]+'.json'
  send(version,body);state['file']=version
 manifest.update(schedulerReady=True,publishedAt=datetime.datetime.now(datetime.timezone.utc).isoformat(),intervalMinutes=60)
 send('manifest.json',json.dumps(manifest,ensure_ascii=False,separators=(',',':')).encode())
 print('Pubblicazione completata')
if __name__=='__main__':publish()
