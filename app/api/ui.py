"""Render the browser forms for the authenticated catalog and its public registration page."""

import json
from html import escape

_STYLE = """<style>
body{font:16px/1.5 system-ui;margin:0;background:#f5f7f6;color:#17313b}header{background:#174354;color:white;padding:18px 5%}
main{max-width:1100px;margin:28px auto;padding:0 20px}section{background:white;padding:20px;margin:16px 0;border-radius:10px}
button{background:#176078;color:white;border:0;border-radius:5px;padding:9px 15px;cursor:pointer;margin:3px}
input,select{padding:8px;margin:3px;max-width:100%}table{width:100%;border-collapse:collapse}td,th{border-bottom:1px solid #ddd;text-align:left;padding:7px}
.muted{color:#50616a}.error{color:#a43126}.ok{color:#17613b}nav a{color:white;margin-left:16px}label{display:inline-block}
.segment{white-space:pre-wrap;overflow-wrap:anywhere;border-left:3px solid #b7d2d8;padding:10px;margin:10px 0}mark{background:#ffe29b;padding:1px 2px}
.preview-dialog{width:min(96vw,1150px);height:min(94vh,980px);max-width:none;max-height:none;border:0;border-radius:10px;padding:0}
.preview-dialog::backdrop{background:#10242bcc}.preview-shell{height:100%;display:flex;flex-direction:column}
.preview-toolbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:10px 16px;background:#174354;color:white}
.preview-toolbar button{background:#e8f3f3;color:#174354}.preview-body{overflow:auto;flex:1;padding:18px;background:#dce3e5}
.original-page{position:relative;width:fit-content;max-width:100%;margin:auto;box-shadow:0 5px 22px #3446;background:white}
.original-page img{display:block;max-width:100%;height:auto}.pii-box{position:absolute;box-sizing:border-box;border:2px solid #07844b;background:#3ac36a55;cursor:help}
.pii-box.review{border-color:#b57400;background:#ffcf4f77}.preview-text{max-width:850px;margin:auto;padding:45px;background:white;box-shadow:0 5px 22px #3446;white-space:pre-wrap;overflow-wrap:anywhere}
.preview-text p{margin:0 0 1em}.preview-findings{padding:8px 16px;max-height:100px;overflow:auto;background:white;font-size:.9rem}
.preview-text table{margin:1em 0}.preview-text td{border:1px solid #8d9aa0;vertical-align:top;min-width:90px}.preview-text h3{margin:1.2em 0 .5em}
</style>"""


