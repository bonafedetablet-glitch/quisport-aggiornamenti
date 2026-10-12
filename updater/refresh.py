"""Public-source updater. Atomic per-competition commits; no credentials required.
Run from any directory: python3 updater/refresh.py [--league NAME] [--rosters] [--offline-test]
Cloud scheduling is supplied by the host, not the visitor's browser.
"""
from pathlib import Path
import argparse,collections,concurrent.futures,datetime,html,json,os,re,sys,tempfile,time,unicodedata,urllib.request
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1];DATA=ROOT/'dist/data';CFG=json.loads((ROOT/'updater/config.json').read_text());NOW=datetime.datetime.now(datetime.timezone.utc);TODAY=NOW.astimezone(ZoneInfo('Europe/Rome')).date()
def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def norm(n):return re.sub('[^a-z0-9]','',unicodedata.normalize('NFKD',n).encode('ascii','ignore').decode().lower())
def atomic(path,data):
 path.parent.mkdir(parents=True,exist_ok=True)
 with tempfile.NamedTemporaryFile('w',dir=path.parent,delete=False,encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,separators=(',',':'));tmp=f.name
 os.replace(tmp,path)
def fetch(url):
 for attempt in range(2):
  try:
   with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'QuiSportData/1.0'}),timeout=25) as r:return r.read().decode()
  except Exception:
   if attempt:raise
   time.sleep(1)
def get(url):return json.loads(fetch(url))
def checked():return NOW.astimezone(ZoneInfo('Europe/Rome')).strftime('%d/%m/%Y %H:%M')
def validate(candidate,previous):
 cs=candidate['clubs'];live=candidate['live'];f=candidate['fixtures'];names={c['name'] for c in cs};assert names and len(names)==len(cs),'Squadre duplicate o mancanti'
 rows=live['standings'];assert len(rows)==len(cs) and {t['name'] for t in rows}==names,'Classifica incompleta'
 assert {t['name'] for t in previous['live']['standings']}==names,'Cambio partecipanti: controllo stagione necessario'
 for t in rows:assert t['played']==t['wins']+t['draws']+t['losses'] and t['difference']==t['goalsFor']-t['goalsAgainst'],'Classifica incoerente'
 matches=[m for r in f['rounds'] for m in r['matches']];seen=set();total=collections.defaultdict(lambda:[0,0,0])
 for m in matches:
  assert m['home'] in names and m['away'] in names and m['home']!=m['away'],'Squadra sconosciuta'
  ident=(m['home'],m['away'],m['date']);assert ident not in seen,'Partita duplicata';seen.add(ident)
  datetime.date.fromisoformat(m['date'])
  if m['played']:
   a,b=map(int,m['score'].split(' – '));assert a>=0 and b>=0
   if m.get('isGroup',True):
    for n,gf,ga in [(m['home'],a,b),(m['away'],b,a)]:total[n][0]+=1;total[n][1]+=gf;total[n][2]+=ga
   detail=candidate['details'].get('|'.join([candidate['name'],m['date'],m['home'],m['away']]))
   if detail:assert [sum(e[1]==m[h] for e in detail['goals']) for h in ['home','away']]==[a,b],'Gol tabellino incoerenti'
 for t in rows:assert total[t['name']]==[t['played'],t['goalsFor'],t['goalsAgainst']],'Calendario e classifica non allineati'
 oldmatches=[m for r in previous['fixtures']['rounds'] for m in r['matches']];assert len(matches)>=len(oldmatches),'Calendario ridotto dalla fonte'
 return candidate

def parse_detail(p,m,names):
 d=dict(source=m['source'],sourceLabel='ESPN · tabellino',checked=checked(),time=m['time'],goals=[],cards=[],substitutions=[])
 for e in p.get('keyEvents',[]):
  typ=e['type']['type'];ats=[a['athlete'] for a in e.get('participants',[])];team=names.get(e.get('team',{}).get('id'));minute=e.get('clock',{}).get('displayValue','').replace("'",'');name=ats[0]['displayName'] if ats else ''
  if not team or not name:continue
  if e.get('scoringPlay') and not e.get('shootout'):
   if 'own' in typ:name+=' · autorete'
   if 'penalty' in typ:name+=' · rigore'
   if len(ats)>1 and 'Assisted by ' in e.get('text',''):name+=' · assist '+ats[1]['displayName']
   d['goals'].append([minute,team,name])
  elif typ=='substitution' and len(ats)>1:d['substitutions'].append([minute,team,name,ats[1]['displayName']])
  elif typ in ['yellow-card','red-card','yellow-red-card']:d['cards'].append([minute,team,name+' · '+('ammonizione' if typ=='yellow-card' else 'espulsione')])
 a,b=map(int,m['score'].split(' – '));assert [sum(e[1]==m[h] for e in d['goals']) for h in ['home','away']]==[a,b],'Tabellino incompleto'
 return d

