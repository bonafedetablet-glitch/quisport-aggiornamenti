"""Sports-data updater. Atomic per-competition commits; credentials stay in env.
Run from any directory: python3 updater/refresh.py [--league NAME] [--rosters] [--offline-test]
Cloud scheduling is supplied by the host, not the visitor's browser.
"""
from pathlib import Path
import argparse,collections,concurrent.futures,datetime,html,json,os,re,sys,tempfile,time,unicodedata,urllib.request,urllib.error
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1];DATA=ROOT/'dist/data';CFG=json.loads((ROOT/'updater/config.json').read_text());NOW=datetime.datetime.now(datetime.timezone.utc);TODAY=NOW.astimezone(ZoneInfo('Europe/Rome')).date()
def clear_unlicensed_clubs(c):
 c['clubs']=[{**{k:v[k] for k in ['name','slug','group','season'] if k in v},'players':[],'source':'','sourceLabel':'','checked':''} for v in c['clubs']]
 return c

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
 # Some providers include ongoing matches in their provisional standings.
 # Accept only an exact whole-table match against completed games or all live scores.
 projected={n:list(v) for n,v in total.items()}
 for m in matches:
  if not m['played'] and m.get('isGroup',True) and m.get('statusLabel','').startswith('IN CORSO') and re.fullmatch(r'\d+ – \d+',m['score']):
   a,b=map(int,m['score'].split(' – '))
   for n,gf,ga in [(m['home'],a,b),(m['away'],b,a)]:
    v=projected.setdefault(n,[0,0,0]);v[0]+=1;v[1]+=gf;v[2]+=ga
 def aligned(expected):return all(expected.get(t['name'],[0,0,0])==[t['played'],t['goalsFor'],t['goalsAgainst']] for t in rows)
 if not (aligned(total) or aligned(projected)):
  # Independent provider endpoints may include one more/fewer completed game.
  # Equal game counts with different goals remain a genuine validation error.
  def one_game_lag(t):
   base=total[t['name']];actual=[t['played'],t['goalsFor'],t['goalsAgainst']]
   if base==actual:return True
   delta=[v-u for u,v in zip(base,actual)]
   return abs(delta[0])==1 and all(v*delta[0]>=0 for v in delta[1:])
  if candidate.get('coverage',{}).get('provider')=='football-data' and all(one_game_lag(t) for t in rows):
   candidate['coverage']['standingsAlignment']='pending'
   candidate['live']['standingsNote']='Classifica fornita da football-data.org. Calendario e classifica possono aggiornarsi in momenti diversi.'
  else:raise AssertionError('Calendario e classifica non allineati')

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

# Football-Data.org: authenticated server-side access, at most 8.5 requests/minute.
_fd_last=0
def fd_get(code,resource):
 global _fd_last
 token=os.environ.get('FOOTBALL_DATA_TOKEN','').strip()
 if not token:raise RuntimeError('Secret FOOTBALL_DATA_TOKEN mancante')
 for attempt in range(3):
  time.sleep(max(0,7.1-(time.monotonic()-_fd_last)))
  req=urllib.request.Request('https://api.football-data.org/v4/competitions/'+code+resource,headers={'X-Auth-Token':token,'User-Agent':'QuiSport/1.0'})
  _fd_last=time.monotonic()
  try:
   with urllib.request.urlopen(req,timeout=40) as response:return json.load(response)
  except urllib.error.HTTPError as exc:
   if exc.code not in [429,500,502,503,504] or attempt==2:raise
   time.sleep(15*(attempt+1))
  except (urllib.error.URLError,TimeoutError,ConnectionError):
   if attempt==2:raise
   time.sleep(5*(attempt+1))

def fd_validate_season(payload,code,season):
 if payload.get('competition',{}).get('code')!=code:raise ValueError('Competizione inattesa dalla fonte')
 starts=[payload['season'].get('startDate','')] if payload.get('season') else [m.get('season',{}).get('startDate','') for m in payload.get('matches',[])]
 if not starts or any(not start.startswith(str(season)+'-') for start in starts):raise ValueError('Stagione inattesa dalla fonte')