def registration_page() -> str:
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Регистрация</title>{_STYLE}</head><body>
<header><strong>Экология данных</strong></header><main><section><h1>Регистрация оператора</h1>
<label>Логин <input id="login" autocomplete="username"></label>
<label>Пароль <input id="password" type="password" autocomplete="new-password" minlength="12"></label>
<button id="register">Зарегистрироваться</button><p id="message" role="status"></p>
<p class="muted">Новая учетная запись получает роль оператора. Другую роль назначает администратор.</p></section></main>
<script>document.querySelector('#register').onclick=async()=>{{
const body={{login:document.querySelector('#login').value,password:document.querySelector('#password').value}};
const response=await fetch('/api/register',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});
const data=await response.json();document.querySelector('#password').value='';
document.querySelector('#message').textContent=response.ok?'Учетная запись создана. Откройте главную страницу и войдите.':
(typeof data.detail==='string'?data.detail:'Проверьте логин и пароль (минимум 12 символов).');
}};</script></body></html>"""


_OVERVIEW_STYLE = """<style>
nav{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}nav a{margin:0;padding:4px 7px;border-radius:5px;background:#2b5b69;text-decoration:none;font-size:.88rem}
nav a:hover,nav a:focus{background:#40798a}section{scroll-margin-top:16px}h1,h2{line-height:1.2}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}.stat{border:1px solid #d7e3e4;border-radius:8px;padding:11px;background:#f8fbfa}
.stat strong{display:block;font-size:1.35rem;color:#124b61}.stat small{color:#50616a}.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px}
.chart{border:1px solid #e0e8e8;border-radius:8px;padding:12px;min-width:0}.bar-row{display:grid;grid-template-columns:95px 1fr 75px;gap:8px;align-items:center;margin:6px 0;font-size:.85rem}
.bar-track{height:13px;background:#e8eeee;border-radius:6px;overflow:hidden}.bar-fill{height:100%;background:#26828c}.chart svg{width:100%;height:150px}
.table-wrap{overflow-x:auto}.compact td,.compact th{font-size:.86rem;white-space:nowrap}.warn{color:#955714}.empty{padding:12px;color:#50616a}
</style>"""


def _overview_sections(login: str, role: str) -> tuple[str, str, str, str]:
    admin = role == "admin"
    editor = role in {"admin", "operator"}
    audit = role in {"admin", "auditor"}
    links = [("Авторизация", "authorization"), ("Дашборд", "ecology-dashboard")]
    if editor:
        links.append(("Источники", "sources"))
    links.append(("Каталог", "catalog"))
    links.append(("Обнаружение ПДн", "pii-analyze" if editor else "pii-metrics"))
    links.extend([("Дубликаты и неактуальные", "duplicates"),
                  ("Рекомендации", "recommendations")])
    if admin:
        links.append(("Резервные копии", "backups"))
    if audit:
        links.append(("Журнал", "journal"))
    if admin:
        links.append(("Пользователи", "users-section"))
    links.append(("Об авторе", "about"))
    navigation = "".join(f'<a href="#{anchor}">{escape(title)}</a>' for title, anchor in links)
    top = f"""<section id="authorization"><h2>Авторизация</h2><p>Вы вошли как <strong>{escape(login)}</strong>; роль: <strong>{escape(role)}</strong>.
Доступ к API и разделам проверяется сервером через HTTP Basic. <a href="/register">Регистрация оператора</a>.</p></section>
<section id="ecology-dashboard"><h1>Дашборд экологичности</h1><button id="overview-refresh">Обновить показатели</button>
<p id="overview-status" role="status" class="muted"></p><div id="overview-stats" class="stats"></div>
<div class="charts"><div class="chart"><h3>Объём по форматам</h3><div id="format-chart"></div></div>
<div class="chart"><h3>Объём по категориям</h3><div id="category-chart"></div></div>
<div class="chart"><h3>Динамика объёма</h3><div id="volume-chart"></div></div>
<div class="chart"><h3>Динамика количества объектов</h3><div id="count-chart"></div></div></div>
<h3>Обнаруженные риски</h3><div class="table-wrap"><table class="compact"><thead><tr><th>Показатель</th><th>Значение</th><th>Основание</th></tr></thead>
<tbody id="risk-rows"></tbody></table></div><p class="muted">Индекс и экономический эффект зависят от охвата данных и настроек. ПДн-детектор даёт кандидатов для проверки.</p></section>"""
    middle = """<section id="pii-metrics"><h2>Обнаружение ПДн</h2><p class="muted">Сводка только по завершённым анализам; найденные сущности требуют проверки.</p>
<div class="table-wrap"><table class="compact"><thead><tr><th>Класс</th><th>Обнаружено</th></tr></thead><tbody id="pii-rows"></tbody></table></div></section>
<section id="duplicates"><h2>Дубликаты и неактуальные объекты</h2><p id="duplicate-summary" class="muted"></p>
<h3>Группы полных дубликатов</h3><div class="table-wrap"><table class="compact"><thead><tr><th>SHA-256</th><th>Сохранить ID</th><th>Копии ID</th></tr></thead><tbody id="duplicate-rows"></tbody></table></div>
<h3>Вероятные версии</h3><div class="table-wrap"><table class="compact"><thead><tr><th>Каталог</th><th>Основа имени</th><th>Объекты ID</th><th>Критерий</th></tr></thead><tbody id="version-rows"></tbody></table></div>
<h3>Кандидаты по неактуальности</h3><div class="table-wrap"><table class="compact"><thead><tr><th>ID</th><th>Причины</th><th>Байты</th></tr></thead><tbody id="stale-rows"></tbody></table></div></section>
<section id="recommendations"><h2>Рекомендации и жизненный цикл</h2><p class="muted">Предложения не выполняются автоматически. Удаление возможно только после архивирования и точного подтверждения администратором.</p>
<div class="table-wrap"><table class="compact"><thead><tr><th>ID</th><th>Рекомендация</th><th>Оценка, байт</th><th>Статус</th><th>Действие</th></tr></thead><tbody id="recommendation-rows"></tbody></table></div>
<h3>Проверка риска ПДн</h3><div class="table-wrap"><table class="compact"><thead><tr><th>ID</th><th>Рекомендация</th></tr></thead><tbody id="pii-risk-rows"></tbody></table></div>
<h3>Архивные объекты</h3><div class="table-wrap"><table class="compact"><thead><tr><th>ID</th><th>Имя</th><th>Статус</th><th>Действие</th></tr></thead><tbody id="lifecycle-rows"></tbody></table></div>
<h3>Выполненные операции</h3><div class="table-wrap"><table class="compact"><thead><tr><th>Время</th><th>ID</th><th>Операция</th></tr></thead><tbody id="operation-rows"></tbody></table></div></section>"""
    if admin:
        middle += """<section id="backups"><h2>Резервные копии</h2><p class="muted">Полная копия содержит SQLite и управляемые файлы. Инкрементальная ссылается на выбранного предка. Восстановление создаёт отдельный каталог.</p>
<button id="backup-full">Создать полную копию</button><select id="backup-parent" aria-label="Родительская копия"></select>
<button id="backup-incremental">Создать инкремент</button><button id="backup-refresh">Обновить список</button>
<div class="table-wrap"><table class="compact"><thead><tr><th>ID</th><th>Тип</th><th>Родитель</th><th>Файлов</th><th>Статус манифеста</th><th>Действие</th></tr></thead><tbody id="backup-rows"></tbody></table></div></section>"""
    bottom = """<section id="about"><h2>Об авторе</h2><p><strong>Путинцева А. А.</strong>, студентка группы ИП23-3.</p>
<p>Курсовая работа «Система обнаружения персональных данных в неструктурированных источниках организации», Финансовый университет при Правительстве Российской Федерации, Москва, 2026.</p>
<p class="muted">Сведения взяты с титульного листа «Курсовая.docx».</p></section>"""
    return navigation, top, middle, bottom


_OVERVIEW_SCRIPT = """<script>
const sectionRole=document.querySelector('main').dataset.role;
const fmt=n=>n===null||n===undefined?'нет данных':new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(n);
const bytes=n=>n===null||n===undefined?'нет данных':fmt(n)+' байт';
const cell=value=>{const e=document.createElement('td');e.textContent=value===null||value===undefined?'нет данных':String(value);return e};
function fillRows(id,rows){const body=document.getElementById(id);body.replaceChildren();
  if(!rows.length){const tr=document.createElement('tr'),td=cell('Нет данных');td.colSpan=body.closest('table').querySelectorAll('th').length;td.className='empty';tr.append(td);body.append(tr);return}
  for(const values of rows){const tr=document.createElement('tr');for(const value of values)tr.append(cell(value));body.append(tr)}
}
function stat(label,value,note=''){const e=document.createElement('div');e.className='stat';
  const title=document.createElement('small'),number=document.createElement('strong'),detail=document.createElement('small');
  title.textContent=label;number.textContent=value;detail.textContent=note;e.append(title,number,detail);return e}
function bars(id,data){const root=document.getElementById(id);root.replaceChildren();
  const entries=Object.entries(data||{}).sort((a,b)=>b[1]-a[1]);if(!entries.length){root.textContent='Нет измеренных объёмов';return}
  const shown=entries.slice(0,8);if(entries.length>8)shown.push(['Прочие',entries.slice(8).reduce((n,x)=>n+x[1],0)]);
  const maximum=Math.max(...shown.map(x=>x[1]),1);for(const [label,value] of shown){
    const row=document.createElement('div');row.className='bar-row';const name=document.createElement('span');name.textContent=label;
    const track=document.createElement('div');track.className='bar-track';const fill=document.createElement('div');fill.className='bar-fill';fill.style.width=(100*value/maximum)+'%';track.append(fill);
    const amount=document.createElement('span');amount.textContent=fmt(value);row.append(name,track,amount);root.append(row)}
}
function line(id,snapshots,key,label){const root=document.getElementById(id);root.replaceChildren();
  if(!snapshots.length){root.textContent='Снимков инвентаризации пока нет';return}
  const max=Math.max(1,...snapshots.map(x=>x[key]||0)),svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 300 150');svg.setAttribute('role','img');svg.setAttribute('aria-label',label);
  const points=snapshots.map((x,i)=>[24+(snapshots.length===1?126:i*252/(snapshots.length-1)),118-95*(x[key]||0)/max]);
  const axis=document.createElementNS(svg.namespaceURI,'path');axis.setAttribute('d','M24 18 V118 H286');axis.setAttribute('stroke','#9bb4b8');axis.setAttribute('fill','none');svg.append(axis);
  const poly=document.createElementNS(svg.namespaceURI,'polyline');poly.setAttribute('points',points.map(p=>p.join(',')).join(' '));poly.setAttribute('stroke','#26828c');poly.setAttribute('stroke-width','3');poly.setAttribute('fill','none');svg.append(poly);
  for(let i=0;i<points.length;i++){const dot=document.createElementNS(svg.namespaceURI,'circle');dot.setAttribute('cx',points[i][0]);dot.setAttribute('cy',points[i][1]);dot.setAttribute('r','4');dot.setAttribute('fill','#176078');
    const title=document.createElementNS(svg.namespaceURI,'title');title.textContent=snapshots[i].scanned_at+' · '+fmt(snapshots[i][key]);dot.append(title);svg.append(dot)}
  root.append(svg);const caption=document.createElement('small');caption.textContent='Снимков: '+snapshots.length+' · '+snapshots[0].scanned_at.slice(0,10)+' — '+snapshots.at(-1).scanned_at.slice(0,10);root.append(caption)
}
async function lifecycleAction(path,options){try{await api(path,options);notice('Операция записана в журнал');await loadOverview();refresh()}catch(error){notice(error.message,true)}}
function renderRecommendations(report,objects){const byId=new Map(objects.map(x=>[x.id,x]));
  const rows=document.getElementById('recommendation-rows');rows.replaceChildren();
  for(const item of report.recommendations){const tr=document.createElement('tr');for(const v of [item.object_id,item.action,bytes(item.estimated_bytes),item.status])tr.append(cell(v));
    const action=document.createElement('td'),obj=byId.get(item.object_id);
    if(sectionRole==='admin'&&obj?.availability==='present'){const button=document.createElement('button');button.textContent='В архив';button.onclick=()=>lifecycleAction('/api/objects/'+obj.id+'/archive',{method:'POST'});action.append(button)}
    tr.append(action);rows.append(tr)}if(!report.recommendations.length)fillRows('recommendation-rows',[]);
  fillRows('pii-risk-rows',report.pii_risk_recommendations.map(x=>[x.object_id,'Проверить доступ, цель обработки и срок хранения']));
  const lifecycle=document.getElementById('lifecycle-rows');lifecycle.replaceChildren();const managed=objects.filter(x=>['archived','deletion_pending'].includes(x.availability));
  for(const obj of managed){const tr=document.createElement('tr');for(const v of [obj.id,obj.name,obj.availability])tr.append(cell(v));const action=document.createElement('td');
    if(sectionRole==='admin'){const button=document.createElement('button');button.textContent='Подтвердить удаление';button.onclick=()=>{
      const expected='DELETE '+obj.id+' '+obj.sha256;const confirmation=prompt('Для удаления архивного файла введите точно:\\n'+expected);
      if(confirmation===null)return;lifecycleAction('/api/objects/'+obj.id+'/delete-file',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({expected_sha256:obj.sha256,confirmation})})};action.append(button)}tr.append(action);lifecycle.append(tr)}
  if(!managed.length)fillRows('lifecycle-rows',[]);
  fillRows('operation-rows',report.performed_operations.map(x=>[x.occurred_at,x.object_id,x.action]))
}
async function loadOverview(){const status=document.getElementById('overview-status');status.textContent='Загрузка показателей…';
  try{const [report,objects]=await Promise.all([api('/api/ecology'),api('/api/objects')]);
    const stats=document.getElementById('overview-stats');stats.replaceChildren(
      stat('Зарегистрировано объектов',fmt(objects.length),'Все доступные записи каталога'),
      stat('Активный объём',bytes(report.volume.known_bytes),'Неизвестных размеров: '+report.volume.unknown_size_count),
      stat('Архивный объём',bytes(report.volume.archive_known_bytes)),
      stat('Индекс экологичности',report.index.value===null?'нет данных':fmt(report.index.value)+' / 100',report.index.status+' · охват весов '+fmt(report.index.available_weight)),
      stat('Дубликаты',fmt(report.redundancy.duplicate_count),'Оценка лишних байтов: '+bytes(report.redundancy.duplicate_bytes)),
      stat('Тёмные объекты',fmt(report.currency.dark_count),'Неизвестных дат обращения: '+report.currency.access_unknown_count),
      stat('Стоимость в месяц',report.economics.baseline_cost_per_month===null?'нет данных':fmt(report.economics.baseline_cost_per_month)+' '+report.economics.currency,'Оценка по заданным коэффициентам'),
      stat('Энергия в месяц',report.economics.baseline_kwh_per_month===null?'нет данных':fmt(report.economics.baseline_kwh_per_month)+' кВт·ч','Оценка по заданным коэффициентам'),
      stat('Возможная экономия',report.economics.estimated_cost_saving_per_month===null?'нет данных':fmt(report.economics.estimated_cost_saving_per_month)+' '+report.economics.currency+'/мес','При выполнении рекомендаций'),
      stat('Возможное снижение энергии',report.economics.estimated_kwh_saving_per_month===null?'нет данных':fmt(report.economics.estimated_kwh_saving_per_month)+' кВт·ч/мес','При выполнении рекомендаций'));
    bars('format-chart',report.volume.by_format_bytes);bars('category-chart',report.volume.by_category_bytes);
    const history=report.volume.registered_snapshots||[];const scope=history.some(x=>x.scope==='.')?'.':history.at(-1)?.scope;
    const selected=history.filter(x=>x.scope===scope).slice(-12);line('volume-chart',selected,'total_bytes','Динамика зарегистрированного объёма, область '+(scope||''));
    line('count-chart',selected,'object_count','Динамика числа объектов, область '+(scope||''));
    fillRows('risk-rows',[
      ['Дубликаты',fmt(report.redundancy.duplicate_count),bytes(report.redundancy.duplicate_bytes)],
      ['Тёмные документы',fmt(report.currency.dark_count),'Порог: '+report.assumptions.dark_days+' дней'],
      ['Истёкший внутренний срок',fmt(report.currency.retention_exceeded_count),'Применим срок к '+report.currency.retention_evaluable_count+' объектам'],
      ['Неполные метаданные',report.quality_and_pii.missing_metadata_share===null?'нет данных':fmt(100*report.quality_and_pii.missing_metadata_share)+' %','Владелец, категория или размер'],
      ['Обнаружены кандидаты ПДн',fmt(report.quality_and_pii.pii_detected_document_count),'Проверено документов: '+report.quality_and_pii.pii_assessed_count]]);
    fillRows('pii-rows',Object.entries(report.quality_and_pii.detected_entities_by_class).map(([key,value])=>[key,value]));
    document.getElementById('duplicate-summary').textContent='Полных копий: '+report.redundancy.duplicate_count+' · тёмных: '+report.currency.dark_count+' · срок истёк: '+report.currency.retention_exceeded_count;
    fillRows('duplicate-rows',report.redundancy.groups.map(x=>[x.sha256,x.keep_id,x.duplicate_ids.join(', ')]));
    fillRows('version-rows',report.redundancy.probable_versions.map(x=>[x.directory,x.base_name,x.object_ids.join(', '),x.criterion]));
    fillRows('stale-rows',report.candidates.filter(x=>x.reasons.some(r=>r==='dark'||r==='retention_exceeded')).map(x=>[x.object_id,x.reasons.join(', '),bytes(x.estimated_bytes)]));
    renderRecommendations(report,objects);
    status.textContent='Показатели на '+report.as_of+' · объём и экономический эффект оценочные. Историческая динамика показана для одной области сканирования: '+(scope||'нет снимков')+'.';
  }catch(error){status.textContent='Не удалось загрузить показатели: '+error.message}
}
async function loadBackups(){const body=document.getElementById('backup-rows');if(!body)return;
  try{const backups=await api('/api/backups');const select=document.getElementById('backup-parent');select.replaceChildren();body.replaceChildren();
    for(const item of backups){const option=document.createElement('option');option.value=item.id;option.textContent=item.kind+' · '+item.id;select.append(option);
      const tr=document.createElement('tr');for(const value of [item.id,item.kind,item.parent_id,item.file_count,item.status])tr.append(cell(value));
      const action=document.createElement('td');if(item.status==='manifest_valid'){const button=document.createElement('button');button.textContent='Восстановить отдельно';
        button.onclick=async()=>{try{const result=await api('/api/backups/'+item.id+'/restore',{method:'POST'});notice('Восстановлено в '+result.destination)}catch(error){notice(error.message,true)}};action.append(button)}tr.append(action);body.append(tr)}
    if(!backups.length)fillRows('backup-rows',[])
  }catch(error){notice(error.message,true)}
}
document.getElementById('overview-refresh').onclick=loadOverview;
if(sectionRole==='admin'){
  document.getElementById('backup-full').onclick=async()=>{try{await api('/api/backups',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'full'})});notice('Полная копия создана');loadBackups()}catch(error){notice(error.message,true)}};
  document.getElementById('backup-incremental').onclick=async()=>{try{const parent_id=document.getElementById('backup-parent').value;if(!parent_id)throw new Error('Сначала создайте полную копию');
    await api('/api/backups',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'incremental',parent_id})});notice('Инкрементальная копия создана');loadBackups()}catch(error){notice(error.message,true)}};
  document.getElementById('backup-refresh').onclick=loadBackups;loadBackups()
}
loadOverview();
</script>"""


def dashboard(login: str, role: str) -> str:
    navigation, top, middle, bottom = _overview_sections(login, role)
    editor = role in {"admin", "operator"}
    editor_sections = """<section id="sources"><h2>Источники</h2><p class="muted">Путь задается относительно настроенного корня.</p>
