(() => {
  'use strict';
  const page = document.body.dataset.page;
  const csrf = document.querySelector('meta[name="pageload-csrf"]')?.content || '';
  const base = document.querySelector('base')?.href || `${location.origin}/`;
  const url = path => new URL(path.replace(/^\//, ''), base).toString();
  const esc = value => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set('Accept', 'application/json');
    if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
    if (!['GET','HEAD'].includes((options.method || 'GET').toUpperCase())) headers.set('X-PageLoad-CSRF', csrf);
    const response = await fetch(url(path), {...options, headers});
    if (response.status === 204) return null;
    const body = await response.json().catch(() => ({error: `HTTP ${response.status}`}));
    if (!response.ok) {
      const detail = Array.isArray(body.detail) ? body.detail.map(item => item.msg).join(' · ') : body.detail;
      throw new Error(body.error || detail || 'Die Aktion konnte nicht ausgeführt werden.');
    }
    return body;
  }

  function toast(message, kind = '') {
    const region = document.getElementById('toasts');
    if (!region) return;
    const item = document.createElement('div');
    item.className = `toast ${kind}`;
    item.textContent = message;
    region.append(item);
    setTimeout(() => item.remove(), 5000);
  }

  const statusNames = {ready:'Bereit',scheduled:'Geplant',running:'Läuft',paused:'Pausiert',completed:'Beendet',stopped:'Abgebrochen',error:'Fehler',interrupted:'Unterbrochen',waiting_capacity:'Wartet auf Kapazität'};
  const status = value => `<span class="status status-${esc(value)}">${esc(statusNames[value] || value)}</span>`;
  const date = value => value ? new Intl.DateTimeFormat('de-CH',{dateStyle:'short',timeStyle:'short'}).format(new Date(value)) : '–';
  const duration = seconds => {
    seconds = Math.max(0, Math.round(Number(seconds) || 0));
    const d = Math.floor(seconds / 86400), h = Math.floor(seconds % 86400 / 3600), m = Math.floor(seconds % 3600 / 60), s = seconds % 60;
    return `${d ? `${d}d ` : ''}${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  };
  const ms = value => value == null ? '–' : value >= 1000 ? `${(value/1000).toFixed(2)} s` : `${Math.round(value)} ms`;
  const number = value => new Intl.NumberFormat('de-CH').format(Number(value) || 0);
  const bytes = value => { let n=Number(value)||0, i=0; const units=['B','KB','MB','GB','TB']; while(n>=1024&&i<4){n/=1024;i++;} return `${n.toFixed(i?1:0)} ${units[i]}`; };

  function initNav() {
    const button = document.getElementById('menu-button');
    const scrim = document.getElementById('sidebar-scrim');
    const close = () => { document.body.classList.remove('nav-open'); button?.setAttribute('aria-expanded','false'); };
    button?.addEventListener('click', () => { const open=document.body.classList.toggle('nav-open'); button.setAttribute('aria-expanded',String(open)); });
    scrim?.addEventListener('click', close);
  }

  const testRow = t => `<tr><td><a class="name" href="${url(`tests/${t.id}`)}">${esc(t.name)}</a></td><td><span class="table-url" title="${esc(t.url)}">${esc(t.url)}</span></td><td>${date(t.started_at || t.start_at)}</td><td>${date(t.finished_at)}</td><td>${status(t.status)}</td><td>${number(t.total_requests)}</td><td>${Number(t.success_rate||0).toFixed(2)} %</td><td>${ms(t.average_ms)}</td><td class="actions"><a title="Öffnen" href="${url(`tests/${t.id}`)}">Öffnen</a><button data-action="duplicate" data-id="${t.id}">Duplizieren</button><button data-action="restart" data-id="${t.id}">Erneut starten</button>${t.reporting_enabled ? `<a href="${url(`tests/${t.id}`)}">Report</a>`:''}<button data-action="delete" data-id="${t.id}">Löschen</button></td></tr>`;

  async function loadDashboard() {
    try {
      const data = await api('api/dashboard');
      const c = data.cards;
      const metrics = [
        ['Aktive Tests',c.active_tests,'laufen oder pausiert'],['Geplante Tests',c.scheduled_tests,'zukünftige Starts'],['Abgeschlossene Tests',c.completed_tests,'regulär beendet'],['Aufrufe heute',number(c.calls_today),'seit Mitternacht UTC'],['Aufrufe insgesamt',number(c.calls_total),'gespeicherte Requests'],['Erfolgsquote',`${c.success_rate.toFixed(2)} %`,'HTTP 2xx/3xx'],['Fehlerquote',`${c.error_rate.toFixed(2)} %`,'alle Fehlertypen'],['Ø Ladezeit',ms(c.average_ms),'alle Tests'],['P95 Ladezeit',ms(c.p95_ms),'95. Perzentil'],['Aktive Sessions',c.active_browser_sessions,'aktuell geöffnet']
      ];
      const cards=document.getElementById('dashboard-cards');
      cards.innerHTML=metrics.map(x=>`<article class="metric-card"><span>${x[0]}</span><strong>${x[1]}</strong><small>${x[2]}</small></article>`).join(''); cards.removeAttribute('aria-busy');
      const rows=document.getElementById('dashboard-tests');
      rows.innerHTML=data.tests.length ? data.tests.map(t=>`<tr><td><a class="name" href="${url(`tests/${t.id}`)}">${esc(t.name)}</a></td><td><span class="table-url">${esc(t.url)}</span></td><td>${status(t.status)}</td><td>${date(t.started_at||t.start_at)}</td><td>${duration(t.runtime_seconds)}</td><td>${number(t.total_requests)}${t.max_requests?` / ${number(t.max_requests)}`:''}</td><td>${t.frequency_per_minute}</td><td>${number(t.successful_requests)}</td><td>${number(t.failed_requests)}</td><td>${ms(t.average_ms)}</td></tr>`).join('') : '<tr><td colspan="10" class="empty">Noch keine Tests vorhanden.</td></tr>';
    } catch (e) { toast(e.message,'error'); }
  }

  let allTests=[];
  async function loadTests() {
    try { allTests=await api('api/tests'); renderTests(); } catch(e){toast(e.message,'error');}
  }
  function renderTests(){
    const query=(document.getElementById('test-search')?.value||'').toLowerCase(), filter=document.getElementById('test-status-filter')?.value||'';
    const items=allTests.filter(t=>(!filter||t.status===filter)&&(!query||`${t.name} ${t.url}`.toLowerCase().includes(query)));
    document.getElementById('tests-table').innerHTML=items.length?items.map(testRow).join(''):'<tr><td colspan="9" class="empty">Keine passenden Tests.</td></tr>';
  }
  async function testAction(event){
    const button=event.target.closest('[data-action]'); if(!button)return;
    const {action,id}=button.dataset;
    try {
      if(action==='delete'){if(!confirm('Test und zugehörige Einzelrequests wirklich löschen?'))return;await api(`api/tests/${id}`,{method:'DELETE'});toast('Test gelöscht.');}
      if(action==='duplicate'){const t=await api(`api/tests/${id}/duplicate`,{method:'POST'});toast('Test dupliziert.');location.href=url(`tests/${t.id}`);return;}
      if(action==='restart'){const t=await api(`api/tests/${id}/restart`,{method:'POST'});toast('Neue Testausführung gestartet.');location.href=url(`tests/${t.id}`);return;}
      await loadTests();
    } catch(e){toast(e.message,'error');}
  }

  function conditionalForms(){
    const update=()=>document.querySelectorAll('[data-show]').forEach(el=>{const [name,value]=el.dataset.show.split(':');const input=document.querySelector(`[name="${name}"]:checked`)||document.querySelector(`[name="${name}"]`);el.classList.toggle('hidden',input?.value!==value);});
    document.querySelectorAll('input,select').forEach(el=>el.addEventListener('change',update)); update();
  }
  function valueOrNull(form,name,numeric=false){const raw=form.elements[name]?.value;return raw===''||raw==null?null:numeric?Number(raw):raw;}
  function testPayload(form){
    const stay=form.elements.stay_mode.value;
    return {
      name:form.elements.name.value,url:form.elements.url.value,mode:form.elements.mode.value,start_type:form.elements.start_type.value,
      start_at:valueOrNull(form,'start_at')?form.elements.start_at.value:null,timezone:form.elements.timezone?.value||'Europe/Zurich',
      frequency_per_minute:Number(form.elements.frequency_per_minute.value),frequency_mode:form.elements.frequency_mode.value,stay_mode:stay,
      stay_min_seconds:stay==='fixed'?Number(form.elements.stay_fixed_seconds.value):stay==='random'?Number(form.elements.stay_min_seconds.value):0,
      stay_max_seconds:stay==='fixed'?Number(form.elements.stay_fixed_seconds.value):stay==='random'?Number(form.elements.stay_max_seconds.value):0,
      duration_mode:form.elements.duration_mode.value,duration_days:Number(form.elements.duration_days.value||0),duration_hours:Number(form.elements.duration_hours.value||0),duration_minutes:Number(form.elements.duration_minutes.value||0),
      end_at:valueOrNull(form,'end_at')?form.elements.end_at.value:null,max_requests:valueOrNull(form,'max_requests',true),max_concurrency:Number(form.elements.max_concurrency.value),
      proxy_mode:form.elements.proxy_mode.value,proxy_id:valueOrNull(form,'proxy_id',true),reporting_enabled:form.elements.reporting_enabled.checked,authorization_confirmed:form.elements.authorization_confirmed.checked,
      abort_error_rate_percent:valueOrNull(form,'abort_error_rate_percent',true),abort_consecutive_errors:valueOrNull(form,'abort_consecutive_errors',true),abort_load_seconds:valueOrNull(form,'abort_load_seconds',true),abort_timeouts:valueOrNull(form,'abort_timeouts',true),abort_on_429:form.elements.abort_on_429.checked,abort_on_503:form.elements.abort_on_503.checked
    };
  }
  function calculateConcurrency(form){
    const warning=document.getElementById('concurrency-warning'); if(!warning)return;
    let stay=0;if(form.elements.mode.value==='browser'){if(form.elements.stay_mode.value==='fixed')stay=Number(form.elements.stay_fixed_seconds.value||0);if(form.elements.stay_mode.value==='random')stay=Number(form.elements.stay_max_seconds.value||0);}
    const needed=Math.max(1,Math.ceil(Number(form.elements.frequency_per_minute.value||0)*stay/60)), limit=Number(form.elements.max_concurrency.value||0);
    warning.classList.toggle('hidden',needed<=limit);warning.innerHTML=`<strong>Kapazitätshinweis</strong><span>Mit den gewählten Einstellungen werden ungefähr ${needed} parallele Browser Sessions benötigt. Das aktuelle Limit beträgt ${limit}.</span>`;
  }
  function initTestForm(){
    const form=document.getElementById('test-form');conditionalForms();form.addEventListener('input',()=>calculateConcurrency(form));calculateConcurrency(form);
    form.addEventListener('submit',async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{const start=button.dataset.start==='true';const result=await api(`api/tests?start=${start}`,{method:'POST',body:JSON.stringify(testPayload(form))});result.warnings.forEach(w=>toast(w));toast(start?'Test erstellt und gestartet.':'Test gespeichert.');location.href=url(`tests/${result.test.id}`);}catch(e){toast(e.message,'error');button.disabled=false;}});
  }

  let detailPage=1, detailPages=1;
  async function loadDetail(){
    const root=document.getElementById('test-detail'), id=root.dataset.testId;
    try {
      const [test,stats,requests]=await Promise.all([api(`api/tests/${id}`),api(`api/tests/${id}/stats`),api(`api/tests/${id}/requests?page=${detailPage}&page_size=50`)]);
      const cards=[['Status',status(test.status)],['Beginn',date(test.started_at)],['Laufzeit',duration(test.runtime_seconds)],['Aufrufe',`${number(stats.total)}${test.max_requests?` / ${number(test.max_requests)}`:''}`],['Erfolgreich',number(stats.successful)],['Fehler',number(stats.failed)],['Erfolgsquote',`${stats.success_rate.toFixed(2)} %`],['Aktive Sessions',stats.active_sessions],['Aufrufe/min',test.frequency_per_minute],['Ø Ladezeit',ms(stats.average_ms)],['Median / P50',ms(stats.median_ms)],['P95',ms(stats.p95_ms)],['P99',ms(stats.p99_ms)],['Maximum',ms(stats.maximum_ms)]];
      document.getElementById('detail-cards').innerHTML=cards.map(c=>`<article class="metric-card"><span>${c[0]}</span><strong>${c[1]}</strong></article>`).join('');
      const stay=test.stay_mode==='none'?'Keine':test.stay_mode==='fixed'?`${test.stay_min_seconds} s fix`:`${test.stay_min_seconds}–${test.stay_max_seconds} s zufällig`;
      const termination=test.duration_mode==='requests'?`${number(test.max_requests)} Aufrufe`:test.duration_mode==='end_at'?`Ende ${date(test.end_at)}`:`${test.duration_days} Tage, ${test.duration_hours} Std., ${test.duration_minutes} Min.`;
      const proxyNames={direct:'Direkt',random:'Zufälliger Proxy',round_robin:'Round Robin',fixed:'Fester Proxy'};
      const meta=[['URL',test.url],['Start',date(test.started_at||test.start_at)],['Ende',date(test.finished_at||test.end_at)],['Testmodus',test.mode==='browser'?'Real Browser':'HTTP Test'],['Aufrufe/min',`${test.frequency_per_minute} · ${test.frequency_mode==='random'?'zufällig':'gleichmässig'}`],['Aufenthalt',stay],['Beendigung',termination],['Proxy-Modus',proxyNames[test.proxy_mode]||test.proxy_mode],['Max. parallel',test.max_concurrency]];
      document.getElementById('report-meta').innerHTML=meta.map(x=>`<div><dt>${x[0]}</dt><dd>${esc(x[1])}</dd></div>`).join('');
      const errorLabels={timeout:'Timeouts',browser:'Browserfehler',browser_crash:'Browser-Crashes',dns:'DNS-Fehler',proxy:'Proxy-Fehler',connection:'Verbindungsfehler',http_status:'HTTP-Fehler',validation:'Validierungsfehler'};
      const errorItems=Object.entries(stats.errors).map(([kind,count])=>[errorLabels[kind]||kind,count]);
      document.getElementById('error-analysis').innerHTML=(errorItems.length?errorItems:[['Keine Fehler',0]]).map(x=>`<div><dt>${esc(x[0])}</dt><dd>${number(x[1])}</dd></div>`).join('');
      const msg=document.getElementById('status-message');msg.classList.toggle('hidden',!test.status_message);msg.textContent=test.status_message||'';
      document.querySelectorAll('#test-controls [data-action]').forEach(b=>{const a=b.dataset.action;b.classList.toggle('hidden',!((a==='start'&&['ready','scheduled','interrupted'].includes(test.status))||(a==='pause'&&['running','waiting_capacity'].includes(test.status))||(a==='resume'&&test.status==='paused')||(a==='stop'&&['running','paused','waiting_capacity'].includes(test.status))));});
      renderLine('chart-load',stats.series.timeline.map(x=>x.average_ms),'#3c9f98');renderBars('chart-success',[stats.successful,stats.failed],['Erfolgreich','Fehler'],['#3c9f78','#c85a5a']);renderBars('chart-status',Object.values(stats.http_status_codes),Object.keys(stats.http_status_codes),'#557fa4');renderLine('chart-calls',stats.series.timeline.map(x=>x.requests),'#557fa4');
      document.getElementById('requests-table').innerHTML=requests.items.length?requests.items.map(r=>`<tr><td>${date(r.started_at)}</td><td>${r.http_status??'–'}</td><td>${ms(r.load_time_ms)}</td><td>${ms(r.ttfb_ms)}</td><td>${Number(r.stay_time_seconds||0).toFixed(1)} s</td><td>${esc(r.connection_label)}</td><td title="${esc(r.error_message||'')}">${esc(r.error_type||'–')}</td></tr>`).join(''):'<tr><td colspan="7" class="empty">Keine Einzelrequests gespeichert.</td></tr>';
      detailPages=requests.pages;document.getElementById('page-label').textContent=`Seite ${requests.page} von ${requests.pages}`;document.getElementById('page-prev').disabled=detailPage<=1;document.getElementById('page-next').disabled=detailPage>=detailPages;
    }catch(e){toast(e.message,'error');}
  }
  function canvasContext(id){const canvas=document.getElementById(id);const ratio=window.devicePixelRatio||1,width=canvas.clientWidth-28,height=Number(canvas.getAttribute('height'));canvas.width=width*ratio;canvas.height=height*ratio;const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);return {ctx,width,height};}
  function renderLine(id,values,color){const {ctx,width,height}=canvasContext(id);ctx.clearRect(0,0,width,height);ctx.strokeStyle=getComputedStyle(document.documentElement).getPropertyValue('--line');ctx.lineWidth=1;for(let y=30;y<height-20;y+=(height-50)/4){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(width,y);ctx.stroke();}if(!values.length){ctx.fillStyle=getComputedStyle(document.documentElement).getPropertyValue('--muted');ctx.fillText('Noch keine Messwerte',12,height/2);return;}const max=Math.max(...values,1),step=values.length>1?width/(values.length-1):width;ctx.strokeStyle=color;ctx.lineWidth=2;ctx.beginPath();values.forEach((v,i)=>{const x=i*step,y=height-22-(v/max)*(height-48);i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.stroke();}
  function renderBars(id,values,labels,colors){const {ctx,width,height}=canvasContext(id);ctx.clearRect(0,0,width,height);if(!values.length){ctx.fillStyle='#7b8790';ctx.fillText('Noch keine Messwerte',12,height/2);return;}const max=Math.max(...values,1),slot=width/values.length;values.forEach((v,i)=>{const h=(v/max)*(height-55);ctx.fillStyle=Array.isArray(colors)?colors[i]:colors;ctx.fillRect(i*slot+slot*.18,height-28-h,slot*.64,h);ctx.fillStyle=getComputedStyle(document.documentElement).getPropertyValue('--muted');ctx.textAlign='center';ctx.fillText(String(labels[i]),i*slot+slot/2,height-8);ctx.fillText(String(v),i*slot+slot/2,height-35-h);});}
  function initDetail(){const root=document.getElementById('test-detail'),id=root.dataset.testId;document.getElementById('test-controls').addEventListener('click',async e=>{const b=e.target.closest('[data-action]');if(!b)return;const action=b.dataset.action;if(action==='stop'&&!confirm('Test wirklich beenden? Bereits aktive Sessions werden sauber abgeschlossen.'))return;b.disabled=true;try{await api(`api/tests/${id}/${action}`,{method:'POST'});toast(action==='pause'?'Test pausiert.':action==='resume'?'Test fortgesetzt.':action==='stop'?'Test beendet.':'Test gestartet.');await loadDetail();}catch(err){toast(err.message,'error');}finally{b.disabled=false;}});document.getElementById('page-prev').onclick=()=>{if(detailPage>1){detailPage--;loadDetail();}};document.getElementById('page-next').onclick=()=>{if(detailPage<detailPages){detailPage++;loadDetail();}};loadDetail();setInterval(loadDetail,5000);}

  async function loadReports(event){event?.preventDefault();const form=document.getElementById('report-filters'),params=new URLSearchParams();new FormData(form).forEach((v,k)=>{if(v)params.set(k,v);});try{const rows=await api(`api/reports?${params}`);document.getElementById('reports-table').innerHTML=rows.length?rows.map(t=>`<tr><td><a class="name" href="${url(`tests/${t.id}`)}">${esc(t.name)}</a></td><td><span class="table-url">${esc(t.url)}</span></td><td>${date(t.started_at)}</td><td>${date(t.finished_at)}</td><td>${status(t.status)}</td><td>${number(t.total_requests)}</td><td>${t.success_rate.toFixed(2)} %</td><td>${ms(t.average_ms)}</td><td class="actions"><a href="${url(`api/tests/${t.id}/export/csv`)}">CSV</a><a href="${url(`api/tests/${t.id}/export/json`)}">JSON</a></td></tr>`).join(''):'<tr><td colspan="9" class="empty">Keine Reports gefunden.</td></tr>';}catch(e){toast(e.message,'error');}}

  let proxyRows=[];
  async function loadProxies(){try{proxyRows=await api('api/proxies');document.getElementById('proxy-table').innerHTML=proxyRows.length?proxyRows.map(p=>`<tr><td><strong>${esc(p.name)}</strong>${p.password_configured?'<small class="table-url">Passwort: ••••••••</small>':''}</td><td>${esc(p.host)}:${p.port}</td><td>${p.protocol}</td><td>${p.enabled?status('running'):status('paused')}</td><td class="actions"><button data-action="test" data-id="${p.id}">Verbindung testen</button><button data-action="edit" data-id="${p.id}">Bearbeiten</button><button data-action="delete" data-id="${p.id}">Löschen</button></td></tr>`).join(''):'<tr><td colspan="5" class="empty">Noch keine Proxys konfiguriert.</td></tr>';}catch(e){toast(e.message,'error');}}
  function resetProxyForm(){const f=document.getElementById('proxy-form');f.reset();f.elements.id.value='';f.elements.enabled.checked=true;document.getElementById('proxy-form-title').textContent='Proxy hinzufügen';document.getElementById('proxy-cancel').classList.add('hidden');}
  function initProxies(){const form=document.getElementById('proxy-form');form.addEventListener('submit',async e=>{e.preventDefault();const data=Object.fromEntries(new FormData(form));data.port=Number(data.port);data.enabled=form.elements.enabled.checked;data.clear_password=false;const id=form.elements.id.value;delete data.id;try{await api(id?`api/proxies/${id}`:'api/proxies',{method:id?'PUT':'POST',body:JSON.stringify(data)});toast('Proxy gespeichert.');resetProxyForm();loadProxies();}catch(err){toast(err.message,'error');}});document.getElementById('proxy-cancel').onclick=resetProxyForm;document.getElementById('proxy-table').addEventListener('click',async e=>{const b=e.target.closest('[data-action]');if(!b)return;const p=proxyRows.find(x=>String(x.id)===b.dataset.id);if(b.dataset.action==='edit'){['id','name','protocol','host','port','username'].forEach(k=>form.elements[k].value=p[k]??'');form.elements.password.value='';form.elements.enabled.checked=p.enabled;document.getElementById('proxy-form-title').textContent='Proxy bearbeiten';document.getElementById('proxy-cancel').classList.remove('hidden');form.scrollIntoView({behavior:'smooth'});return;}try{if(b.dataset.action==='delete'){if(!confirm(`Proxy „${p.name}“ wirklich löschen?`))return;await api(`api/proxies/${p.id}`,{method:'DELETE'});toast('Proxy gelöscht.');loadProxies();}if(b.dataset.action==='test'){b.disabled=true;const r=await api(`api/proxies/${p.id}/test`,{method:'POST'});toast(r.success?`Verbindung erfolgreich · ${r.latency_ms} ms${r.visible_source_ip?` · Quell-IP ${r.visible_source_ip}`:''}`:r.message,'error'.repeat(!r.success));b.disabled=false;}}catch(err){toast(err.message,'error');b.disabled=false;}});loadProxies();}

  function initSettings(){const form=document.getElementById('settings-form');form.addEventListener('submit',async e=>{e.preventDefault();const d=Object.fromEntries(new FormData(form));['max_requests_per_minute','max_parallel_browsers','max_stay_seconds','max_test_duration_hours','max_memory_percent','navigation_timeout_seconds','retention_days'].forEach(k=>d[k]=Number(d[k]));d.allow_private_targets=form.elements.allow_private_targets.checked;try{await api('api/settings',{method:'PUT',body:JSON.stringify(d)});toast('Einstellungen gespeichert.');}catch(err){toast(err.message,'error');}});}

  async function loadSystem(){
    try {
      const s=await api('api/system');
      const cards=[
        ['Anwendung', [['Version',s.version],['Architektur',s.architecture],['Python',s.python_version],['Chromium',s.chromium_version||'Nicht verfügbar']]],
        ['Datenbank', [['Status',s.database.ok?'OK':'Fehler'],['Schema',s.database.schema_version],['Grösse',bytes(s.database.size_bytes)],['Tests',number(s.test_count)],['Requests',number(s.request_count)]]],
        ['Ressourcen', [['RAM Prozess',bytes(s.ram.process_rss)],['RAM System',`${s.ram.system_percent} %`],['Speicher frei',bytes(s.disk.free)],['Browser aktiv',s.active_browser_sessions],['Browser wartend',s.waiting_browser_sessions],['Browser-Zombies',s.browser_zombie_processes],['Letzter Browserfehler',s.browser_last_error||'Keiner']]],
        ['Scheduler', [['Status',s.scheduler.running?'Läuft':'Gestoppt'],['Jobs',s.scheduler.jobs],['Letzter Tick',date(s.scheduler.last_tick)],['Fehler',s.scheduler.last_error||'Keine']]]
      ];
      document.getElementById('system-grid').innerHTML=cards.map(c=>`<article class="panel system-card"><h2>${c[0]}</h2><dl class="system-list">${c[1].map(x=>`<div><dt>${x[0]}</dt><dd>${esc(x[1])}</dd></div>`).join('')}</dl></article>`).join('');
    } catch(e) { toast(e.message,'error'); }
  }
  function initSystem(){document.getElementById('db-check').onclick=async()=>{try{const r=await api('api/system/database-check',{method:'POST'});toast(`Datenbankprüfung: ${r.status}`);}catch(e){toast(e.message,'error');}};document.getElementById('cleanup').onclick=async()=>{if(!confirm('Einzelrequests ausserhalb der Aufbewahrungsfrist jetzt bereinigen?'))return;try{const r=await api('api/system/cleanup',{method:'POST'});toast(`${r.removed_requests} alte Einzelrequests entfernt.`);loadSystem();}catch(e){toast(e.message,'error');}};loadSystem();setInterval(loadSystem,10000);}
  function initOnboarding(){document.getElementById('onboarding-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.target,d=Object.fromEntries(new FormData(f));['max_requests_per_minute','max_parallel_browsers','max_stay_seconds','max_test_duration_hours'].forEach(k=>d[k]=Number(d[k]));d.authorization_confirmed=f.elements.authorization_confirmed.checked;d.onboarding_complete=true;try{await api('api/settings',{method:'PUT',body:JSON.stringify(d)});toast('Einrichtung abgeschlossen.');location.href=url('');}catch(err){toast(err.message,'error');}});}

  initNav();
  if(page==='dashboard'){loadDashboard();setInterval(loadDashboard,5000);}
  if(page==='tests'){loadTests();document.getElementById('test-search').addEventListener('input',renderTests);document.getElementById('test-status-filter').addEventListener('change',renderTests);document.getElementById('tests-table').addEventListener('click',testAction);}
  if(page==='new-test')initTestForm();
  if(page==='test-detail')initDetail();
  if(page==='reports'){document.getElementById('report-filters').addEventListener('submit',loadReports);loadReports();}
  if(page==='proxies')initProxies();
  if(page==='settings')initSettings();
  if(page==='system')initSystem();
  if(page==='onboarding')initOnboarding();
})();
