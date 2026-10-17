from html.parser import HTMLParser
import re,json
class Tables(HTMLParser):
 def __init__(self):super().__init__();self.tables=[];self.stack=[];self.sup=False
 def handle_starttag(self,t,a):
  a=dict(a)
  if t=='table':self.stack.append({'rows':[],'row':None,'cell':None,'class':a.get('class','')})
  if not self.stack:return
  c=self.stack[-1]
  if t=='tr':c['row']=[]
  if t in ('td','th'):c['cell']={'text':'','sup':'','rowspan':int(a.get('rowspan',1)),'colspan':int(a.get('colspan',1))}
  if t=='sup':self.sup=True
  if t=='br' and c['cell'] is not None:c['cell']['text']+=' '
 def handle_endtag(self,t):
  if t=='sup':self.sup=False
  if not self.stack:return
  c=self.stack[-1]
  if t in ('td','th') and c['cell'] is not None:
   for k in ('text','sup'):c['cell'][k]=re.sub(r'\[[^]]*\]','',re.sub(r'\s+',' ',c['cell'][k])).strip()
   if c['row'] is not None:c['row'].append(c['cell'])
   c['cell']=None
  if t=='tr' and c['row'] is not None:c['rows'].append(c['row']);c['row']=None
  if t=='table':self.tables.append(self.stack.pop())
 def handle_data(self,d):
  if self.stack and self.stack[-1]['cell'] is not None:self.stack[-1]['cell']['sup' if self.sup else 'text']+=d

import datetime,urllib.request,urllib.error,os,sys,concurrent.futures,unicodedata,time
def expand(rows):
 out=[];spans={}
 for row in rows:
  vals=[];col=0
  def pending():
   nonlocal col
   while col in spans:
    cell,n=spans[col];vals.append(cell);col+=1
    if n==1:del spans[col-1]
    else:spans[col-1]=(cell,n-1)
  for cell in row:
   pending()
   for _ in range(cell['colspan']):
    vals.append(cell)
    if cell['rowspan']>1:spans[col]=(cell,cell['rowspan']-1)
    col+=1
  pending();out.append(vals)
 return out

SOURCE='https://en.wikipedia.org/wiki/2026_MotoGP_World_Championship'
LICENSE='https://creativecommons.org/licenses/by-sa/4.0/'
def fetch(url):
 for attempt in range(3):
  try:
   with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'QuiSportData/1.0 (https://quisport.it/; MotoGP open-data adaptation)'}),timeout=35) as r:return r.read().decode()
  except (urllib.error.URLError,TimeoutError):
   if attempt==2:raise
   time.sleep(2**attempt)
def tables(raw):
 parser=Tables();parser.feed(raw);return parser.tables
def revision(raw,url):
 match=re.search(r'oldid=(\d+)',raw)
 return 'https://en.wikipedia.org/w/index.php?oldid='+match.group(1) if match else url
def norm(s):return ''.join(c for c in unicodedata.normalize('NFKD',s).casefold() if c.isalnum())
def base(raw):
 T=[t['rows'] for t in tables(raw) if 'wikitable' in t['class']]
 teams=expand(T[0]);numbers={r[4]['text']:r[3]['text'] for r in teams[1:] if len(r)>=6}
 races=[]
 titles=['Thailandia','Brasile','Stati Uniti','Spagna','Francia','Catalogna','Italia','Ungheria','Repubblica Ceca','Paesi Bassi','Germania','Gran Bretagna','Aragona','San Marino','Austria','Giappone','Indonesia','Australia','Malesia','Qatar','Portogallo','Valencia']
 flags=['🇹🇭','🇧🇷','🇺🇸','🇪🇸','🇫🇷','🇪🇸','🇮🇹','🇭🇺','🇨🇿','🇳🇱','🇩🇪','🇬🇧','🇪🇸','🇸🇲','🇦🇹','🇯🇵','🇮🇩','🇦🇺','🇲🇾','🇶🇦','🇵🇹','🇪🇸']
 for i,row in enumerate(T[2][1:]):
  r=[c['text'] for c in row];dt=datetime.datetime.strptime(r[1]+' 2026','%d %B %Y').date().isoformat();races.append(dict(round=i+1,name=titles[i],flag=flags[i],date=dt,circuit=r[3],results=[],sprintResults=[],pole=''))
 points=[25,20,16,13,11,10,9,8,7,6,5,4,3,2,1];sp=[12,9,7,6,5,4,3,2,1]
 drivers=[]
 for row in expand(T[5])[1:]:
  if len(row)<27 or not row[0]['text'].isdigit():continue
  r=[c['text'] for c in row];driver=dict(name=r[1],number=numbers.get(r[1],''),team=r[3],bike=r[2]);drivers.append(dict(**driver,position=int(r[0]),points=int(r[-1])))
  for i,c in enumerate(row[4:26]):
   if not c['text']:continue
   pos=int(c['text']) if c['text'].isdigit() else None
   races[i]['results'].append(dict(**driver,position=pos,status=c['text'],points=points[pos-1] if pos and pos<=15 else 0))
   sprint=re.search(r'\d+',c['sup'])
   if sprint:
    n=int(sprint.group());races[i]['sprintResults'].append(dict(**driver,position=n,status=str(n),points=sp[n-1] if n<=9 else 0))
 for row in T[3][1:]:
  if row and row[0]['text'].isdigit():races[int(row[0]['text'])-1]['pole']=row[2]['text']
 drivers=list({x['name']:x for x in reversed(drivers)}.values());drivers.sort(key=lambda x:x['position'])
 for r in races:
  for k in ['results','sprintResults']:r[k].sort(key=lambda x:(x['position'] is None,x['position'] or 999,x['name']))
 constructors=[dict(position=int(r[0]['text']),name=r[1]['text'],points=int(r[-1]['text'])) for r in T[7][1:] if r and r[0]['text'].isdigit()]
 assert len(races)==22 and len(drivers)>=22 and len(constructors)==5
 for r in races:
  positions=[x['position'] for x in r['results'] if x['position']];assert len(positions)==len(set(positions)),r['name']
 return dict(schema=1,sport='motogp',season=2026,updatedAt=datetime.datetime.now(datetime.timezone.utc).isoformat(),source=SOURCE,license=LICENSE,sourceRevision=revision(raw,SOURCE),notice='Dati adattati da Wikipedia e autori delle voci, CC BY-SA 4.0. Traduzione, selezione e struttura JSON QuiSport. Griglia distinta dalle qualifiche; campi mancanti non ricostruiti.',races=races,drivers=drivers,constructors=constructors)