FD_NATIONS=dict(zip('Mexico|Czech Republic|South Korea|South Africa|Canada|Bosnia and Herzegovina|Switzerland|Qatar|Brazil|Scotland|Haiti|Morocco|Paraguay|Turkey|Australia|United States|Ecuador|Germany|Ivory Coast|Curaçao|Netherlands|Sweden|Japan|Tunisia|Belgium|Iran|Egypt|New Zealand|Spain|Uruguay|Saudi Arabia|Cape Verde|Norway|France|Senegal|Iraq|Argentina|Austria|Algeria|Jordan|Colombia|Portugal|Uzbekistan|DR Congo|England|Croatia|Panama|Ghana|Hungary|Italy|Albania|Slovenia|Denmark|Serbia|Poland|Ukraine|Slovakia|Romania|Georgia'.split('|'),'Messico|Cechia|Corea del Sud|Sudafrica|Canada|Bosnia-Herzegovina|Svizzera|Qatar|Brasile|Scozia|Haiti|Marocco|Paraguay|Turchia|Australia|Stati Uniti|Ecuador|Germania|Costa d’Avorio|Curaçao|Paesi Bassi|Svezia|Giappone|Tunisia|Belgio|Iran|Egitto|Nuova Zelanda|Spagna|Uruguay|Arabia Saudita|Capo Verde|Norvegia|Francia|Senegal|Iraq|Argentina|Austria|Algeria|Giordania|Colombia|Portogallo|Uzbekistan|RD Congo|Inghilterra|Croazia|Panama|Ghana|Ungheria|Italia|Albania|Slovenia|Danimarca|Serbia|Polonia|Ucraina|Slovacchia|Romania|Georgia'.split('|')))
FD_NATIONS.update({'Czechia':'Cechia','Korea Republic':'Corea del Sud','Türkiye':'Turchia','USA':'Stati Uniti','United States of America':'Stati Uniti','Congo DR':'RD Congo','Congo':'RD Congo','Cote d’Ivoire':'Costa d’Avorio',"Côte d’Ivoire":'Costa d’Avorio',"Côte d'Ivoire":'Costa d’Avorio','Cabo Verde':'Capo Verde'})
def football_data(c,cfg,*_):
 code=cfg['footballDataCode'];national=code in ['WC','EC'];source='https://www.football-data.org/';names={v['name'] for v in c['clubs']};mapping=cfg.get('footballDataTeams',{})
 def team(t):
  n=mapping.get(str(t.get('id')))
  if national:n=FD_NATIONS.get(t.get('name')) or FD_NATIONS.get(t.get('shortName')) or t.get('name')
  if n not in names:raise ValueError('Squadra da verificare: '+str(t.get('name')))
  return n
 payload=fd_get(code,'/matches?season='+str(cfg['season']));fd_validate_season(payload,code,cfg['season']);events=payload.get('matches',[])
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
  p=fd_get(code,'/standings?season='+str(cfg['season']));fd_validate_season(p,code,cfg['season']);tables=[g for g in p.get('standings',[]) if g.get('type')=='TOTAL']
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
   tables.append({'name':group.replace('GROUP_','Girone '),'rows':ordered})
  rows=list(rows.values())
 c['details']={};rs=list(rounds.values())
 for r in rs:r['matches'].sort(key=lambda m:(m['date'],m['time']))
 latest=max((i for i,r in enumerate(rs) if any(m['played'] for m in r['matches'])),default=0)
 c['fixtures']=dict(rs[latest],rounds=rs);c['live']=dict(source=source,sourceLabel='football-data.org',checked=checked(),standings=rows,scorers=[],assists=[],keepers=[])
 if national:c['live']['groups']=tables
 c['coverage']={'provider':'football-data','events':'Il piano gratuito non include tabellini dettagliati.','live':'Risultati con ritardo secondo il piano gratuito.'};return c