<input id="source-path" value="." aria-label="Путь каталога"><button id="source-add">Зарегистрировать каталог</button>
<select id="source-list" aria-label="Зарегистрированный каталог"></select><button id="source-scan">Сканировать</button></section>
<section><h2>Создать запись каталога</h2><input id="new-name" placeholder="Имя" aria-label="Имя">
<input id="new-type" placeholder="Тип, например docx" aria-label="Тип"><input id="new-category" placeholder="Категория" aria-label="Категория">
<input id="new-size" type="number" min="0" placeholder="Размер, если известен" aria-label="Размер"><button id="object-add">Создать запись</button>
<p class="muted">Запись можно удалить из каталога; исходный файл при этом остается на месте.</p></section>
<section><details id="edit-panel"><summary>Редактировать выбранную запись</summary><p id="edit-id"></p>
<input id="edit-name" placeholder="Имя" aria-label="Имя записи"><input id="edit-type" placeholder="Тип" aria-label="Тип записи">
<input id="edit-category" placeholder="Категория" aria-label="Категория записи">
<input id="edit-size" type="number" min="0" placeholder="Размер (пусто = неизвестно)" aria-label="Размер записи">
<input id="edit-owner" placeholder="Владелец (если известен)" aria-label="Владелец записи">
<input id="edit-created" placeholder="Создан: ISO 8601 или пусто" aria-label="Дата создания">
<input id="edit-modified" placeholder="Изменен: ISO 8601 или пусто" aria-label="Дата изменения">
<input id="edit-accessed" placeholder="Доступ: ISO 8601 или пусто" aria-label="Дата доступа">
<input id="edit-sha" placeholder="SHA-256 или пусто" aria-label="Контрольная сумма">
<select id="edit-status" aria-label="Статус обработки"><option value="not_processed">Не обработан</option>
<option value="completed">Завершен</option><option value="partial">Частично</option><option value="failed">Ошибка</option></select>
<button id="edit-save">Сохранить изменения</button></details></section>
<section id="pii-analyze"><h2>Загрузить документ</h2><input id="file" type="file" accept=".docx,.pdf,.txt,.png,.jpg,.jpeg,.tif,.tiff">
<input id="upload-category" placeholder="Категория для записи (обязательно)" aria-label="Категория загрузки" maxlength="100">
<button id="analyze">Проверить без сохранения записи</button><button id="upload">Добавить запись и проверить</button>
<button id="open-preview" hidden>Открыть документ с выделениями</button>
<div id="result" role="status"></div></section>""" if editor else ""
    admin_section = """<section id="users-section"><h2>Пользователи и роли</h2><table><thead><tr><th>Логин</th><th>Роль</th><th>Действие</th></tr></thead>
