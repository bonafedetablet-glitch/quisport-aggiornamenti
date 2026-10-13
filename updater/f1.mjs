const SOURCE='https://api.jolpi.ca/ergast/f1/';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function pages(season,path,key){
 let offset=0,total=null,out=[];
 do{
  await sleep(300);
  let body;
  for(let attempt=0;attempt<3;attempt++){
   const response=await fetch(`${SOURCE}${season}${path?'/'+path:''}.json?limit=100&offset=${offset}`,{headers:{'User-Agent':'QuiSport/1.0 (non-commercial racing results)'},signal:AbortSignal.timeout(30000)});
   if(response.ok){body=(await response.json()).MRData;break;}
   if(![429,500,502,503,504].includes(response.status)||attempt===2)throw new Error('Fonte Formula 1 non disponibile: '+response.status);
   await sleep(2000*(attempt+1));
  }
  if(!body||Number(body.offset)!==offset)throw new Error('Paginazione F1 inattesa');
  const items=key==='Races'?body.RaceTable?.Races:body.StandingsTable?.StandingsLists;
  if(!Array.isArray(items))throw new Error('Risposta F1 incompleta');
  total=Number(body.total);if(!Number.isFinite(total)||total<0||total>2000)throw new Error('Conteggio F1 inatteso');
  out.push(...items);offset+=Number(body.limit);
  if(!Number.isFinite(offset)||offset<=0||offset>2100)throw new Error('Limite pagine F1');
 }while(offset<total);
 return out;
}
function driver(d){return {id:d.driverId,name:[d.givenName,d.familyName].join(' '),code:d.code||d.familyName.slice(0,3).toUpperCase(),number:d.permanentNumber||'',nationality:d.nationality};}
function team(c){return {id:c.constructorId,name:c.name};}
function result(r,type){return {position:Number(r.position),driver:driver(r.Driver),team:team(r.Constructor),points:Number(r.points||0),grid:Number(r.grid||0),laps:Number(r.laps||0),status:r.status||'',time:r.Time?.time||'',q1:r.Q1||'',q2:r.Q2||'',q3:r.Q3||'',fastestLap:r.FastestLap?.Time?.time||'',fastestLapRank:r.FastestLap?.rank||'',type};}
export async function collectF1(season){
 const calendar=await pages(season,'','Races');
 if(!calendar.length||calendar.some(r=>String(r.season)!==String(season)))throw new Error('Calendario stagione F1 non valido');
 const races=calendar.map(r=>({round:Number(r.round),name:r.raceName,circuit:r.Circuit.circuitName,country:r.Circuit.Location.country,city:r.Circuit.Location.locality,date:r.date,time:r.time||null,qualifying:r.Qualifying||null,sprint:r.Sprint||null,practice:[r.FirstPractice,r.SecondPractice,r.ThirdPractice].filter(Boolean),results:[],qualifyingResults:[],sprintResults:[]}));
 const ids=new Map(races.map(r=>[r.round,r]));
 if(ids.size!==races.length||races.some(r=>!/^\d{4}-\d{2}-\d{2}$/.test(r.date)))throw new Error('Gare duplicate o data F1 non valida');
 for(const [endpoint,rawKey,key] of [['results','Results','results'],['qualifying','QualifyingResults','qualifyingResults'],['sprint','SprintResults','sprintResults']]){
  for(const raw of await pages(season,endpoint,'Races')){
   const race=ids.get(Number(raw.round));if(!race||String(raw.season)!==String(season))throw new Error('Risultato F1 di altra gara/stagione');
   race[key].push(...raw[rawKey].map(r=>result(r,endpoint)));
  }
 }
 for(const race of races)for(const key of ['results','qualifyingResults','sprintResults']){
  race[key].sort((a,b)=>a.position-b.position);
  if(new Set(race[key].map(r=>r.driver.id)).size!==race[key].length||race[key].some(r=>!Number.isInteger(r.position)||r.position<1||r.points<0))throw new Error('Risultati F1 duplicati o incompleti');
 }
 const ds=await pages(season,'driverstandings','StandingsLists'),cs=await pages(season,'constructorstandings','StandingsLists');
 const drivers=(ds[0]?.DriverStandings||[]).map(r=>({position:Number(r.position),points:Number(r.points),wins:Number(r.wins),driver:driver(r.Driver),teams:r.Constructors.map(team)}));
 const constructors=(cs[0]?.ConstructorStandings||[]).map(r=>({position:Number(r.position),points:Number(r.points),wins:Number(r.wins),team:team(r.Constructor)}));
 if(races.some(r=>r.results.length)&&(!drivers.length||!constructors.length))throw new Error('Classifiche F1 mancanti');
 if([...ds,...cs].some(s=>String(s.season)!==String(season)))throw new Error('Classifica F1 di altra stagione');
 return {schema:1,sport:'f1',season,updatedAt:new Date().toISOString(),source:'https://jolpi.ca/',license:'https://creativecommons.org/licenses/by-nc-sa/4.0/',attribution:'Dati Jolpica-F1 · CC BY-NC-SA 4.0. Riordinati e adattati per QuiSport.',races,drivers,constructors,standingsRound:Number(ds[0]?.round||0)};
}
export async function refreshF1(env){
 try{
  const data=await collectF1(new Date().getUTCFullYear());
  const old=await env.QS_DATA.get('f1-current','json');
  if(old?.season===data.season&&(data.races.length<old.races.length||data.races.reduce((n,r)=>n+r.results.length,0)<old.races.reduce((n,r)=>n+r.results.length,0)))throw new Error('La fonte ha restituito meno dati F1 della versione verificata');
  await env.QS_DATA.put('f1-current',JSON.stringify(data));
  await env.QS_DATA.put('f1-status',JSON.stringify({state:'ok',lastAttempt:new Date().toISOString(),lastSuccess:data.updatedAt}));
  return data;
 }catch(e){await env.QS_DATA.put('f1-status',JSON.stringify({state:'pending',lastAttempt:new Date().toISOString()}));console.error('Aggiornamento F1 conservato:',e.message);throw e;}
}
export async function f1Response(request,env){
 const data=await env.QS_DATA.get('f1-current','json');
 if(!data)return env.ASSETS.fetch(new Request(new URL('/f1-data.json',request.url),request));
 return new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json; charset=utf-8','Cache-Control':'public, max-age=300'}});
}

if(typeof process!=='undefined'&&process.argv[1]?.endsWith('/f1.mjs')){
 const fs=await import('node:fs/promises');const path='dist/f1-data.json';
 let old=null;try{old=JSON.parse(await fs.readFile(path,'utf8'));}catch{}
 try{
  const data=await collectF1(new Date().getUTCFullYear());
  if(old?.season===data.season&&(data.races.length<old.races.length||data.races.reduce((n,r)=>n+r.results.length,0)<old.races.reduce((n,r)=>n+r.results.length,0)))throw Error('La fonte ha restituito meno dati F1 della versione verificata');
  await fs.writeFile(path+'.tmp',JSON.stringify(data));await fs.rename(path+'.tmp',path);
  console.log('Formula 1 aggiornata:',data.races.length,'Gran Premi,',data.races.filter(r=>r.results.length).length,'gare concluse.');
 }catch(e){console.error('Formula 1: ultimi dati validi conservati.',e.message);process.exitCode=old&&Date.now()-Date.parse(old.updatedAt)<6*3600000?0:1;}
}