# OpenLigaDB: community-maintained Europa League data, ODbL 1.0.
def openliga(c,cfg,*_):
 import hashlib
 code=cfg['openLigaCode'];season=cfg['season'];base='https://api.openligadb.de/';source='https://www.openligadb.de/';license_url='https://opendatacommons.org/licenses/odbl/1-0/';mapping=cfg['openLigaTeams'];names={v['name'] for v in c['clubs']}
 def team(i):
  n=mapping.get(str(i))
  if n not in names:raise ValueError('Squadra OpenLigaDB da verificare: '+str(i))
  return n
 events=get(base+'getmatchdata/'+code+'/'+str(season));table=get(base+'getbltable/'+code+'/'+str(season))
 if not events or not table:raise ValueError('Dati OpenLigaDB incompleti')
 rounds={}
 for e in events:
  if e.get('leagueShortcut')!=code or e.get('leagueSeason')!=season:raise ValueError('Stagione OpenLigaDB non corrispondente')
  home,away=team(e['team1']['teamId']),team(e['team2']['teamId']);dt=datetime.datetime.fromisoformat(e['matchDateTimeUTC'].replace('Z','+00:00')).astimezone(ZoneInfo('Europe/Rome'));done=e['matchIsFinished'];result=next((r for r in e['matchResults'] if r['resultTypeID']==2),None)
  if done and not result:raise ValueError('Risultato finale OpenLigaDB mancante')
  num=e['group']['groupOrderID'];label='Giornata '+str(num)
  if not 1<=num<=8:raise ValueError('Nuova fase Europa League: verificare formato')
  score=f"{result['pointsTeam1']} – {result['pointsTeam2']}" if done else '–'
  match=dict(home=home,away=away,date=dt.date().isoformat(),time=dt.strftime('%H:%M'),score=score,played=done,isGroup=True,source=source,statusLabel='PARTITA CONCLUSA' if done else 'IN PROGRAMMA')
  rounds.setdefault(num,dict(label=label,source=source,sourceLabel='OpenLigaDB',checked=checked(),matches=[]))['matches'].append(match)
 rs=[rounds[n] for n in sorted(rounds)]
 for r in rs:r['matches'].sort(key=lambda m:(m['date'],m['time'],m['home']))
 rows=[dict(name=team(r['teamInfoId']),position=i+1,points=r['points'],played=r['matches'],wins=r['won'],draws=r['draw'],losses=r['lost'],goalsFor=r['goals'],goalsAgainst=r['opponentGoals'],difference=r['goalDiff']) for i,r in enumerate(table)]
 latest=max((i for i,r in enumerate(rs) if any(m['played'] for m in r['matches'])),default=0)
 live=dict(source=source,sourceLabel='OpenLigaDB',checked=checked(),standings=rows,scorers=[],assists=[],keepers=[],license=license_url)
 fixtures=dict(rs[latest],rounds=rs)
 # Offer only the adapted OpenLigaDB database; club profiles and imagery are separate.
 exported=dict(name=c['name'],season=season,source=source,license=license_url,notice='Contains information from OpenLigaDB, available under the Open Database License (ODbL) 1.0. QuiSport adaptations: Italian team names and dates in Europe/Rome. Images and club profiles excluded.',standings=rows,rounds=[dict(label=r['label'],matches=r['matches']) for r in rs])
 body=json.dumps(exported,ensure_ascii=False,separators=(',',':')).encode();filename=hashlib.sha256(body).hexdigest()[:16]+'.json';atomic(DATA/filename,exported);live['openDataFile']=filename
 c.update(fixtures=fixtures,live=live,details={},coverage=dict(provider='openligadb',events='Tabellini individuali non collegati.',live='Aggiornamento periodico da fonte comunitaria; nessuna diretta minuto per minuto.',openDataFile=filename,license=license_url));return c