def enrich(race,raw,url,known):
 ts=tables(raw);matching=[];qual=[]
 for table in ts:
  if 'wikitable' not in table['class']:continue
  rows=expand(table['rows'])
  if not rows:continue
  heads=[c['text'] for c in rows[0]]
  if not ('Rider' in heads and 'No.' in heads):continue
  if 'Q1' in heads and 'Q2' in heads:
   qual.append(rows);continue
  if 'Time/Retired' in heads and 'Laps' in heads:
   names=[r[heads.index('Rider')]['text'] for r in rows[1:] if len(r)>=len(heads)]
   if sum(norm(n) in known for n in names)>=10:matching.append(rows)
 def parse_rows(rows,q=False):
  heads=[re.sub(r'^.*[}]','',c['text']) for c in rows[0]];out=[]
  for row in rows[1:]:
   if len(row)<len(heads):continue
   v={h:c['text'] for h,c in zip(heads,row)}
   if not v.get('No.','').isdigit() or norm(v.get('Rider','')) not in known:continue
   rider=known[norm(v['Rider'])];position=int(v['Pos.']) if v['Pos.'].isdigit() else None
   x=dict(name=rider['name'],number=v['No.'],team=v.get('Team',rider['team']),bike=v.get('Manufacturer',v.get('Constructor',rider['bike'])),position=position,status=v['Pos.'])
   if q:x.update(q1=v.get('Q1',''),q2=v.get('Q2',''),grid=v.get('Final grid',''))
   else:x.update(laps=int(v['Laps']) if v['Laps'].isdigit() else None,time=v['Time/Retired'],grid=v.get('Grid',''),points=float(v['Points']) if re.fullmatch(r'\d+(?:\.\d+)?',v.get('Points','')) else 0)
   out.append(x)
  return out
 def consistent(actual,expected):
  positions={norm(x['name']):x['position'] for x in actual}
  return len(actual)>=len(expected) and all(positions.get(norm(x['name']))==x['position'] for x in expected)
 sessions=[parse_rows(rows) for rows in matching]
 # Distinguish the full race from sprint by every known classified position.
 race_rows=next((rows for rows in sessions if race['results'] and consistent(rows,race['results'])),None)
 if race_rows:race['results']=race_rows;race['raceComplete']=True
 sprint_rows=next((rows for rows in sessions if rows is not race_rows and race['sprintResults'] and consistent(rows,race['sprintResults'])),None)
 if sprint_rows:race['sprintResults']=sprint_rows;race['sprintComplete']=True
 for rows in qual:
  parsed=parse_rows(rows,True)
  if len(parsed)>=10:
   positions=[x['position'] for x in parsed if x['position']]
   if len(positions)!=len(set(positions)):raise ValueError('Posizioni qualifiche duplicate')
   race['qualifyingResults']=parsed;break
 race['gridResults']=[]
 for label,results in [('Gara',race_rows),('Sprint',sprint_rows)]:
  rows=[dict(x,position=int(x['grid'])) for x in results or [] if str(x.get('grid','')).isdigit()]
  positions=[x['position'] for x in rows]
  if rows and len(positions)==len(set(positions)):
   race['gridResults']=sorted(rows,key=lambda x:x['position']);race['gridSession']=label;break
 # First infobox MotoGP pole time; never use the Moto2/Moto3 section.
 for table in ts:
  if 'infobox' in table['class']:
   rows=table['rows'];texts=[' '.join(c['text'] for c in r) for r in rows]
   for i,t in enumerate(texts):
    if t=='Pole position':
     for value in texts[i+1:i+4]:
      if value.startswith('Time '):race['poleTime']=value[5:]
     break
   break
 race['detailSource']=url;race['detailRevision']=revision(raw,url);return race