def aggregate(c):
 players={};keepers={};totals={n:dict(name=n,played=0,goalsFor=0,goalsAgainst=0) for n in [x['name'] for x in c['clubs']]};count=0
 for r in c['fixtures']['rounds']:
  for m in r['matches']:
   if not m['played']:continue
   a,b=map(int,m['score'].split(' – '))
   for name,gf,ga in [(m['home'],a,b),(m['away'],b,a)]:totals[name]['played']+=1;totals[name]['goalsFor']+=gf;totals[name]['goalsAgainst']+=ga
   key='|'.join([c['name'],m['date'],m['home'],m['away']]);d=c['details'].get(key)
   if not d:continue
   count+=1
   for e in d['goals']:
    name=e[2].split(' · ')[0]
    if 'autorete' not in e[2]:v=players.setdefault((name,e[1]),dict(name=name,club=e[1],goals=0,assists=0));v['goals']+=1
    if ' · assist ' in e[2]:name=e[2].split(' · assist ',1)[1];v=players.setdefault((name,e[1]),dict(name=name,club=e[1],goals=0,assists=0));v['assists']+=1
   for v in d.get('keepers',[]):
    old=keepers.setdefault((v['name'],v['club']),dict(name=v['name'],club=v['club'],conceded=0,saves=0,games=0,cleanSheets=0))
    for field in ['conceded','saves','games','cleanSheets']:old[field]+=v[field]
 c['live'].update(scorers=sorted([p for p in players.values() if p['goals']],key=lambda p:-p['goals']),assists=sorted([p for p in players.values() if p['assists']],key=lambda p:-p['assists']),detailCount=count,playedCount=sum(t['played'] for t in totals.values())//2,teamTotals=list(totals.values()))
 # Do not erase a verified keeper table when older imported summaries lack keeper rows.
 if len([d for d in c['details'].values() if 'keepers' in d])==count:c['live']['keepers']=sorted(keepers.values(),key=lambda p:(-p['cleanSheets'],p['conceded']))
 else:c['coverage']['keepers']='Ultima classifica verificata; ricostruzione dai tabellini non ancora completa.'

def espn(c,cfg,rosters=False):
 months=['gennaio','febbraio','marzo','aprile','maggio','giugno','luglio','agosto','settembre','ottobre','novembre','dicembre']
 for r in c['fixtures']['rounds']:
  for m in r['matches']:
   if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',m['date']):
    bits=m['date'].split(',')[-1].strip().split();assert len(bits)==3 and bits[1] in months,'Data da verificare';oldkey='|'.join([c['name'],m['date'],m['home'],m['away']]);m['date']=str(datetime.date(int(bits[2]),months.index(bits[1])+1,int(bits[0])));newkey='|'.join([c['name'],m['date'],m['home'],m['away']])
    if oldkey in c['details']:c['details'][newkey]=c['details'].pop(oldkey)
 code=cfg['code'];year=cfg['season'];base='https://site.api.espn.com/apis/site/v2/sports/soccer/'+code;ids={str(x['espnId']):x['name'] for x in c['clubs'] if x.get('espnId')};aliases={norm(k):v for k,v in cfg.get('aliases',{}).items()};byn={norm(x['name']):x['name'] for x in c['clubs']}
 standings=get(f'https://site.web.api.espn.com/apis/v2/sports/soccer/{code}/standings?season={year}');groups=[];rows=[]
 for group in standings['children']:
  gr=[]
  for r in group['standings']['entries']:
   t=r['team'];name=ids.get(t['id']) or aliases.get(norm(t['displayName'])) or byn.get(norm(t['displayName']));assert name in byn.values(),'Partecipante non riconosciuto: '+t['displayName'];ids[t['id']]=name
   next(x for x in c['clubs'] if x['name']==name)['espnId']=t['id'];st={v['name']:int(v.get('value',0)) for v in r['stats']};record={dest:st[src] for src,dest in [('rank','position'),('points','points'),('gamesPlayed','played'),('wins','wins'),('ties','draws'),('losses','losses'),('pointsFor','goalsFor'),('pointsAgainst','goalsAgainst'),('pointDifferential','difference')]};record['name']=name;rows.append(record);gr.append(record)
  groups.append(dict(name=group['name'].replace('Group','Girone'),rows=gr))
 old={m['eventId']:m for r in c['fixtures']['rounds'] for m in r['matches'] if m.get('eventId')};matches=[m for r in c['fixtures']['rounds'] for m in r['matches']];dates={TODAY+datetime.timedelta(days=n) for n in range(-2,3)}
 # Catch up all overdue unplayed fixtures even after a prolonged outage.
 dates|={datetime.date.fromisoformat(m['date']) for m in matches if not m['played'] and m['date']<=str(TODAY)}
 if rosters:dates|={datetime.date.fromisoformat(m['date']) for m in matches if m['played'] and 'keepers' not in c['details'].get('|'.join([c['name'],m['date'],m['home'],m['away']]),{})}
 events={}
 for date in sorted(dates):
  p=get(base+'/scoreboard?dates='+date.strftime('%Y%m%d')+'&limit=1000')
  for e in p.get('events',[]):events[e['id']]=e
 for eid,e in events.items():
  if int(e.get('season',{}).get('year',year))!=year:continue
  comp=e['competitions'][0];ts={x['homeAway']:x for x in comp['competitors']}
  if any(t['id'] not in ids for t in ts.values()):continue
  m=old.get(eid)
  if not m:
   found=[x for x in matches if x['home']==ids[ts['home']['id']] and x['away']==ids[ts['away']['id']]];assert len(found)==1,'Nuova partita: verifica della fase necessaria';m=found[0];m['eventId']=eid
  oldkey='|'.join([c['name'],m['date'],m['home'],m['away']]);prior=(m['score'],m['played']);dt=datetime.datetime.fromisoformat(e['date'].replace('Z','+00:00')).astimezone(ZoneInfo('Europe/Rome'));status=comp['status']['type'];complete=status['completed'];state=status['state'];score=lambda h:str(int(float(ts[h]['score'].get('value',0)))) if isinstance(ts[h].get('score'),dict) else str(ts[h].get('score','0'))
  m.update(date=dt.strftime('%Y-%m-%d'),time=dt.strftime('%H:%M') if comp.get('timeValid',True) else 'Orario da confermare',played=complete,score=score('home')+' – '+score('away') if complete or state=='in' else '–',source='https://www.espn.com/soccer/match/_/gameId/'+eid)
  m['statusLabel']='PARTITA CONCLUSA' if complete else ('IN CORSO · '+status.get('shortDetail','') if state=='in' else 'IN PROGRAMMA')
  if status['name'] not in ['STATUS_FULL_TIME','STATUS_SCHEDULED'] and state!='in':m['statusLabel']=status.get('description',status['name'])
  key='|'.join([c['name'],m['date'],m['home'],m['away']])
  if complete and (key not in c['details'] or prior!=(m['score'],complete) or (TODAY-dt.date()).days<=2 or (rosters and 'keepers' not in c['details'].get(key,{}))):
   try:
    p=get(base+'/summary?event='+eid);d=parse_detail(p,m,ids);ks=[]
    for roster in p.get('rosters',[]):
     name=ids.get(roster['team']['id'])
     for a in roster.get('roster',[]):
      st={v['name']:v.get('value',0) for v in a.get('stats',[])}
      if name and a.get('position',{}).get('abbreviation')=='G' and st.get('appearances'):ks.append(dict(name=a['athlete']['displayName'],club=name,conceded=int(st.get('goalsConceded',0)),saves=int(st.get('saves',0)),games=1,cleanSheets=int(st.get('goalsConceded',0)==0 and a.get('starter') and not a.get('subbedOut'))))
    d['keepers']=ks;c['details'].pop(oldkey,None);c['details'][key]=d
   except Exception:
    # Old events are only retained if they still match the new score/date.
    prior_d=c['details'].get(oldkey)
    if prior_d and [sum(x[1]==m[h] for x in prior_d['goals']) for h in ['home','away']]!=list(map(int,m['score'].split(' – '))):c['details'].pop(oldkey,None)
    c['coverage']['events']='Uno o più tabellini in attesa di dati completi.'
  elif not complete:c['details'].pop(oldkey,None)
 for r in c['fixtures']['rounds']:r.update(source='https://www.espn.com/soccer/fixtures/_/league/'+code,sourceLabel='ESPN',checked=checked());r['matches'].sort(key=lambda m:(m['date'],m['time']))
 latest=max((i for i,r in enumerate(c['fixtures']['rounds']) if any(m['played'] for m in r['matches'])),default=0);c['fixtures'].update(**c['fixtures']['rounds'][latest]);c['live'].update(standings=rows,sourceLabel='ESPN',source=f'https://www.espn.com/soccer/table/_/league/{code}/season/{year}',scorerSource=f'https://www.espn.com/soccer/stats/_/league/{code}/season/{year}',checked=checked())
 if c['name']=='Nations League':c['live']['groups']=groups
 aggregate(c)
 if rosters:
  for club in c['clubs']:
   try:
    p=get(base+f'/teams/{club["espnId"]}/roster?season={year}');previous={str(v.get('espnId')):v for v in club['players']};out=[]
    for a in p.get('athletes',[]):
     role={'G':'1','D':'2','M':'3','F':'4'}.get(a.get('position',{}).get('abbreviation'))
     if not role:continue
     dob=a.get('dateOfBirth','')[:10];prior=previous.get(a['id'],{});matches=[v for v in club['players'] if norm(v.get('shortName',''))==norm(a['displayName']) and v.get('dateOfBirth','')[:10]==dob];prior=prior or (matches[0] if len(matches)==1 else {});v=dict(shortName=a['displayName'],role=role,bibNumber=a.get('jersey',''),dateOfBirth=dob,espnId=a['id'],nationality=a.get('citizenshipCountry',{}).get('abbreviation',''),photo='',height=round(a['height']*2.54) if a.get('height') else '',weight=round(a['weight']*.453592) if a.get('weight') else '')
     if prior.get('dateOfBirth','')[:10]==dob:v.update({k:w for k,w in prior.items() if k.startswith('photo')})
     out.append(v)
    assert 11<=len(out)<=80,'Rosa incompleta';club.update(players=out,checked=checked(),sourceLabel='ESPN',source=f'https://www.espn.com/soccer/team/squad/_/id/{club["espnId"]}/season/{year}')
   except Exception:c['coverage']['rosters']='Alcune rose conservano l’ultima versione verificata.'
 return c

# Football-Data.org: authenticated server-side access, at most 8.5 requests/minute.
_fd_last=0
def fd_get(code,resource):
 global _fd_last
 token=os.environ.get('FOOTBALL_DATA_TOKEN','').strip()
 if not token:raise RuntimeError('Secret FOOTBALL_DATA_TOKEN mancante')
 time.sleep(max(0,7.1-(time.monotonic()-_fd_last)))
 req=urllib.request.Request('https://api.football-data.org/v4/competitions/'+code+resource,headers={'X-Auth-Token':token,'User-Agent':'QuiSport/1.0'})
 _fd_last=time.monotonic()
 with urllib.request.urlopen(req,timeout=40) as response:return json.load(response)

FD_NATIONS=dict(zip('Mexico|Czech Republic|South Korea|South Africa|Canada|Bosnia and Herzegovina|Switzerland|Qatar|Brazil|Scotland|Haiti|Morocco|Paraguay|Turkey|Australia|United States|Ecuador|Germany|Ivory Coast|Curaçao|Netherlands|Sweden|Japan|Tunisia|Belgium|Iran|Egypt|New Zealand|Spain|Uruguay|Saudi Arabia|Cape Verde|Norway|France|Senegal|Iraq|Argentina|Austria|Algeria|Jordan|Colombia|Portugal|Uzbekistan|DR Congo|England|Croatia|Panama|Ghana|Hungary|Italy|Albania|Slovenia|Denmark|Serbia|Poland|Ukraine|Slovakia|Romania|Georgia'.split('|'),'Messico|Cechia|Corea del Sud|Sudafrica|Canada|Bosnia-Herzegovina|Svizzera|Qatar|Brasile|Scozia|Haiti|Marocco|Paraguay|Turchia|Australia|Stati Uniti|Ecuador|Germania|Costa d’Avorio|Curaçao|Paesi Bassi|Svezia|Giappone|Tunisia|Belgio|Iran|Egitto|Nuova Zelanda|Spagna|Uruguay|Arabia Saudita|Capo Verde|Norvegia|Francia|Senegal|Iraq|Argentina|Austria|Algeria|Giordania|Colombia|Portogallo|Uzbekistan|RD Congo|Inghilterra|Croazia|Panama|Ghana|Ungheria|Italia|Albania|Slovenia|Danimarca|Serbia|Polonia|Ucraina|Slovacchia|Romania|Georgia'.split('|')))
FD_NATIONS.update({'Czechia':'Cechia','Korea Republic':'Corea del Sud','Türkiye':'Turchia','USA':'Stati Uniti','United States of America':'Stati Uniti','Congo DR':'RD Congo','Congo':'RD Congo','Cote d’Ivoire':'Costa d’Avorio',"Côte d’Ivoire":'Costa d’Avorio',"Côte d'Ivoire":'Costa d’Avorio','Cabo Verde':'Capo Verde'})
def football_data(c,cfg,*_):
 code=cfg['footballDataCode'];national=code in ['WC','EC'];source='https://www.football-data.org/';names={v['name'] for v in c['clubs']};mapping=cfg.get('footballDataTeams',{})
 def team(t):
  n=mapping.get(str(t.get('id')))
  if national:n=FD_NATIONS.get(t.get('name')) or FD_NATIONS.get(t.get('shortName')) or t.get('name')
  if n not in names:raise ValueError('Squadra da verificare: '+str(t.get('name')))
  return n
 payload=fd_get(code,'/matches?season='+str(cfg['season']));events=payload.get('matches',[])
 if not events:raise ValueError('Calendario vuoto')
 rounds=collections.OrderedDict();tables=[]
 for e in events:
  # Future knockout participants are unknown until qualification; do not invent them.
  if not e.get('homeTeam',{}).get('id') or not e.get('awayTeam',{}).get('id'):continue
  home,away=team(e['homeTeam']),team(e['awayTeam']);dt=datetime.datetime.fromisoformat(e['utcDate'].replace('Z','+00:00')).astimezone(ZoneInfo('Europe/Rome'));status=e['status'];score=e['score'];full=score.get('fullTime',{});a,b=full.get('home'),full.get('away');done=status in ['FINISHED','AWARDED'];inplay=status in ['IN_PLAY','PAUSED','EXTRA_TIME','PENALTY_SHOOTOUT'];stage=e.get('stage','');group=e.get('group');isgroup=stage in ['REGULAR_SEASON','LEAGUE_STAGE','GROUP_STAGE']
  if done and (not isinstance(a,int) or not isinstance(b,int)):raise ValueError('Risultato concluso incompleto')
  label=('Giornata '+str(e['matchday'])) if stage in ['REGULAR_SEASON','LEAGUE_STAGE','GROUP_STAGE'] else {'LAST_16':'Ottavi di finale','QUARTER_FINALS':'Quarti di finale','SEMI_FINALS':'Semifinali','FINAL':'Finale','LAST_32':'Sedicesimi di finale','THIRD_PLACE':'Finale terzo posto'}.get(stage,stage.replace('_',' ').title())
  m=dict(home=home,away=away,date=dt.date().isoformat(),time=dt.strftime('%H:%M'),score=f'{a} – {b}' if isinstance(a,int) and isinstance(b,int) and (done or inplay) else '–',played=done,isGroup=isgroup,source=source,statusLabel='PARTITA CONCLUSA' if done else 'IN CORSO · Dati con ritardo' if inplay else {'POSTPONED':'RINVIATA','CANCELLED':'ANNULLATA','SUSPENDED':'SOSPESA'}.get(status,'IN PROGRAMMA'))
  if group:m['group']=group
  rounds.setdefault(label,dict(label=label,source=source,sourceLabel='football-data.org',checked=checked(),matches=[]))['matches'].append(m)
 if not national:
  p=fd_get(code,'/standings?season='+str(cfg['season']));tables=[g for g in p.get('standings',[]) if g.get('type')=='TOTAL']
  if not tables:raise ValueError('Classifica non disponibile')
  rows=[]
  for g in tables:
   for r in g['table']:rows.append(dict(name=team(r['team']),position=r['position'],points=r['points'],played=r['playedGames'],wins=r['won'],draws=r['draw'],losses=r['lost'],goalsFor=r['goalsFor'],goalsAgainst=r['goalsAgainst'],difference=r['goalDifference']))
 else:
  # Group-only table derived from the provider's match scores; knockout scores do not add league points.
  rows={n:dict(name=n,position=0,points=0,played=0,wins=0,draws=0,losses=0,goalsFor=0,goalsAgainst=0,difference=0) for n in names};groups=collections.defaultdict(set)
  for r in rounds.values():
   for m in r['matches']:
    if not m['isGroup']:continue
    groups[m.get('group','Girone')].update([m['home'],m['away']])
    if not m['played']:continue
    a,b=map(int,m['score'].split(' – '))
    for n,gf,ga in [(m['home'],a,b),(m['away'],b,a)]:
     t=rows[n];t['played']+=1;t['goalsFor']+=gf;t['goalsAgainst']+=ga;t['difference']=t['goalsFor']-t['goalsAgainst'];t['wins']+=gf>ga;t['draws']+=gf==ga;t['losses']+=gf<ga;t['points']+=3 if gf>ga else 1 if gf==ga else 0
  tables=[]
  for group,ns in groups.items():
   ordered=sorted((rows[n] for n in ns),key=lambda t:(-t['points'],-t['difference'],-t['goalsFor'],t['name']))
   for i,t in enumerate(ordered):t['position']=i+1
   tables.append({'label':group.replace('GROUP_','Girone '),'standings':ordered})
  rows=list(rows.values())
 c['details']={};rs=list(rounds.values())
 for r in rs:r['matches'].sort(key=lambda m:(m['date'],m['time']))
 latest=max((i for i,r in enumerate(rs) if any(m['played'] for m in r['matches'])),default=0)
 c['fixtures']=dict(rs[latest],rounds=rs);c['live']=dict(source=source,sourceLabel='football-data.org',checked=checked(),standings=rows,scorers=[],assists=[],keepers=[])
 if national:c['live']['groups']=tables
 c['coverage']={'provider':'football-data','events':'Il piano gratuito non include tabellini dettagliati.','live':'Risultati con ritardo secondo il piano gratuito.'};return c

def main():
 import fcntl
 lock=(ROOT/'updater/.refresh.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX)
 ap=argparse.ArgumentParser();ap.add_argument('--league');ap.add_argument('--rosters',action='store_true');ap.add_argument('--offline-test',action='store_true');args=ap.parse_args();manifest=json.loads((DATA/'manifest.json').read_text());fail=0
 manifest['competitions']={name:state for name,state in manifest['competitions'].items() if name in CFG}
 for snapshot in DATA.glob('*.json'):
  if snapshot.name=='manifest.json':continue
  if re.match(r'^Serie [CD](?:$| ·)',json.loads(snapshot.read_text()).get('name','')):snapshot.unlink()
 for name,cfg in CFG.items():
  if args.league and name!=args.league:continue
  path=DATA/cfg['file'];old=json.loads(path.read_text());c=json.loads(json.dumps(old));c['coverage']={};state=manifest['competitions'][name];state['lastAttempt']=stamp()
  if cfg.get('finished') and cfg.get('provider')!='football-data':state.update(state='concluded',lastError=None);continue
  try:
   if args.offline_test:raise ConnectionError('Simulated source outage')
   candidate=(football_data if cfg.get("provider")=="football-data" else espn)(c,cfg,args.rosters);validate(candidate,old);candidate['updatedAt']=stamp();atomic(path,candidate);state.update(provider=cfg.get('provider'),state='ok',lastSuccess=candidate['updatedAt'],lastError=None,coverage=candidate['coverage'])
   print(name,'OK',flush=True)
  except Exception as e:
   fail+=1;state.update(state='error',lastError='Aggiornamento non riuscito; conservati gli ultimi dati verificati.');print(name,type(e).__name__,str(e)[:160],file=sys.stderr,flush=True)
 manifest.update(lastAttempt=stamp());atomic(DATA/'manifest.json',manifest);lock.close();return 1 if fail else 0
if __name__=='__main__':sys.exit(main())