# SportScore API: display permission with attribution; do not commit its datasets.
def sportscore(c,cfg,*_):
 from urllib.parse import urlencode
 source='https://sportscore.com/';slug=cfg['sportScoreSlug'];names={v['name'] for v in c['clubs']};mapping=cfg['sportScoreTeams']
 def team(n):
  value=mapping.get(n,n)
  if value not in names:raise ValueError('Squadra SportScore da verificare: '+n)
  return value
 def api(kind,**kw):return get(source+'api/v1/'+kind+'/?'+urlencode(dict(sport='football',**kw)))
 # Previous fixtures provide routing dates and round labels only. All scores are replaced.
 index={};dates=set()
 for r in c['fixtures']['rounds']:
  for m in r['matches']:
   key=(m['home'],m['away'])
   if key in index:raise ValueError('Nuova fase: verificare le partite ripetute')
   index[key]=(r['label'],m.get('group'));dates.add(m['date'])
 for offset in range(-2,3):dates.add((TODAY+datetime.timedelta(days=offset)).isoformat())
 def day(date):
  data=api('fixtures',date=date,competition=slug,limit=200)
  if data.get('date')!=date or data.get('sport')!='football' or data.get('count')!=len(data.get('matches',[])) or data['count']>=200:raise ValueError('Risposta calendario incompleta')
  time.sleep(.15)
  return data['matches']
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:events=[m for batch in pool.map(day,sorted(dates)) for m in batch]
 rounds={label:dict(label=label,source=source,sourceLabel='SportScore',checked=checked(),matches=[]) for label in dict.fromkeys(v[0] for v in index.values())};seen=set()
 for e in events:
  home,away=team(e['home']),team(e['away']);key=(home,away)
  if key not in index:raise ValueError('Nuova partita/fase: verificare calendario SportScore')
  if key in seen:raise ValueError('Partita duplicata SportScore')
  seen.add(key);label,group=index[key];dt=datetime.datetime.fromisoformat(e['time']).astimezone(ZoneInfo('Europe/Rome'))
  if not cfg['season']<=dt.year<=cfg['season']+1:raise ValueError('Stagione SportScore non corrispondente')
  status=e['status'];done=status=='finished';inplay=status in ['live','inprogress','in_progress'];a,b=e.get('home_score'),e.get('away_score')
  if done and (a is None or b is None):raise ValueError('Risultato finale mancante')
  score=f'{int(a)} – {int(b)}' if (done or inplay) and a is not None and b is not None else '–'
  m=dict(home=home,away=away,date=dt.date().isoformat(),time=dt.strftime('%H:%M'),score=score,played=done,isGroup=True,source=source,statusLabel='PARTITA CONCLUSA' if done else 'IN CORSO · Aggiornamento periodico' if inplay else 'RINVIATA' if status=='postponed' else 'ANNULLATA' if status=='cancelled' else 'IN PROGRAMMA')
  if group:m['group']=group
  rounds[label]['matches'].append(m)
 if seen!=set(index):raise ValueError('Calendario SportScore incompleto: '+str(len(seen))+'/'+str(len(index)))
 rs=list(rounds.values())
 for r in rs:r['matches'].sort(key=lambda m:(m['date'],m['time'],m['home']))
 groups=[]
 if c['name']=='Conference League':
  # This provider's standings include qualifying rounds. Derive only league-stage rows.
  rows={n:dict(name=n,position=0,points=0,played=0,wins=0,draws=0,losses=0,goalsFor=0,goalsAgainst=0,difference=0) for n in names}
  for r in rs:
   for m in r['matches']:
    if not m['played']:continue
    a,b=map(int,m['score'].split(' – '))
    for n,gf,ga in [(m['home'],a,b),(m['away'],b,a)]:
     t=rows[n];t['played']+=1;t['goalsFor']+=gf;t['goalsAgainst']+=ga;t['difference']=t['goalsFor']-t['goalsAgainst'];t['wins']+=int(gf>ga);t['draws']+=int(gf==ga);t['losses']+=int(gf<ga);t['points']+=3 if gf>ga else 1 if gf==ga else 0
  rows=sorted(rows.values(),key=lambda t:(-t['points'],-t['difference'],-t['goalsFor'],t['name']))
  for i,t in enumerate(rows):t['position']=i+1
 else:
  payload=api('standings',slug=slug)
  if payload.get('competition_slug')!=slug:raise ValueError('Classifica di altra competizione')
  previous_groups={frozenset(t['name'] for t in g['rows']):g['name'] for g in c['live'].get('groups',[])};rows=[]
  for table in payload['tables']:
   group_rows=[dict(name=team(t['team']),position=int(t['pos']),points=int(t['pts']),played=int(t['p']),wins=int(t['w']),draws=int(t['d']),losses=int(t['l']),goalsFor=int(t['gf']),goalsAgainst=int(t['ga']),difference=int(t['gd'])) for t in table['rows']]
   if c['name']=='Nations League':
    label=previous_groups.get(frozenset(t['name'] for t in group_rows))
    if not label:raise ValueError('Girone Nations League da verificare')
    groups.append(dict(name=label,rows=group_rows))
   rows.extend(group_rows)
  if c['name']=='Nations League':
   if len(groups)!=len(previous_groups):raise ValueError('Gironi Nations League incompleti')
   groups.sort(key=lambda g:g['name'])
 latest=max((i for i,r in enumerate(rs) if any(m['played'] for m in r['matches'])),default=0)
 live=dict(source=source,sourceLabel='SportScore',checked=checked(),standings=rows,scorers=[],assists=[],keepers=[],playedCount=sum(m['played'] for r in rs for m in r['matches']),detailCount=0)
 if groups:live['groups']=groups
 if c['name']=='Conference League':live['standingsNote']='Classifica calcolata sui risultati della fase campionato. Parità ordinate per punti, differenza reti e gol fatti; gli ulteriori criteri UEFA non sono applicati.'
 c.update(fixtures=dict(rs[latest],rounds=rs),live=live,details={},coverage=dict(provider='sportscore',events='Tabellini individuali non collegati.',live='Aggiornamento periodico · SportScore',attribution='Powered by SportScore',terms=source+'developers/terms/'))
 return c