def validate(data):
 assert data['schema']==1 and data['sport']=='motogp' and data['season']==2026
 assert len(data['races'])==22 and len(data['constructors'])==5
 assert len({d['name'] for d in data['drivers']})==len(data['drivers'])>=22
 for race in data['races']:
  datetime.date.fromisoformat(race['date'])
  for key in ('results','sprintResults','qualifyingResults','gridResults'):
   rows=race.get(key,[]);names=[x['name'] for x in rows];assert len(names)==len(set(names)),(race['name'],key,'piloti duplicati')
   positions=[x['position'] for x in rows if x['position']];assert len(positions)==len(set(positions)),(race['name'],key,'posizioni duplicate')
  if race.get('raceComplete'):assert race['results'][0]['position']==1 and race['results'][0]['time']
 return data
def update(previous=None):
 raw=fetch(SOURCE);data=base(raw);known={norm(x['name']):x for x in data['drivers']};calendar=[r for r in data['races'] if r['results'] or r['date']<=(datetime.date.today()+datetime.timedelta(days=3)).isoformat()]
 names={int(r[0]['text']):r[1]['text'] for r in [t['rows'] for t in tables(raw) if 'wikitable' in t['class']][3][1:] if r and r[0]['text'].isdigit()}
 failures=[]
 def load(race):
  title='2026_'+names[race['round']].replace(' ','_');url='https://en.wikipedia.org/wiki/'+title
  try:return enrich(race,fetch(url),url,known),None
  except (urllib.error.URLError,ValueError,TimeoutError) as e:return race,(race['round'],type(e).__name__)
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
  for race,problem in pool.map(load,calendar):
   if problem:
    failures.append(problem)
    old=next((x for x in (previous or {}).get('races',[]) if x['round']==race['round']),None)
    if old and old.get('detailSource') and [(x['name'],x['position']) for x in old['results']]==[(x['name'],x['position']) for x in race['results']]:
     for k in ('results','sprintResults','qualifyingResults','gridResults','poleTime','detailSource','detailRevision','raceComplete','sprintComplete','gridSession'): 
      if k in old:race[k]=old[k]
 data['schedulerReady']=True;data['intervalMinutes']=60;data['coverage']=dict(raceSessions=sum(bool(r.get('raceComplete')) for r in data['races']),sprintSessions=sum(bool(r.get('sprintComplete')) for r in data['races']),qualifyingSessions=sum(bool(r.get('qualifyingResults')) for r in data['races']),sourceFailures=failures)
 return validate(data)
def main():
 endpoint=os.environ.get('QUISPORT_UPDATE_URL','').rstrip('/');token=os.environ.get('QUISPORT_UPDATE_TOKEN','');previous=None
 if endpoint:
  try:previous=json.loads(fetch(endpoint+'/motogp-data.json'))
  except (urllib.error.URLError,ValueError):pass
 try:data=update(previous)
 except (urllib.error.URLError,TimeoutError):
  if not previous:raise
  validate(previous)
  age=(datetime.datetime.now(datetime.timezone.utc)-datetime.datetime.fromisoformat(previous['updatedAt'])).total_seconds()
  if age>24*3600:raise
  message='Fonte MotoGP temporaneamente non disponibile: conservati gli ultimi dati validi.'
  print(message)
  if os.environ.get('GITHUB_STEP_SUMMARY'):
   with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write(message+'\n')
  return
 body=json.dumps(data,ensure_ascii=False,separators=(',',':')).encode()
 from pathlib import Path
 target=Path('dist/motogp-data.json');target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(body)
 if '--publish' in sys.argv:
  if not endpoint.startswith('https://') or not token:raise ValueError('Configurazione pubblicazione mancante')
  request=urllib.request.Request(endpoint+'/__updates/motogp-current.json',data=body,method='PUT',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','User-Agent':'QuiSportData/1.0'})
  with urllib.request.urlopen(request,timeout=60) as r:assert r.status==200
 coverage=data['coverage'];message='MotoGP verificata: '+str(coverage)
 print(message)
 if os.environ.get('GITHUB_STEP_SUMMARY'):
  with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('## Aggiornamento MotoGP\n\n'+message+'\n\nUltimi dettagli validi conservati in caso di errore temporaneo.\n')
if __name__=='__main__':main()