<tbody id="users"></tbody></table></section>""" if role == "admin" else ""
    audit_section = """<section id="journal"><h2>Журнал действий</h2><button id="audit-refresh">Обновить журнал</button>
<div style="overflow:auto"><table><thead><tr><th>Время</th><th>Пользователь</th><th>Действие</th><th>Объект/детали</th></tr></thead>
<tbody id="audit"></tbody></table></div></section>""" if role in {"admin", "auditor"} else ""
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Экология данных</title>{_STYLE}{_OVERVIEW_STYLE}</head><body>
<header><strong>Экология данных</strong> · {escape(login)} ({escape(role)}) <nav>{navigation}</nav></header><main data-role="{escape(role)}">
<p id="message" role="status"></p>{top}{editor_sections}
<dialog id="document-preview" class="preview-dialog"><div class="preview-shell">
<div class="preview-toolbar"><strong id="preview-title">Документ</strong><button id="preview-prev">←</button>
<span id="preview-counter"></span><button id="preview-next">→</button><button id="preview-close">Закрыть</button></div>
<div id="preview-body" class="preview-body"></div><div id="preview-findings" class="preview-findings"></div>
</div></dialog>
<section id="catalog"><h1>Каталог объектов</h1><label>Поиск <input id="q" placeholder="Имя"></label>
<label>Тип <input id="type" placeholder="docx"></label><label>Мин. байт <input id="min-size" type="number" min="0"></label>
<label>Категория <input id="category" placeholder="HR"></label>
<label>Макс. байт <input id="max-size" type="number" min="0"></label>
<label>Доступ до <input id="accessed" type="date"></label><label>Сортировка <select id="sort">
<option value="name">Имя</option><option value="size">Размер</option><option value="modified">Дата изменения</option>
<option value="accessed">Последний доступ</option><option value="category">Категория</option></select></label>
<label><input id="desc" type="checkbox"> По убыванию</label><button id="search">Найти</button><p id="totals"></p>
<div style="overflow:auto"><table><thead><tr><th>Имя</th><th>Тип</th><th>Категория</th><th>Байт</th><th>Владелец</th>
<th>Последний доступ</th><th>Статус</th><th>SHA-256</th><th>Действия</th></tr></thead><tbody id="rows"></tbody></table></div></section>
{middle}{admin_section}{audit_section}{bottom}</main><script>
const role={json.dumps(role)};
const canEdit=role==='admin'||role==='operator';let selectedObjectId=null;
const node=(tag,value)=>{{const e=document.createElement(tag);e.textContent=value??'недоступно';return e}};
function notice(message,error=false){{const e=document.querySelector('#message');e.textContent=message;e.className=error?'error':'ok'}}
async function api(path,options){{const response=await fetch(path,options);const data=await response.json();
if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Некорректные данные или недоступный сервис');return data}}
async function refresh(){{try{{const p=new URLSearchParams();
for(const [id,key] of [['q','q'],['type','file_type'],['category','category'],['min-size','min_size'],['max-size','max_size']]){{const v=document.getElementById(id).value;if(v)p.set(key,v)}}
const a=document.getElementById('accessed').value;if(a)p.set('accessed_before',a+'T00:00:00Z');
p.set('sort_by',document.getElementById('sort').value);p.set('descending',document.getElementById('desc').checked);
const objects=await api('/api/objects?'+p);document.querySelector('#totals').textContent=`${{objects.length}} записей`;
const body=document.querySelector('#rows');body.replaceChildren();for(const x of objects){{const tr=document.createElement('tr');
for(const k of ['name','file_type','category','size_bytes','owner','accessed_at','processing_status','sha256'])tr.appendChild(node('td',x[k]));
const actions=document.createElement('td');if(canEdit){{const edit=node('button','Изменить');edit.onclick=()=>{{
selectedObjectId=x.id;document.getElementById('edit-id').textContent='Запись '+x.id;
for(const [id,key] of [['edit-name','name'],['edit-type','file_type'],['edit-category','category'],['edit-size','size_bytes'],
['edit-owner','owner'],['edit-created','created_at'],['edit-modified','modified_at'],['edit-accessed','accessed_at'],
['edit-sha','sha256'],['edit-status','processing_status']])document.getElementById(id).value=x[key]??'';
document.getElementById('edit-panel').open=true;document.getElementById('edit-panel').scrollIntoView()}};
if(x.origin==='directory'&&x.availability==='present'){{const analyze=node('button','Проверить ПДн');analyze.onclick=async()=>{{try{{
renderAnalysis(await api('/api/objects/'+x.id+'/analyze',{{method:'POST'}}));notice('Анализ завершен');refresh()
}}catch(e){{notice(e.message,true)}}}};actions.appendChild(analyze)}}
const del=node('button','Удалить запись');del.onclick=async()=>{{if(!confirm('Удалить только запись каталога? Файл сохранится.'))return;
try{{await api('/api/objects/'+x.id,{{method:'DELETE'}});refresh()}}catch(e){{notice(e.message,true)}}}};actions.append(edit,del)}}
tr.appendChild(actions);body.appendChild(tr)}}}}catch(e){{notice(e.message,true)}}}}
document.querySelector('#search').onclick=refresh;
if(canEdit){{
async function sources(){{const data=await api('/api/sources');const e=document.getElementById('source-list');e.replaceChildren();
for(const s of data){{const o=node('option',s.relative_path);o.value=s.id;e.appendChild(o)}}}}
document.getElementById('source-add').onclick=async()=>{{try{{await api('/api/sources',{{method:'POST',headers:{{'Content-Type':'application/json'}},
body:JSON.stringify({{relative_path:document.getElementById('source-path').value}})}});await sources();notice('Источник зарегистрирован')}}catch(e){{notice(e.message,true)}}}};
document.getElementById('source-scan').onclick=async()=>{{try{{const id=document.getElementById('source-list').value;
if(!id)throw new Error('Сначала зарегистрируйте каталог');const d=await api('/api/sources/'+id+'/scan',{{method:'POST'}});
notice('Обновлено файлов: '+d.inventoried);refresh()}}catch(e){{notice(e.message,true)}}}};
document.getElementById('object-add').onclick=async()=>{{try{{const s=document.getElementById('new-size').value;
await api('/api/objects',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{
name:document.getElementById('new-name').value,file_type:document.getElementById('new-type').value,
category:document.getElementById('new-category').value,size_bytes:s?Number(s):null}})}});notice('Запись создана');refresh()}}catch(e){{notice(e.message,true)}}}};
document.getElementById('edit-save').onclick=async()=>{{try{{if(selectedObjectId===null)throw new Error('Сначала выберите запись');
const fieldMap=[['edit-name','name'],['edit-type','file_type'],['edit-category','category'],['edit-owner','owner'],
['edit-created','created_at'],['edit-modified','modified_at'],['edit-accessed','accessed_at'],
['edit-sha','sha256'],['edit-status','processing_status']];const changes={{}};
for(const [id,key] of fieldMap)changes[key]=document.getElementById(id).value||null;
const size=document.getElementById('edit-size').value;changes.size_bytes=size===''?null:Number(size);
await api('/api/objects/'+selectedObjectId,{{method:'PATCH',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(changes)}});
notice('Запись обновлена');refresh()}}catch(e){{notice(e.message,true)}}}};
async function fileRequest(path,category){{const f=document.getElementById('file').files[0];if(!f)throw new Error('Выберите документ');
if(category!==null){{category=category.trim();if(!category||category.length>100)throw new Error('Укажите категорию длиной до 100 символов')}}
const d=new FormData();d.append('document',f);if(category!==null)d.append('category',category);const result=await api(path,{{method:'POST',body:d}});
const analysis=category===null?result:result.analysis;renderAnalysis(analysis,false);openDocument(f,analysis);return result}}
function renderAnalysis(result,showSegments=true){{const root=document.getElementById('result');root.replaceChildren();
root.appendChild(node('p','Режим: '+result.mode+' · статус: '+result.status));
if(result.model_error)root.appendChild(node('p','PERSON недоступен: '+result.model_error));
for(const error of result.errors||[])root.appendChild(node('p','Ошибка '+error.stage+': '+error.error));
if(showSegments)for(const segment of result.segments||[]){{const title=node('h3','Фрагмент '+segment.segment_id+
(segment.page_number?' · страница '+segment.page_number:''));root.appendChild(title);
const content=document.createElement('div');content.className='segment';const entities=(result.entities||[])
.filter(x=>x.segment_id===segment.segment_id).sort((a,b)=>a.start-b.start||a.end-b.end);
let cursor=0;for(const entity of entities){{if(entity.start<cursor)continue;
content.appendChild(document.createTextNode(segment.text.slice(cursor,entity.start)));
const mark=node('mark',segment.text.slice(entity.start,entity.end));mark.title=entity.label+' · '+entity.source;
content.appendChild(mark);cursor=entity.end}}
content.appendChild(document.createTextNode(segment.text.slice(cursor)));root.appendChild(content)}}
const table=document.createElement('table');const head=document.createElement('thead');const row=document.createElement('tr');
for(const name of ['Тип','Текст','Начало','Конец','Источник','Оценка','Фрагмент / страница','Координаты'])row.appendChild(node('th',name));
head.appendChild(row);table.appendChild(head);const body=document.createElement('tbody');
for(const x of result.entities||[]){{const tr=document.createElement('tr');for(const value of [x.label,x.text,x.start,x.end,x.source,
x.score?.toFixed(3),x.segment_id+(x.page_number?' / '+x.page_number:''),
x.bounding_box?x.bounding_box.join(', ')+' ('+x.coordinate_system+')':null])tr.appendChild(node('td',value));
body.appendChild(tr)}}table.appendChild(body);root.appendChild(table);
if(!(result.entities||[]).length)root.appendChild(node('p','ПДн не обнаружены в извлеченном тексте'))}}
const previewState={{file:null,result:null,page:1,count:1}};
function highlightedText(container,segment,result){{const entities=(result.entities||[])
.filter(x=>x.segment_id===segment.segment_id).sort((a,b)=>a.start-b.start||a.end-b.end);
let cursor=0;for(const entity of entities){{if(entity.start<cursor||entity.end>segment.text.length)continue;
container.appendChild(document.createTextNode(segment.text.slice(cursor,entity.start)));
const mark=node('mark',segment.text.slice(entity.start,entity.end));mark.title=entity.label+' · '+entity.source;
container.appendChild(mark);cursor=entity.end}}
container.appendChild(document.createTextNode(segment.text.slice(cursor)))}}
function previewText(reason=''){{const body=document.getElementById('preview-body');body.replaceChildren();
const page=document.createElement('div');page.className='preview-text';
if(reason)page.appendChild(node('p',reason+' Показан извлечённый текст документа.'));
for(const segment of previewState.result.segments||[]){{const paragraph=document.createElement('p');
highlightedText(paragraph,segment,previewState.result);page.appendChild(paragraph)}}
body.appendChild(page);document.getElementById('preview-counter').textContent='Весь документ';
document.getElementById('preview-prev').hidden=true;document.getElementById('preview-next').hidden=true;
document.getElementById('preview-findings').textContent='Жёлтым выделены найденные сущности в извлечённом тексте.'}}
function previewDocx(blocks){{const body=document.getElementById('preview-body');body.replaceChildren();
const page=document.createElement('div');page.className='preview-text';
function paragraphView(paragraph){{const heading=(paragraph.style||'').toLowerCase().startsWith('heading');
const element=document.createElement(heading?'h3':'p');const entities=(previewState.result.entities||[])
.filter(x=>x.segment_id===paragraph.segment_id).sort((a,b)=>a.start-b.start||a.end-b.end);
const stops=new Set([0,paragraph.text.length]);for(const run of paragraph.runs||[]){{stops.add(run.start);stops.add(run.end)}}
for(const entity of entities){{stops.add(entity.start);stops.add(entity.end)}}
const positions=[...stops].filter(x=>x>=0&&x<=paragraph.text.length).sort((a,b)=>a-b);
for(let i=0;i<positions.length-1;i++){{const start=positions[i],end=positions[i+1];if(start===end)continue;
const entity=entities.find(x=>x.start<=start&&x.end>=end);const run=(paragraph.runs||[]).find(x=>x.start<=start&&x.end>=end);
const part=node(entity?'mark':'span',paragraph.text.slice(start,end));if(entity)part.title=entity.label+' · '+entity.source;
if(run?.bold)part.style.fontWeight='bold';if(run?.italic)part.style.fontStyle='italic';element.appendChild(part)}}
return element}}
for(const block of blocks){{if(block.kind==='paragraph')page.appendChild(paragraphView(block.paragraph));
else if(block.kind==='table'){{const table=document.createElement('table');
for(const row of block.rows){{const tr=document.createElement('tr');for(const cell of row){{const td=document.createElement('td');
for(const paragraph of cell)td.appendChild(paragraphView(paragraph));tr.appendChild(td)}}table.appendChild(tr)}}page.appendChild(table)}}}}
body.appendChild(page);document.getElementById('preview-counter').textContent='DOCX · весь документ';
document.getElementById('preview-prev').hidden=true;document.getElementById('preview-next').hidden=true;
document.getElementById('preview-findings').textContent='DOCX показан локально: абзацы, таблицы и прямое форматирование текста. Точная пагинация Word и встроенные изображения не воспроизводятся.'}}
async function previewPage(){{const body=document.getElementById('preview-body');body.replaceChildren();
body.appendChild(node('p','Загружаю страницу…'));
const form=new FormData();form.append('document',previewState.file);form.append('page_number',String(previewState.page));
const isDocx=previewState.file.name.toLowerCase().endsWith('.docx');
try{{const page=await api('/api/preview-page',{{method:'POST',body:form}});
if(page.kind==='docx'){{previewDocx(page.blocks);return}}
previewState.count=page.page_count;body.replaceChildren();
const frame=document.createElement('div');frame.className='original-page';
const image=document.createElement('img');image.src=page.image;image.alt='Исходная страница '+previewState.page;frame.appendChild(image);
const findings=(previewState.result.entities||[]).filter(x=>x.page_number===previewState.page);
let shown=0;for(const entity of findings){{const box=entity.bounding_box;
if(!box||!['pdf_points','image_pixels'].includes(entity.coordinate_system))continue;
const [left,top,right,bottom]=box;if(right<=left||bottom<=top||left<0||top<0||right>page.width+1||bottom>page.height+1)continue;
const highlight=document.createElement('span');highlight.className='pii-box '+entity.decision;
highlight.style.left=(100*left/page.width)+'%';highlight.style.top=(100*top/page.height)+'%';
highlight.style.width=(100*(right-left)/page.width)+'%';highlight.style.height=(100*(bottom-top)/page.height)+'%';
highlight.title=entity.label+': '+entity.text;frame.appendChild(highlight);shown++}}
body.appendChild(frame);document.getElementById('preview-counter').textContent='Страница '+previewState.page+' из '+page.page_count;
document.getElementById('preview-prev').hidden=previewState.page<=1;
document.getElementById('preview-next').hidden=previewState.page>=page.page_count;
document.getElementById('preview-findings').textContent=shown+' выделено на странице; '+
(findings.length-shown)+' находок без надёжных координат. Зелёный — принято, жёлтый — требует проверки.'
}}catch(error){{if(isDocx)previewText(error.message);else body.replaceChildren(node('p','Не удалось показать страницу: '+error.message))}}}}
function openDocument(file,result){{previewState.file=file;previewState.result=result;
previewState.page=(result.entities||[]).find(x=>x.page_number)?.page_number||1;
document.getElementById('preview-title').textContent=file.name;
document.getElementById('open-preview').hidden=false;
const dialog=document.getElementById('document-preview');if(!dialog.open)dialog.showModal();
if(/[.](docx|pdf|png|jpe?g|tiff?)$/i.test(file.name))previewPage();else previewText()}}
document.getElementById('open-preview').onclick=()=>openDocument(previewState.file,previewState.result);
document.getElementById('preview-close').onclick=()=>document.getElementById('document-preview').close();
document.getElementById('preview-prev').onclick=()=>{{if(previewState.page>1){{previewState.page--;previewPage()}}}};
document.getElementById('preview-next').onclick=()=>{{if(previewState.page<previewState.count){{previewState.page++;previewPage()}}}};
document.getElementById('analyze').onclick=async()=>{{try{{await fileRequest('/api/analyze',null)}}catch(e){{notice(e.message,true)}}}};
document.getElementById('upload').onclick=async()=>{{try{{await fileRequest('/api/documents',document.getElementById('upload-category').value);refresh()}}catch(e){{notice(e.message,true)}}}};
sources().catch(e=>notice(e.message,true));}}
if(role==='admin'){{async function users(){{const data=await api('/api/users');const body=document.getElementById('users');body.replaceChildren();
for(const u of data){{const tr=document.createElement('tr');tr.appendChild(node('td',u.login));const td=document.createElement('td');
const select=document.createElement('select');for(const r of ['admin','operator','auditor']){{const option=node('option',r);option.value=r;option.selected=u.role===r;select.appendChild(option)}}td.appendChild(select);tr.appendChild(td);
const action=document.createElement('td');const save=node('button','Сохранить');save.onclick=async()=>{{try{{await api('/api/users/'+u.id+'/role',{{method:'PATCH',
headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{role:select.value}})}});notice('Роль изменена');users()}}catch(e){{notice(e.message,true)}}}};
action.appendChild(save);tr.appendChild(action);body.appendChild(tr)}}}}users().catch(e=>notice(e.message,true));}}
if(role==='admin'||role==='auditor'){{async function audit(){{const data=await api('/api/audit');const body=document.getElementById('audit');
body.replaceChildren();for(const a of data){{const tr=document.createElement('tr');for(const k of ['occurred_at','user_id','action','detail'])tr.appendChild(node('td',a[k]));body.appendChild(tr)}}}}
document.getElementById('audit-refresh').onclick=()=>audit().catch(e=>notice(e.message,true));audit().catch(e=>notice(e.message,true));}}
refresh();</script>{_OVERVIEW_SCRIPT}</body></html>"""