def main():
 import fcntl
 lock=(ROOT/'updater/.refresh.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX)
 ap=argparse.ArgumentParser();ap.add_argument('--league');ap.add_argument('--rosters',action='store_true');ap.add_argument('--offline-test',action='store_true');args=ap.parse_args();
 for seed in DATA.glob('*.json'):
  value=json.loads(seed.read_text())
  if value.get('schema')==1 and isinstance(value.get('clubs'),list):atomic(seed,clear_unlicensed_clubs(value))
 manifest=json.loads((DATA/'manifest.json').read_text());fail=0;critical=[];warnings=[]
 manifest['competitions']={name:state for name,state in manifest['competitions'].items() if name in CFG}
 for snapshot in DATA.glob('*.json'):
  if snapshot.name=='manifest.json':continue
  if re.match(r'^Serie [CD](?:$| ·)',json.loads(snapshot.read_text()).get('name','')):snapshot.unlink()
 remote=None
 for name,cfg in CFG.items():
  if args.league and name!=args.league:continue
  path=DATA/cfg['file'];old=json.loads(path.read_text());c=json.loads(json.dumps(old));c['coverage']={};state=manifest['competitions'][name];state['lastAttempt']=stamp()
  if cfg.get('finished') and cfg.get('provider')!='football-data':state.update(state='concluded',lastError=None);continue
  try:
   if cfg.get('provider')=='sportscore' and not args.offline_test:
    if remote is None:remote=get('https://quisport.bonafedetablet.workers.dev/data/manifest.json')
    prior=remote['competitions'][name];filename=prior['file']
    if not re.fullmatch(r'[a-f0-9]{16}\.json',filename):raise ValueError('Versione precedente non valida')
    old=get('https://quisport.bonafedetablet.workers.dev/data/'+filename)
    if old.get('name')!=name:raise ValueError('Snapshot precedente non valido')
    atomic(path,old);c=json.loads(json.dumps(old))
   if args.offline_test:raise ConnectionError('Simulated source outage')
   candidate=({"football-data":football_data,"openligadb":openliga,"sportscore":sportscore}[cfg["provider"]])(c,cfg,args.rosters);clear_unlicensed_clubs(candidate);validate(candidate,old);candidate['updatedAt']=stamp();atomic(path,candidate);state.update(provider=cfg.get('provider'),state='ok',lastSuccess=candidate['updatedAt'],lastError=None,coverage=candidate['coverage'],consecutiveFailures=0)
   print(name,'OK',flush=True)
  except Exception as e:
   fail+=1;state.update(state='error',lastError='Aggiornamento non riuscito; conservati gli ultimi dati verificati.',consecutiveFailures=state.get('consecutiveFailures',0)+1)
   message=name+': '+type(e).__name__+' · '+str(e)[:200];warnings.append(message)
   try:age=(datetime.datetime.now(datetime.timezone.utc)-datetime.datetime.fromisoformat(state['lastSuccess'])).total_seconds()
   except (KeyError,ValueError,TypeError):age=float('inf')
   recoverable=isinstance(e,(AssertionError,ValueError,TimeoutError,ConnectionError,urllib.error.URLError)) and not (isinstance(e,urllib.error.HTTPError) and e.code in [401,403])
   if not recoverable or age>=6*3600:critical.append(message)
   print('Fonte temporaneamente non disponibile; dati conservati:',message,file=sys.stderr,flush=True)
 manifest.update(lastAttempt=stamp());atomic(DATA/'manifest.json',manifest);lock.close()
 summary=os.environ.get('GITHUB_STEP_SUMMARY')
 if summary:
  with open(summary,'a') as f:
   f.write('## Verifica dati QuiSport\n\n')
   f.write('Fonti da ricontrollare: '+str(fail)+'. Ultimi dati validi conservati.\n\n' if fail else 'Tutte le competizioni selezionate aggiornate e verificate.\n')
   for message in warnings:f.write('- '+message.replace('\n',' ')+'\n')
 if critical:
  print('Intervento necessario: dati non aggiornati da almeno 6 ore o errore interno.',file=sys.stderr)
 return 1 if critical else 0
if __name__=='__main__':sys.exit(main())
