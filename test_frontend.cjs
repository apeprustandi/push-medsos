// Run with node --test test_frontend.cjs; no browser or dependencies required.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/static/index.html','utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const target={url:'https://vt.tiktok.com/demo/',platform:'tiktok',service:'views'};
function setup(fetch){
  const elements={};
  for(const id of ['claim-form','fields','platform','service','url','availability','submit','button-label','status','response-card','http-status','empty','response-content','network-notice','retry','result','elapsed']){
    elements[id]={value:'',textContent:'',hidden:false,disabled:false,dataset:{},events:{},attrs:{},addEventListener(event,fn){this.events[event]=fn},setAttribute(key,value){this.attrs[key]=value}};
    Object.defineProperty(elements[id],'innerHTML',{set(){throw Error('Unsafe HTML rendering')}});
  }
  elements.url.value='  '+target.url+'  ';
  elements.platform.value='tiktok';elements.service.value='views';
  for(const id of ['platform','service']){
    Object.defineProperty(elements[id],'selectedIndex',{get(){return 0}});
    Object.defineProperty(elements[id],'options',{get(){return [{text:elements[id].value}]}});
  }
  const calls=[];
  vm.runInNewContext(source,{document:{getElementById:id=>elements[id]},performance:{now:()=>1000},AbortController,setTimeout,clearTimeout,fetch:async(...args)=>{calls.push(args);return fetch(...args)}});
  return {elements,calls,submit:()=>elements['claim-form'].events.submit({preventDefault(){}})};
}
function response(status,body,headers={}){return {status,ok:status>=200&&status<300,json:async()=>body,headers:{get:key=>headers[key]||null}}}
function body(message='Permintaan berhasil.'){return {message,request_id:'req-test',data:target}}
test('form sends exactly url/platform/service without a key and displays JSON',async()=>{
  const {elements,calls,submit}=setup(async()=>response(200,body()));
  await submit();
  assert.equal(calls[0][0],'/api/claim');
  assert.equal(calls[0][1].method,'POST');
  assert.deepEqual(JSON.parse(calls[0][1].body),target);
  assert.deepEqual(Object.keys(calls[0][1].headers),['Content-Type']);
  assert.deepEqual(JSON.parse(elements.result.textContent),body());
  assert.equal(elements['http-status'].textContent,'HTTP 200');
  assert.equal(elements.fields.disabled,false);
});
test('429 displays the API response and Retry-After',async()=>{
  const {elements,submit}=setup(async()=>response(429,body('Batas layanan tercapai.'),{'Retry-After':'120'}));
  await submit();
  assert.equal(elements['http-status'].textContent,'HTTP 429');
  assert.equal(elements['http-status'].dataset.tone,'error');
  assert.equal(elements.retry.hidden,false);
  assert.match(elements.retry.textContent,/120 detik/);
  assert.equal(JSON.parse(elements.result.textContent).message,'Batas layanan tercapai.');
});
test('202 is displayed as pending',async()=>{
  const {elements,submit}=setup(async()=>response(202,body('Permintaan sedang diproses.')));
  await submit();assert.equal(elements['http-status'].dataset.tone,'pending');
});
test('a second submit is blocked while the first is processing',async()=>{
  let resolve;
  const pending=new Promise(done=>resolve=done);
  const {elements,calls,submit}=setup(()=>pending);
  const first=submit();
  assert.equal(elements.fields.disabled,true);
  await submit();assert.equal(calls.length,1);
  resolve(response(200,body()));await first;
  assert.equal(elements.fields.disabled,false);
  assert.equal(elements['response-card'].attrs['aria-busy'],'false');
});
test('network failure restores the form and does not fabricate a response',async()=>{
  const {elements,submit}=setup(async()=>{throw Error('private internal network error')});
  await submit();
  assert.equal(elements.fields.disabled,false);
  assert.equal(elements['response-content'].hidden,true);
  assert.match(elements['network-notice'].textContent,/belum dapat dipastikan/);
  assert.doesNotMatch(elements['network-notice'].textContent,/private/);
});
test('unreadable upstream HTML is never rendered',async()=>{
  const {elements,submit}=setup(async()=>({status:502,ok:false,json:async()=>{throw Error('<html>private upstream</html>')}}));
  await submit();
  assert.equal(elements['http-status'].textContent,'HTTP 502');
  assert.equal(elements['response-content'].hidden,true);
  assert.doesNotMatch(elements['network-notice'].textContent,/private upstream/);
});
test('response fields are rendered as text and extra provider fields are omitted',async()=>{
  const raw={...body('<img src=x onerror=alert(1)>'),private:'private upstream'};
  const {elements,submit}=setup(async()=>response(200,raw));
  await submit();
  assert.equal(JSON.parse(elements.result.textContent).message,raw.message);
  assert.equal('private' in JSON.parse(elements.result.textContent),false);
});
test('unsupported selection explains HTTP 422',()=>{
  const {elements}=setup(async()=>response(422,body()));
  elements.platform.value='instagram';elements.platform.events.change();
  assert.match(elements.availability.textContent,/HTTP 422/);
  assert.match(elements.url.placeholder,/instagram/);
});
