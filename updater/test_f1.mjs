import test from 'node:test';
import assert from 'node:assert/strict';
import {collectF1,refreshF1} from './f1.mjs';
const year=new Date().getUTCFullYear();
const drivers=[1,2,3].map(n=>({driverId:'d'+n,givenName:'Pilota',familyName:String(n),code:'P'+n}));
const ctor={constructorId:'team',name:'Team'};
const calendar={season:String(year),round:'1',raceName:'Test Grand Prix',Circuit:{circuitName:'Test Circuit',Location:{country:'Italy',locality:'Test'}},date:`${year}-03-01`};
const results=drivers.map((Driver,i)=>({Driver,Constructor:ctor,position:String(i+1),points:String(25-i*7),grid:String(i+1),laps:'50',status:'Finished'}));
function mockFetch(wrong=false){return async url=>{
 const u=new URL(url),path=u.pathname,offset=Number(u.searchParams.get('offset'));let value,total,limit=100;
 if(path.includes('driverstandings')){total=3;value={StandingsTable:{StandingsLists:[{season:String(year),round:'1',DriverStandings:drivers.map((Driver,i)=>({Driver,Constructors:[ctor],position:String(i+1),points:'25',wins:'1'}))}]}};}
 else if(path.includes('constructorstandings')){total=1;value={StandingsTable:{StandingsLists:[{season:String(year),round:'1',ConstructorStandings:[{Constructor:ctor,position:'1',points:'50',wins:'1'}]}]}};}
 else if(path.includes('results')){total=3;limit=2;value={RaceTable:{Races:[{...calendar,Results:results.slice(offset,offset+2)}]}};}
 else if(path.includes('qualifying')||path.includes('sprint')){total=0;value={RaceTable:{Races:[]}};}
 else {total=1;value={RaceTable:{Races:[{...calendar,season:String(wrong?year-1:year)}]}};}
 return new Response(JSON.stringify({MRData:{offset:String(offset),limit:String(limit),total:String(total),...value}}));
};}
const realFetch=globalThis.fetch,realTimeout=globalThis.setTimeout;
function setup(t,fetcher){globalThis.fetch=fetcher;globalThis.setTimeout=(fn)=>{queueMicrotask(fn);return 1;};t.after(()=>{globalThis.fetch=realFetch;globalThis.setTimeout=realTimeout;});}
test('merge a race split across result pages without losing drivers',async t=>{setup(t,mockFetch());const d=await collectF1(year);assert.equal(d.races[0].results.length,3);assert.deepEqual(d.races[0].results.map(r=>r.position),[1,2,3]);assert.equal(d.constructors.length,1);});
test('reject another season',async t=>{setup(t,mockFetch(true));await assert.rejects(collectF1(year),/stagione/);});
test('source outage keeps verified cloud snapshot',async t=>{setup(t,async()=>new Response('',{status:503}));const saved=new Map([['f1-current','verified-data']]);const env={QS_DATA:{get:async()=>null,put:async(k,v)=>saved.set(k,v)}};await assert.rejects(refreshF1(env));assert.equal(saved.get('f1-current'),'verified-data');assert.equal(JSON.parse(saved.get('f1-status')).state,'pending');});
test('a shortened calendar cannot replace the verified snapshot',async t=>{setup(t,mockFetch());const saved=new Map([['f1-current','verified-data']]);const env={QS_DATA:{get:async()=>({season:year,races:[{results:[]},{results:[]}]}),put:async(k,v)=>saved.set(k,v)}};await assert.rejects(refreshF1(env),/meno dati/);assert.equal(saved.get('f1-current'),'verified-data');});
