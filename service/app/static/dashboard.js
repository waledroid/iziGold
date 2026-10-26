const $=id=>document.getElementById(id);
const fmt=(v,d=2)=>v==null?"—":Number(v).toFixed(d);
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}

function getAuthHeaders(){
  const pin = localStorage.getItem('dashboard_pin') || '';
  const h = {'Content-Type': 'application/json'};
  if(pin) h['X-Dashboard-PIN'] = pin;
  return h;
}

async function j(u){const r=await fetch(u);return r.json()}

async function postJson(u, body){
  let h = getAuthHeaders();
  let r = await fetch(u, {method: 'POST', headers: h, body: body !== undefined ? JSON.stringify(body) : undefined});
  if(r.status === 401){
    const entered = prompt('Action locked: enter Dashboard PIN:');
    if(entered){
      localStorage.setItem('dashboard_pin', entered);
      h['X-Dashboard-PIN'] = entered;
      r = await fetch(u, {method: 'POST', headers: h, body: body !== undefined ? JSON.stringify(body) : undefined});
    }
  }
  return r;
}

function openLightbox(src){
  $('lightbox-img').src=src;
  $('lightbox').style.display='flex';
}
function closeLightbox(e){
  if(e.target===$('lightbox')||e.target===$('lightbox-img')) {
    $('lightbox').style.display='none';
  }
}

let _currentOrderDir = 'BUY';
function openOrderModal(){
  if(_activeAsset === 'XAUUSD' && _isWeekend){
    alert('XAUUSD market is closed for the weekend (reopens Sunday 22:00 UTC).\nPlease switch to BTCUSD (iziBit) for 24/7 crypto trading.');
    return;
  }
  $('orderModal').style.display='flex';
  if($('orderModalMarket')) $('orderModalMarket').textContent = _activeAsset;
  if($('orderModalLane')) $('orderModalLane').textContent = _activeTab;
  selectOrderDir(_currentOrderDir);
}
function closeOrderModal(e){
  if(!e || e.target===$('orderModal')) {
    $('orderModal').style.display='none';
  }
}
function selectOrderDir(dir){
  _currentOrderDir = dir;
  $('modalBuyBtn').classList.toggle('selected', dir==='BUY');
  $('modalSellBtn').classList.toggle('selected', dir==='SELL');
  $('orderSubmitBtn').textContent = `Transmit ${dir} @ Market`;
}
async function submitInstantOrder(){
  const btn = $('orderSubmitBtn');
  btn.disabled = true;
  btn.textContent = 'Transmitting…';
  try {
    const s = await j('/api/state');
    const h = (s.heartbeats && s.heartbeats[_activeAsset] && s.heartbeats[_activeAsset].heartbeat) || s.heartbeat;
    const strat = (h && h.active_strategy) || _activeTab || 'iziGold15';
    const price = (h && h.bar_c) || 0.0;
    const r = await postJson('/api/proposal/quick', {direction: _currentOrderDir, strategy_id: strat, price: price, symbol: _activeAsset});
    if(r.ok || r.status === 404){
      closeOrderModal();
      state();
    } else {
      alert('Order error (' + r.status + ')');
    }
  } catch(e) {
    alert('Transmit error: ' + e);
  } finally {
    btn.disabled = false;
    selectOrderDir(_currentOrderDir);
  }
}

let _svcFailStreak=0;
function svcOk(){
  _svcFailStreak=0;
  const el=$('svcdown'); if(el) el.style.display='none';
}
function svcFailed(e){
  console.error('Poll error:', e);
  _svcFailStreak++;
  if(_svcFailStreak>=2){ const el=$('svcdown'); if(el) el.style.display='flex'; }
}

function updateClockAndSession(){
  const now = new Date();
  const utcStr = now.toUTCString().slice(17, 25) + ' UTC';
  const clockEl = $('session-clock');
  if(clockEl) clockEl.textContent = utcStr;

  const parisHour = (now.getUTCHours() + 2) % 24;
  const parisMin = now.getUTCMinutes();
  const minutes = parisHour * 60 + parisMin;

  let sessionName = 'Rollover';
  if(minutes >= 60 && minutes < 540) sessionName = 'Asia';
  else if(minutes >= 540 && minutes < 600) sessionName = 'LDN Pre';
  else if(minutes >= 600 && minutes < 840) sessionName = 'London';
  else if(minutes >= 840 && minutes < 930) sessionName = 'LDN/NY Data';
  else if(minutes >= 930 && minutes < 1080) sessionName = 'LDN/NY Overlap';
  else if(minutes >= 1080 && minutes < 1200) sessionName = 'NY Afternoon';
  else if(minutes >= 1200 && minutes < 1320) sessionName = 'Late NY';
  else if(minutes >= 1320 && minutes < 1380) sessionName = 'NY Close';

  const sessEl = $('session-name');
  if(sessEl) sessEl.textContent = sessionName;
}
setInterval(updateClockAndSession, 1000);
updateClockAndSession();

let _rules=null;
let _aiEnabled=false;

async function toggleAiFromPill(){
  const next = !_aiEnabled;
  try {
    const r = await postJson('/api/rules', {key: 'ai_enabled', value: next ? 'true' : 'false'});
    if(r.ok) state();
  } catch(e){}
}

function switchDockTab(tabId){
  document.querySelectorAll('.dock-tab-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.dock === tabId);
  });
  ['deals', 'trades', 'signals', 'matrix'].forEach(id => {
    const el = $('dock-' + id);
    if(el) el.style.display = (id === tabId) ? 'block' : 'none';
  });
  $('dockSigControls').style.display = (tabId === 'signals') ? 'block' : 'none';
  if(tabId === 'signals') signals();
}

async function state(){
  try {
    const s=await j('/api/state');
    svcOk();
    $('stale').style.display=(s.age_s==null||s.age_s>30)?'flex':'none';

    _isWeekend = !!s.is_weekend;
    _marketStatus = s.market_status || null;

    const userPinnedAsset = sessionStorage.getItem('user_selected_asset');
    if(_isWeekend && !userPinnedAsset && _activeAsset !== 'BTCUSD'){
      switchAsset('BTCUSD', false);
    }
    updateMarketClosedBanner();

    // EA Status
    const hbs = s.heartbeats || {};
    const symKeys = Object.keys(hbs);
    let eaOk = false;
    let eaText = 'EA OFF';
    if(symKeys.length > 1){
      const onlineCount = symKeys.filter(k => hbs[k].age_s <= 30).length;
      eaOk = onlineCount > 0;
      eaText = `EA: ${onlineCount}/${symKeys.length} Online`;
    } else {
      eaOk = s.age_s != null && s.age_s <= 30;
      eaText = eaOk ? `EA ${s.age_s.toFixed(0)}s` : 'EA OFF';
    }
    $('ea-dot').className = 'status-dot ' + (eaOk ? 'green' : 'red');
    $('ea-text').textContent = eaText;

    // Mode Buttons
    $('mAuto').className = 'seg-btn ' + (s.mode==='auto' ? 'active' : '');
    $('mManual').className = 'seg-btn ' + (s.mode==='manual' ? 'active' : '');

    _rules=s.rules||null;
    if(_rules){
      if($('rAdr')) $('rAdr').className = 'seg-btn ' + (_rules.entry_mode==='adr' ? 'active' : '');
      if($('rFixed')) $('rFixed').className = 'seg-btn ' + (_rules.entry_mode==='fixed' ? 'active' : '');
      if($('rHybrid')) $('rHybrid').className = 'seg-btn ' + (_rules.entry_mode==='hybrid' ? 'active' : '');
      if($('adaptiveTierBadge') && s.adaptive_tier){
        const at = s.adaptive_tier;
        $('adaptiveTierBadge').textContent = `⚡ Adaptive: ${at.tier.toUpperCase()} (${at.description})`;
      }
      if(document.activeElement!==$('rHtf')) $('rHtf').value=_rules.htf_enforce;

      const isE200 = _rules.ema200_enforce==='on';
      $('rE200').className = 'filter-toggle ' + (isE200 ? 'active' : '');

      const isDyn = _rules.dynamic_strategy==='auto';
      if($('rDyn')){
        $('rDyn').className = 'filter-toggle ' + (isDyn ? 'active' : '');
        $('rDyn').textContent = isDyn ? 'DYN: AUTO' : 'DYN: OFF';
      }

      _aiEnabled = _rules.ai_enabled === true || _rules.ai_enabled === 'true' || _rules.ai_enabled === 'on';
      $('ai-dot').className = 'status-dot ' + (_aiEnabled ? 'green' : 'gray');
      $('ai-text').textContent = _aiEnabled ? 'AI' : 'AI:off';

      let activeGates = 0;
      if(_rules.htf_enforce && _rules.htf_enforce !== 'off') activeGates++;
      if(isE200) activeGates++;
      if(isDyn) activeGates++;
      if($('gatesCount')) $('gatesCount').textContent = `${activeGates} on`;
    }

    renderProposal(s.proposal);
    const h = (hbs[_activeAsset] && hbs[_activeAsset].heartbeat) || s.heartbeat;

    if(h){
      const dec = _activeAsset.includes('BTC') ? 1 : 2;
      if(h.bar_c) $('livePrice').textContent = fmt(h.bar_c, dec);
      if(h.spread_points != null) $('spreadPill').textContent = `${fmt(h.spread_points, 0)}pt`;
      if($('orderModalPrice')) $('orderModalPrice').textContent = `$${fmt(h.bar_c, dec)}`;
      if($('orderModalLane')) $('orderModalLane').textContent = `${esc(h.active_strategy || _activeTab)}`;
      if($('orderModalMarket')) $('orderModalMarket').textContent = _activeAsset;
    }

    // Open positions across ALL assets
    const allPositions = (s.all_positions && s.all_positions.length > 0)
      ? s.all_positions
      : ((h && h.positions) ? h.positions : []);

    const posCount = allPositions.length;
    $('dockDealCount').textContent = posCount;
    $('closeAll').disabled = posCount === 0;

    // Build Live Deals Cards for Dock Pane
    if(posCount > 0){
      $('liveDealsList').innerHTML = allPositions.map(p => {
        const isBtc = (p.symbol && p.symbol.includes('BTC'));
        const badge = isBtc
          ? `<span class="chip" style="background:rgba(56,189,248,0.15);color:#38bdf8;border:1px solid rgba(56,189,248,0.3)">₿ BTC</span>`
          : `<span class="chip chip-gold">🥇 XAU</span>`;
        const dec = isBtc ? 1 : 2;
        return `
        <div style="display:flex;align-items:center;justify-content:space-between;background:rgba(255,255,255,0.03);border:1px solid var(--border-subtle);border-radius:8px;padding:8px 12px;margin-bottom:6px;">
          <div style="display:flex;align-items:center;gap:10px;">
            ${badge}
            <span class="chip ${p.direction==='BUY'?'chip-pos':'chip-neg'}">${esc(p.direction)}</span>
            <span class="mono" style="font-weight:700;">${fmt(p.lots,2)} @ ${fmt(p.open_price,dec)}</span>
            <span class="mono" style="font-size:11px;color:var(--text-dim);">SL ${fmt(p.sl,dec)}</span>
          </div>
          <div style="display:flex;align-items:center;gap:12px;">
            <span class="mono ${p.profit>=0?'pos':'neg'}" style="font-size:14px;font-weight:800;">${p.profit>=0?'+':''}$${fmt(p.profit,2)}</span>
            <button class="btn-close-deal" onclick="closePosition(${Number(p.ticket)}, '${esc(p.symbol||'')}')" title="Close Position #${esc(p.ticket)}">✕</button>
          </div>
        </div>
      `}).join('');
    } else {
      $('liveDealsList').innerHTML = `<span class="chip chip-muted" style="padding:6px 12px;font-size:12px">Flat — No active positions</span>`;
    }

    if(!h) return;

    // Top Glanceable Telemetry HUD (3 Bento Cards)
    const totalFloating = (allPositions && allPositions.length > 0)
      ? allPositions.reduce((acc, p) => acc + (p.profit || 0), 0)
      : 0.0;
    const posChips = posCount > 0
      ? allPositions.map(p => {
          const isBtc = (p.symbol && p.symbol.includes('BTC'));
          const symStr = isBtc ? 'BTC' : 'XAU';
          const pnlStr = (p.profit != null) ? ` (${p.profit>=0?'+':''}$${fmt(p.profit,2)})` : '';
          return `<span class="chip ${p.direction==='BUY'?'chip-pos':'chip-neg'}">${symStr} ${p.direction} ${fmt(p.lots,2)}${pnlStr}</span>`;
        }).join(' ')
      : `<span class="chip chip-muted">FLAT · 0 pos</span>`;

    const ddPct = (h.hwm>0 && h.equity) ? (100*(h.equity/h.hwm-1)).toFixed(1) : '0.0';

    $('strip').innerHTML = `
      <div class="hud-card">
        <div class="hud-header"><span>Master Equity</span><span style="color:var(--gold-primary)">●</span></div>
        <div class="hud-val-row"><span class="hud-hero-val mono" style="color:var(--gold-primary);">$${fmt(h.equity,2)}</span></div>
        <div class="hud-sub-row">
          <span>BAL: $${fmt(h.balance,2)}</span>
          <span>·</span>
          <span>HWM: $${fmt(h.hwm,2)}</span>
          <span>·</span>
          <span class="${Number(ddPct)<0?'neg':'pos'} mono">DD ${ddPct}%</span>
        </div>
      </div>

      <div class="hud-card">
        <div class="hud-header"><span>Account Floating PnL</span><span class="${totalFloating>=0?'pos':'neg'}">●</span></div>
        <div class="hud-val-row"><span class="hud-hero-val mono ${totalFloating>=0?'pos':'neg'}">${totalFloating>=0?'+':''}$${fmt(totalFloating,2)}</span></div>
        <div class="hud-sub-row">
          <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">${posChips}</div>
        </div>
      </div>

      <div class="hud-card">
        <div class="hud-header"><span>Active: ${_activeAsset}</span><span class="${h.window_open?'pos':'neg'}">●</span></div>
        <div class="hud-val-row" style="font-size:15px;font-weight:700;display:flex;align-items:center;gap:8px;">
          <span class="chip chip-gold">${esc(h.active_strategy || _activeTab)}</span>
          <span class="chip ${_activeAsset.includes('BTC') ? 'chip-pos' : (_isWeekend ? 'chip-neg' : (h.window_open?'chip-pos':'chip-muted'))}">${_activeAsset.includes('BTC') ? '24/7 OPEN' : (_isWeekend ? 'WEEKEND CLOSED' : (h.window_open?'04-22h OPEN':'CLOSED'))}</span>
        </div>
        <div class="hud-sub-row">
          <span>EXPO: ${(s.config && s.config.max_daily_exposure_min > 0) ? (h.exposure_min||0) + 'm/' + s.config.max_daily_exposure_min + 'm' : 'OFF'}</span>
          <span>·</span>
          <span>GUARD: ${h.kill_switch?'TRIPPED':'OK'}</span>
        </div>
      </div>
    `;
  } catch(e) {
    svcFailed(e);
  }
}

async function loadProfile(){
  try {
    const {profile,completion_pct}=await j('/api/profile');
    svcOk();
    const prof=$('prof');
    if(prof && profile && profile.name){
      prof.title = `${profile.name} (${completion_pct}%)`;
    }
  } catch(e) {
    svcFailed(e);
  }
}
document.addEventListener('visibilitychange', ()=>{
  if(document.visibilityState==='visible') loadProfile();
});
window.addEventListener('pageshow', e=>{
  if(e.persisted) loadProfile();
});

let chart=null,candleSeries=null,S={};
let _activeTab='iziGold15',_activeAsset='XAUUSD',_expanded=false,_pcData=null;
let _isWeekend=false,_marketStatus=null;
let _chartSpec=null,_srLines=[],_preview=null,_previewWanted=null,_previewLines=[];
const PREVIEW_STORAGE_KEY='xau-dashboard-preview-enabled';

function resetChartForAssetSwitch(){
  clearChartPriceLines(_srLines);
  clearChartPriceLines(_previewLines);
  clearChartPriceLines(_basketLines);
  _lastBasketData = null;
  _lastVpData = null;
  _lastSrZones = null;
  _lastCandleKey = null;
  _lastCandleCount = null;
  _lastTradeId = null;
  _lastTradeCount = null;
  _lastPreviewKey = null;

  if(candleSeries){
    try { candleSeries.setMarkers([]); } catch(e){}
    try { candleSeries.setData([]); } catch(e){}
  }
  Object.values(S).forEach(s => {
    try { if(s && s.setData) s.setData([]); } catch(e){}
  });

  const vpCanvas = $('vpCanvas');
  if(vpCanvas){
    const ctx = vpCanvas.getContext('2d');
    ctx.clearRect(0, 0, vpCanvas.width, vpCanvas.height);
  }
  const rrCanvas = $('rrCanvas');
  if(rrCanvas){
    const ctx = rrCanvas.getContext('2d');
    ctx.clearRect(0, 0, rrCanvas.width, rrCanvas.height);
  }

  if(chart){
    try {
      chart.priceScale('right').applyOptions({ autoScale: true });
    } catch(e){}
  }
}

function switchAsset(sym, userInitiated = false){
  if(userInitiated){
    sessionStorage.setItem('user_selected_asset', sym);
  }
  if(_activeAsset === sym) return;
  _activeAsset = sym;
  if(sym === 'BTCUSD'){
    if(!_activeTab.startsWith('iziBit')) _activeTab = 'iziBit15';
  } else {
    if(_activeTab.startsWith('iziBit')) _activeTab = 'iziGold15';
  }
  updateAssetHeaderUI();
  updateMarketClosedBanner();
  document.querySelectorAll('#stratTabs .tab-btn').forEach(b=>
    b.classList.toggle('active', b.dataset.tab===_activeTab));
  resetChartForAssetSwitch();
  refreshChartAndTrades();
  state();
}

function updateMarketClosedBanner(){
  const banner = $('marketClosedBanner');
  if(!banner) return;
  if(_activeAsset === 'XAUUSD' && _isWeekend){
    banner.style.display = 'flex';
  } else {
    banner.style.display = 'none';
  }
}

function updateAssetHeaderUI(){
  if($('btnAssetGold')) $('btnAssetGold').classList.toggle('active', _activeAsset === 'XAUUSD');
  if($('btnAssetBtc')) $('btnAssetBtc').classList.toggle('active', _activeAsset === 'BTCUSD');
  if($('activeSymbolName')) $('activeSymbolName').textContent = _activeAsset;
  if($('activeSymbolTag')){
    $('activeSymbolTag').textContent = _activeAsset === 'BTCUSD' ? 'BITCOIN' : 'GOLD';
    $('activeSymbolTag').style.color = _activeAsset === 'BTCUSD' ? '#38bdf8' : 'var(--gold-primary)';
    $('activeSymbolTag').style.borderColor = _activeAsset === 'BTCUSD' ? 'rgba(56,189,248,0.3)' : 'rgba(245,197,66,0.3)';
    $('activeSymbolTag').style.background = _activeAsset === 'BTCUSD' ? 'rgba(56,189,248,0.15)' : 'rgba(245,197,66,0.15)';
  }
}

function lineStyleFor(style){
  return style==='solid'?LightweightCharts.LineStyle.Solid:
    style==='dotted'?LightweightCharts.LineStyle.Dotted:
    LightweightCharts.LineStyle.Dashed;
}

function clearChartPriceLines(lines){
  lines.forEach(line=>{
    try{ candleSeries.removePriceLine(line); }catch(e){}
  });
  lines.length=0;
}

function applySeriesSpec(){
  if(!_chartSpec||!_chartSpec.series) return;
  const s=_chartSpec.series;
  try{
    if(s.ema9&&s.ema9.color) S.ema9.applyOptions({color:s.ema9.color,lineWidth:s.ema9.width||1});
    if(s.ema21&&s.ema21.color) S.ema21.applyOptions({color:s.ema21.color,lineWidth:s.ema21.width||1});
    if(s.ema_trade&&s.ema_trade.color) S.ema55.applyOptions({color:s.ema_trade.color,lineWidth:s.ema_trade.width||2});
    if(s.ema200&&s.ema200.color) S.ema200.applyOptions({color:s.ema200.color,lineWidth:s.ema200.width||2});
    if(s.halftrend&&s.halftrend.up) S.ht.applyOptions({color:s.halftrend.up,lineWidth:s.halftrend.width||2});
  }catch(e){}
}

function htPointColor(trend){
  const ht=(_chartSpec&&_chartSpec.series&&_chartSpec.series.halftrend)||{};
  if(trend===0) return ht.up||'#38bdf8';
  if(trend===1) return ht.down||'#fb7185';
  return ht.up||'#38bdf8';
}

function storedPreviewWanted(){
  try{
    const stored=window.localStorage.getItem(PREVIEW_STORAGE_KEY);
    if(stored==='1') return true;
    if(stored==='0') return false;
  }catch(e){}
  return null;
}

function isPreviewEnabled(){
  if(_previewWanted!==null) return _previewWanted;
  return !!(_chartSpec&&_chartSpec.preview&&_chartSpec.preview.enabled_default);
}

function updatePreviewButton(){
  const btn=$('previewBtn');
  if(!btn) return;
  const on=isPreviewEnabled();
  btn.classList.toggle('active',on);
  btn.setAttribute('aria-pressed',on?'true':'false');
  btn.textContent='P: '+(on?'On':'Off');
}

function setPreviewEnabled(on){
  _previewWanted=!!on;
  try{ window.localStorage.setItem(PREVIEW_STORAGE_KEY,_previewWanted?'1':'0'); }catch(e){}
  updatePreviewButton();
  renderPreview();
}

function togglePreview(){ setPreviewEnabled(!isPreviewEnabled()); }

function renderPreview(){
  clearChartPriceLines(_previewLines);
  if(!candleSeries||!isPreviewEnabled()) return;
  const pv=_preview, spec=_chartSpec&&_chartSpec.preview;
  if(!pv||!pv.active||!spec) return;
  [['entry',pv.entry,'P '+pv.direction+' '+pv.entry],
   ['sl',pv.sl,'P SL '+pv.sl],
   ['tp',pv.tp,'P TP '+pv.tp]].forEach(([kind,price,label])=>{
    const style=spec[kind]||{};
    if(!Number.isFinite(price)||typeof style.color!=='string') return;
    try{
      _previewLines.push(candleSeries.createPriceLine({
        price:price,
        color:style.color,
        lineWidth:Number.isFinite(style.width)?style.width:1,
        lineStyle:lineStyleFor(style.style),
        axisLabelVisible:true,
        title:label
      }));
    }catch(e){}
  });
}

let _basketLines=[];
let _lastBasketData=null;
let _rrWanted=true;

function toggleRr(){
  _rrWanted = !_rrWanted;
  const btn = $('rrBtn');
  if(btn){
    btn.classList.toggle('active', _rrWanted);
    btn.textContent = 'RR: ' + (_rrWanted ? 'On' : 'Off');
  }
  renderRiskRewardTool(_lastBasketData);
}

function getChartTimeOffset(candles){
  if(!candles || !candles.length) return 0;
  const lastCandleTime = candles[candles.length - 1].t;
  const nowUtc = Math.floor(Date.now() / 1000);
  const diffSec = lastCandleTime - nowUtc;
  // Snap difference to nearest 30-minute block (e.g. 0, 3600, 7200, 10800)
  return Math.round(diffSec / 1800) * 1800;
}

function renderRiskRewardTool(basket){
  if(basket !== undefined) _lastBasketData = basket;
  const canvas = $('rrCanvas');
  if(!canvas) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();

  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, rect.width, rect.height);

  const bk = _lastBasketData;
  if(!_rrWanted || !bk || !candleSeries || !chart) return;
  if(bk.state !== 'open' && bk.state !== 'closed') return;

  const bSym = (bk.symbol || '').toUpperCase();
  const aSym = (_activeAsset || 'XAUUSD').toUpperCase();
  if(bSym && bSym !== aSym) return;

  const entry = Number(bk.entry);
  const sl = Number(bk.sl);
  const tp = Number(bk.tp);
  const isBuy = (bk.direction || 'BUY').toUpperCase() === 'BUY';

  if(!Number.isFinite(entry) || entry <= 0 || !Number.isFinite(sl) || sl <= 0) return;

  const yEntry = candleSeries.priceToCoordinate(entry);
  const ySl = candleSeries.priceToCoordinate(sl);
  const yTp = (Number.isFinite(tp) && tp > 0) ? candleSeries.priceToCoordinate(tp) : null;

  if(yEntry == null || ySl == null) return;

  const w = rect.width;
  const h = rect.height;

  if(!_pcData || !_pcData.candles || !_pcData.candles.length) return;
  const candles = _pcData.candles;
  const lastCandle = candles[candles.length - 1];

  let xStart = null;
  let xEnd = null;

  const chartOffset = (bk.server_offset != null && bk.server_offset !== 0) 
    ? bk.server_offset 
    : getChartTimeOffset(candles);

  // 1. EXACT ENTRY TIME (aligned to chart candle time)
  if(bk.open_ts){
    let tsSec = typeof bk.open_ts === 'number' ? bk.open_ts : Math.floor(new Date(bk.open_ts).getTime() / 1000);
    if(Number.isFinite(tsSec) && tsSec > 0){
      let targetTs = tsSec;
      if(chartOffset !== 0 && (tsSec + chartOffset <= lastCandle.t + 3600)){
        targetTs = tsSec + chartOffset;
      }
      const entryBar = barTime(candles, targetTs);
      if(entryBar){
        xStart = chart.timeScale().timeToCoordinate(entryBar);
        if(xStart == null && targetTs < candles[0].t){
          xStart = 0;
        }
      }
    }
  }

  // 2. EXACT EXIT TIME (or current bar if open)
  if(bk.state === 'closed' && bk.exit_ts){
    let exitSec = typeof bk.exit_ts === 'number' ? bk.exit_ts : Math.floor(new Date(bk.exit_ts).getTime() / 1000);
    if(Number.isFinite(exitSec) && exitSec > 0){
      let targetExit = exitSec;
      if(chartOffset !== 0 && (exitSec + chartOffset <= lastCandle.t + 3600)){
        targetExit = exitSec + chartOffset;
      }
      const exitBar = barTime(candles, targetExit);
      if(exitBar){
        xEnd = chart.timeScale().timeToCoordinate(exitBar);
      }
    }
  } else {
    // Open position: extends exactly to the current bar
    xEnd = chart.timeScale().timeToCoordinate(lastCandle.t);
  }

  // Fallbacks if off-screen or unresolved
  if(xStart == null){
    const defaultX = chart.timeScale().timeToCoordinate(lastCandle.t);
    xStart = (defaultX != null) ? Math.max(0, defaultX - 120) : w * 0.7;
  }
  if(xEnd == null){
    const defaultX = chart.timeScale().timeToCoordinate(lastCandle.t);
    xEnd = (defaultX != null) ? defaultX : w - 55;
  }

  if(xEnd <= xStart + 5){
    xEnd = xStart + 25;
  }

  const boxW = xEnd - xStart;
  const rDist = Math.abs(entry - sl);
  const targetDist = (tp > 0) ? Math.abs(tp - entry) : (1.5 * rDist);
  const rrRatio = (rDist > 0) ? (targetDist / rDist).toFixed(2) : '1.50';

  // 1. Target / Profit Box (TradingView Green shaded rectangle - NO OUTLINE)
  if(yTp != null){
    const topG = Math.min(yEntry, yTp);
    const botG = Math.max(yEntry, yTp);
    const hG = Math.max(2, botG - topG);

    ctx.fillStyle = 'rgba(16, 185, 129, 0.22)';
    ctx.fillRect(xStart, topG, boxW, hG);
  }

  // 2. Risk / Stop Box (TradingView Red shaded rectangle - NO OUTLINE)
  const topR = Math.min(yEntry, ySl);
  const botR = Math.max(yEntry, ySl);
  const hR = Math.max(2, botR - topR);

  ctx.fillStyle = 'rgba(244, 63, 94, 0.22)';
  ctx.fillRect(xStart, topR, boxW, hR);
}

function renderBasketBand(basket){
  clearChartPriceLines(_basketLines);
  _lastBasketData = basket;
  renderRiskRewardTool(basket);
  if(!candleSeries||!basket||basket.state!=='open') return;

  const bSym = (basket.symbol || '').toUpperCase();
  const aSym = (_activeAsset || 'XAUUSD').toUpperCase();
  if(bSym && bSym !== aSym) return;

  const entry=Number(basket.entry), sl=Number(basket.sl), tp=Number(basket.tp), tp2=Number(basket.tp2);

  if(_pcData && _pcData.candles && _pcData.candles.length){
    const lastKnown = _pcData.candles[_pcData.candles.length - 1];
    if(lastKnown && lastKnown.c > 0){
      if(entry > 0 && (entry / lastKnown.c > 2.5 || entry / lastKnown.c < 0.4)) return;
      if(sl > 0 && (sl / lastKnown.c > 2.5 || sl / lastKnown.c < 0.4)) return;
      if(tp > 0 && (tp / lastKnown.c > 2.5 || tp / lastKnown.c < 0.4)) return;
      if(tp2 > 0 && (tp2 / lastKnown.c > 2.5 || tp2 / lastKnown.c < 0.4)) return;
    }
  }

  if(Number.isFinite(entry)&&entry>0){
    try{
      _basketLines.push(candleSeries.createPriceLine({
        price:entry,
        color:'#38bdf8',
        lineWidth:1,
        lineStyle:LightweightCharts.LineStyle.Dashed,
        axisLabelVisible:true,
        title:(basket.direction||'BUY')+' '+entry.toFixed(2)
      }));
    }catch(e){}
  }
  if(Number.isFinite(sl)&&sl>0){
    try{
      _basketLines.push(candleSeries.createPriceLine({
        price:sl,
        color:'#f43f5e',
        lineWidth:1,
        lineStyle:LightweightCharts.LineStyle.Solid,
        axisLabelVisible:true,
        title:'SL '+sl.toFixed(2)
      }));
    }catch(e){}
  }
  if(Number.isFinite(tp)&&tp>0){
    try{
      _basketLines.push(candleSeries.createPriceLine({
        price:tp,
        color:'#10b981',
        lineWidth:1,
        lineStyle:LightweightCharts.LineStyle.Dashed,
        axisLabelVisible:true,
        title:'TP (1.5R) '+tp.toFixed(2)
      }));
    }catch(e){}
  }
  if(Number.isFinite(tp2)&&tp2>0){
    try{
      _basketLines.push(candleSeries.createPriceLine({
        price:tp2,
        color:'#059669',
        lineWidth:1,
        lineStyle:LightweightCharts.LineStyle.Dotted,
        axisLabelVisible:true,
        title:'TP2 (2.0R) '+tp2.toFixed(2)
      }));
    }catch(e){}
  }
}

function initRealtimeStream(){
  if(typeof EventSource==='undefined') return;
  try{
    const es=new EventSource('/api/stream/chart');
    es.onmessage=function(e){
      try{
        const data=JSON.parse(e.data);
        if(!data) return;

        const aSym = (_activeAsset || 'XAUUSD').toUpperCase();
        if(data.symbol && data.symbol.toUpperCase() !== aSym){
          return;
        }

        if(data.type==='bar'&&candleSeries&&data.candle){
          const currentTf = (['iziGold5', 'iziBit5', 'izi5'].includes(_activeTab)) ? 'M5' : 'M15';
          if(data.timeframe && data.timeframe.toUpperCase() !== currentTf.toUpperCase()){
            return;
          }
          const c = data.candle;
          if(!c || !Number.isFinite(c.c) || c.c <= 0) return;

          if(_pcData && _pcData.candles && _pcData.candles.length){
            const lastKnown = _pcData.candles[_pcData.candles.length - 1];
            if(lastKnown && lastKnown.c > 0){
              const ratio = c.c / lastKnown.c;
              if(ratio > 2.0 || ratio < 0.5){
                return;
              }
            }
          }

          candleSeries.update({
            time:c.t,
            open:c.o,
            high:c.h,
            low:c.l,
            close:c.c
          });
        }else if(data.type==='basket'&&data.basket){
          const bSym = (data.basket.symbol || data.symbol || '').toUpperCase();
          if(bSym && bSym !== aSym) return;
          renderBasketBand(data.basket);
        }else if(data.type==='trade'){
          if(data.symbol && data.symbol.toUpperCase() !== aSym) return;
          refreshChartAndTrades();
        }
      }catch(err){}
    };
  }catch(err){}
}

function initChart(){
  chart=LightweightCharts.createChart($('chartEl'),{
    layout:{
      background:{type:'solid',color:'rgba(8,12,20,0)'},
      textColor:'#94a3b8',
      fontFamily:'"JetBrains Mono", "Plus Jakarta Sans", monospace'
    },
    grid:{
      vertLines:{color:'rgba(255,255,255,0.025)'},
      horzLines:{color:'rgba(255,255,255,0.025)'}
    },
    rightPriceScale:{
      borderColor:'rgba(255,255,255,0.08)'
    },
    timeScale:{
      borderColor:'rgba(255,255,255,0.08)',
      timeVisible:true,
      secondsVisible:false
    },
    crosshair:{
      mode:LightweightCharts.CrosshairMode.Normal,
      vertLine:{color:'rgba(245,197,66,0.35)',width:1,style:LightweightCharts.LineStyle.Dashed},
      horzLine:{color:'rgba(245,197,66,0.35)',width:1,style:LightweightCharts.LineStyle.Dashed}
    },
    autoSize:true
  });

  const noLbl={priceLineVisible:false,lastValueVisible:false};

  S.ema9=chart.addLineSeries(Object.assign({color:'rgba(148,163,184,0.6)',lineWidth:1},noLbl));
  S.ema21=chart.addLineSeries(Object.assign({color:'rgba(148,163,184,0.8)',lineWidth:1},noLbl));
  S.ema55=chart.addLineSeries(Object.assign({color:'#f5c542',lineWidth:2},noLbl));
  S.ema200=chart.addLineSeries(Object.assign({color:'#a78bfa',lineWidth:2},noLbl));
  S.ht=chart.addLineSeries(Object.assign({color:'#38bdf8',lineWidth:2},noLbl));
  S.bb_upper=chart.addLineSeries(Object.assign({color:'rgba(127,140,154,0.6)',lineWidth:1},noLbl));
  S.bb_mid=chart.addLineSeries(Object.assign({color:'rgba(127,140,154,0.6)',lineWidth:1,lineStyle:LightweightCharts.LineStyle.Dashed},noLbl));
  S.bb_lower=chart.addLineSeries(Object.assign({color:'rgba(127,140,154,0.6)',lineWidth:1},noLbl));

  candleSeries=chart.addCandlestickSeries({
    upColor:'#10b981',
    downColor:'#f43f5e',
    borderUpColor:'#10b981',
    borderDownColor:'#f43f5e',
    wickUpColor:'#10b981',
    wickDownColor:'#f43f5e'
  });

  chart.timeScale().subscribeVisibleLogicalRangeChange(()=>{
    if(_lastVpData) requestAnimationFrame(()=>renderVolumeProfile(_lastVpData));
    if(_lastBasketData) requestAnimationFrame(()=>renderRiskRewardTool(_lastBasketData));
  });
  window.addEventListener('resize', ()=>{
    if(_lastVpData) requestAnimationFrame(()=>renderVolumeProfile(_lastVpData));
    if(_lastBasketData) requestAnimationFrame(()=>renderRiskRewardTool(_lastBasketData));
  });

  initRealtimeStream();
}

function setLine(s,arr){
  if(!arr||!_pcData){s.setData([]);return;}
  s.setData(_pcData.candles.map((c,i)=>arr[i]==null?null:{time:c.t,value:arr[i]}).filter(Boolean));
}

let _srWanted = true;
let _lastSrZones = null;

function toggleSr(){
  _srWanted = !_srWanted;
  const btn = $('srBtn');
  if(btn){
    btn.classList.toggle('active', _srWanted);
    btn.textContent = 'SR: ' + (_srWanted ? 'On' : 'Off');
  }
  setSrZones(_lastSrZones, _pcData ? _pcData.candles : [], _chartSpec);
}

function setSrZones(zones,candles,chartSpec){
  _lastSrZones = zones || _lastSrZones;
  clearChartPriceLines(_srLines);
  if(!_srWanted || !candleSeries || !Array.isArray(candles) || !candles.length) return;

  const currentPrice = candles[candles.length - 1] ? candles[candles.length - 1].c : null;
  if(!Number.isFinite(currentPrice)) return;

  // Filter to the most relevant battleground zones closest to price
  const activeZones = Array.isArray(zones) ? zones.filter(z =>
    z && (z.role === 'support' || z.role === 'resistance') &&
    Number.isFinite(z.lo) && Number.isFinite(z.hi) && z.hi > z.lo
  ) : [];

  const resistances = activeZones
    .filter(z => z.role === 'resistance' && z.lo >= currentPrice - 2.0)
    .sort((a,b) => a.lo - b.lo)
    .slice(0, 2); // Show top 2 nearest resistance walls

  const supports = activeZones
    .filter(z => z.role === 'support' && z.hi <= currentPrice + 2.0)
    .sort((a,b) => b.hi - a.hi)
    .slice(0, 2); // Show top 2 nearest support floors

  const battleground = [...resistances, ...supports];

  battleground.forEach(z => {
    const isRes = z.role === 'resistance';
    const edgePrice = isRes ? z.lo : z.hi; // Key barrier edge closest to price
    const color = isRes ? 'rgba(253, 224, 71, 0.75)' : 'rgba(56, 189, 248, 0.75)';
    const tag = (isRes ? 'RES ' : 'SUP ') + z.tier;

    try {
      _srLines.push(candleSeries.createPriceLine({
        price: edgePrice,
        color: color,
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: tag
      }));
    } catch(e){}
  });
}

let _vpWanted = true;
let _lastVpData = null;

function toggleVp(){
  _vpWanted = !_vpWanted;
  const btn = $('vpBtn');
  if(btn){
    btn.classList.toggle('active', _vpWanted);
    btn.textContent = 'VP: ' + (_vpWanted ? 'On' : 'Off');
  }
  renderVolumeProfile(_lastVpData);
}

function renderVolumeProfile(vp){
  _lastVpData = vp || _lastVpData;
  const canvas = $('vpCanvas');
  if(!canvas) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();

  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, rect.width, rect.height);

  if(!_vpWanted || !vp || !Array.isArray(vp.bins) || !vp.bins.length || !candleSeries) return;

  const w = rect.width;
  const h = rect.height;
  const maxBarWidth = Math.min(130, w * 0.16); // Sleek, non-intrusive width
  const rightOffset = 55; // Sits neatly along right price axis

  // Draw histogram bars with buyer (green) and seller (red) volume split mirroring MT5
  vp.bins.forEach(b => {
    if(!b || !Number.isFinite(b.low) || !Number.isFinite(b.high)) return;
    try {
      const yHigh = candleSeries.priceToCoordinate(b.high);
      const yLow = candleSeries.priceToCoordinate(b.low);
      if(yHigh == null || yLow == null) return;

      const top = Math.min(yHigh, yLow);
      const barH = Math.max(1.8, Math.abs(yLow - yHigh));
      const totW = Math.max(2, (b.pct || 0) * maxBarWidth);
      if(totW <= 0) return;

      const buyW = Math.round((b.buy_pct != null ? b.buy_pct : (b.pct || 0) * 0.5) * maxBarWidth);
      const sellW = Math.max(0, Math.round(totW - buyW));

      const inVa = !!b.in_va;
      // Stacking from right price edge leftward:
      // Nearest to right edge is Seller volume (red), extending leftward is Buyer volume (green)
      const xRight = w - rightOffset;

      if(sellW > 0) {
        const xSell = xRight - sellW;
        ctx.fillStyle = inVa ? 'rgba(244, 63, 94, 0.42)' : 'rgba(244, 63, 94, 0.22)';
        ctx.fillRect(xSell, top, sellW, barH - 0.5);
      }

      if(buyW > 0) {
        const xBuy = xRight - sellW - buyW;
        ctx.fillStyle = inVa ? 'rgba(16, 185, 129, 0.42)' : 'rgba(16, 185, 129, 0.22)';
        ctx.fillRect(xBuy, top, buyW, barH - 0.5);
      }

      // Left edge highlight accent
      const xLeft = xRight - (sellW + buyW);
      ctx.fillStyle = inVa ? 'rgba(255, 255, 255, 0.35)' : 'rgba(148, 163, 184, 0.15)';
      ctx.fillRect(xLeft, top, 1.2, barH - 0.5);
    } catch(err){}
  });

  // Draw POC Line cleanly
  if(vp.poc) {
    try {
      const yPoc = candleSeries.priceToCoordinate(vp.poc);
      if(yPoc != null && yPoc >= 0 && yPoc <= h) {
        ctx.save();
        ctx.strokeStyle = '#f5c542';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(w - rightOffset - maxBarWidth - 30, yPoc);
        ctx.lineTo(w - rightOffset, yPoc);
        ctx.stroke();

        ctx.fillStyle = '#f5c542';
        ctx.font = '600 10px "JetBrains Mono", monospace';
        ctx.textAlign = 'right';
        ctx.textBaseline = 'middle';
        ctx.fillText('POC ' + vp.poc.toFixed(2), w - rightOffset - maxBarWidth - 35, yPoc);
        ctx.restore();
      }
    } catch(e){}
  }

  // Draw VAH and VAL boundaries
  [ { p: vp.vah, lbl: 'VAH' }, { p: vp.val, lbl: 'VAL' } ].forEach(lvl => {
    if(!lvl.p) return;
    try {
      const y = candleSeries.priceToCoordinate(lvl.p);
      if(y != null && y >= 0 && y <= h) {
        ctx.save();
        ctx.strokeStyle = 'rgba(56, 189, 248, 0.55)';
        ctx.lineWidth = 1;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(w - rightOffset - maxBarWidth - 20, y);
        ctx.lineTo(w - rightOffset, y);
        ctx.stroke();

        ctx.fillStyle = 'rgba(56, 189, 248, 0.85)';
        ctx.font = '500 9.5px "JetBrains Mono", monospace';
        ctx.textAlign = 'right';
        ctx.textBaseline = 'middle';
        ctx.fillText(lvl.lbl + ' ' + lvl.p.toFixed(2), w - rightOffset - maxBarWidth - 25, y);
        ctx.restore();
      }
    } catch(e){}
  });
}

function setHt(arr){
  if(!arr||!_pcData){S.ht.setData([]);return;}
  S.ht.setData(_pcData.candles.map((c,i)=>{
    const e=arr[i]; if(e==null) return null;
    return {time:c.t,value:e[0],color:htPointColor(e[1])};
  }).filter(Boolean));
}

function barSec(){const c=_pcData&&_pcData.candles;return c&&c.length>1?c[1].t-c[0].t:300;}

function barTime(candles,ts){
  if(!candles || !candles.length) return ts;
  if(ts <= candles[0].t) return candles[0].t;
  if(ts >= candles[candles.length - 1].t) return candles[candles.length - 1].t;
  let best = candles[0].t;
  for(let i = 0; i < candles.length; i++){
    if(candles[i].t <= ts){
      best = candles[i].t;
    } else {
      break;
    }
  }
  return best;
}

function buildMarkers(candles,tr){
  if(!candles.length) return [];
  const offset = getChartTimeOffset(candles);
  const m=[];
  for(const t of tr){
    let chartTs = t.ts || 0;
    if(offset !== 0 && (chartTs + offset <= candles[candles.length - 1].t + 3600)){
      chartTs = chartTs + offset;
    }
    if(chartTs < candles[0].t) continue;
    if(t.event==='open'||t.event==='add'){
      m.push({
        time:barTime(candles, chartTs),
        position:t.direction==='BUY'?'belowBar':'aboveBar',
        color:t.direction==='BUY'?'#10b981':'#f43f5e',
        shape:t.direction==='BUY'?'arrowUp':'arrowDown',
        text:(t.event==='add'?'add ':'')+fmt(t.lots,2)
      });
    }else if(t.event==='close'){
      m.push({
        time:barTime(candles, chartTs),
        position:'aboveBar',
        color:t.profit>=0?'#10b981':'#f43f5e',
        shape:'circle',
        text:(t.profit>=0?'+':'')+fmt(t.profit)
      });
    }
  }
  return m.sort((a,b)=>a.time-b.time);
}

let _lastCandleKey=null,_lastCandleCount=null,_lastTradeId=null,_lastTradeCount=null,_lastTab=null,_lastPreviewKey=null,_lastAsset=null;
let _trades=[];

async function refreshChartAndTrades(){
  if(!window.LightweightCharts){
    $('chartWrap').innerHTML='<div style="padding:30px;text-align:center;color:var(--text-muted)">Chart library loading…</div>';
    return;
  }
  if(!chart) initChart();
  try {
    const tf = (['iziGold5', 'iziBit5', 'izi5'].includes(_activeTab)) ? 'M5' : 'M15';
    const cd = await j('/api/candles?symbol=' + encodeURIComponent(_activeAsset) + '&timeframe=' + encodeURIComponent(tf));
    svcOk();
    let ov={};
    try{ ov=await j('/api/overlays?strategy='+encodeURIComponent(_activeTab)+'&symbol='+encodeURIComponent(_activeAsset)); }catch(e){}
    let trades=_trades;
    try{ trades=(await j('/api/trades?limit=100&symbol='+encodeURIComponent(_activeAsset))).trades||[]; }catch(e){ console.error('trades fetch failed', e); }
    _trades=trades;

    const candles=cd.candles||[];
    const last=candles[candles.length-1];
    const candleKey=last?(last.t+'_'+last.c):null;
    const newestTradeId=trades.length?trades[0].id:null;
    const previewKey=ov.preview?JSON.stringify(ov.preview):'none';

    const assetChanged = _activeAsset !== _lastAsset;
    const tabChanged = _activeTab !== _lastTab;
    const unchanged = !assetChanged && !tabChanged && candleKey===_lastCandleKey && candles.length===_lastCandleCount
      && newestTradeId===_lastTradeId && trades.length===_lastTradeCount
      && previewKey===_lastPreviewKey;

    if(!unchanged){
      if(ov.chart) _chartSpec=ov.chart;
      _preview=ov.preview||null;
      _pcData={candles,ov,tr:trades};
      applySeriesSpec();
      candleSeries.setData(candles.map(c=>({time:c.t,open:c.o,high:c.h,low:c.l,close:c.c})));
      setLine(S.ema9,ov.ema9); setLine(S.ema21,ov.ema21);
      setLine(S.ema55,ov.ema55); setLine(S.ema200,ov.ema200);
      setHt(ov.halftrend);
      setSrZones(ov.sr_zones, candles, _chartSpec);
      setLine(S.bb_upper,ov.bb_upper); setLine(S.bb_mid,ov.bb_mid);
      setLine(S.bb_lower,ov.bb_lower);
      candleSeries.setMarkers(buildMarkers(candles,trades));
      renderVolumeProfile(ov.volume_profile);
      renderPreview();
      updatePreviewButton();
      try{ const bk=await j('/api/basket?symbol='+encodeURIComponent(_activeAsset)); if(bk) renderBasketBand(bk); }catch(e){}
      requestAnimationFrame(()=>renderRiskRewardTool(_lastBasketData));

      if(assetChanged || tabChanged){
        try{ chart.timeScale().fitContent(); }catch(e){}
      }

      _lastCandleKey=candleKey; _lastCandleCount=candles.length;
      _lastTradeId=newestTradeId; _lastTradeCount=trades.length;
      _lastTab=_activeTab; _lastPreviewKey=previewKey;
      _lastAsset=_activeAsset;
    }

    renderTradesTable(trades.slice(0,50));
  } catch(e) {
    svcFailed(e);
  }
}

function setTab(t){
  if(_activeTab === t) return;
  _activeTab=t;
  const newAsset = t.startsWith('iziBit') ? 'BTCUSD' : 'XAUUSD';
  const assetChanged = newAsset !== _activeAsset;
  _activeAsset = newAsset;

  updateAssetHeaderUI();
  document.querySelectorAll('#stratTabs .tab-btn').forEach(b=>
    b.classList.toggle('active', b.dataset.tab===t));

  if(assetChanged){
    resetChartForAssetSwitch();
  }
  refreshChartAndTrades();
}

function toggleExpand(){
  _expanded=!_expanded;
  $('pricePanel').classList.toggle('expanded', _expanded);
}

function zoomToTrade(ts){
  if(!chart||!_pcData||!_pcData.candles.length) return;
  const t=barTime(_pcData.candles,ts), span=90*barSec();
  chart.timeScale().setVisibleRange({from:t-span,to:t+span});
  $('pricePanel').scrollIntoView({behavior:'smooth',block:'start'});
}

async function stats(){
  try {
    const s=await j('/api/stats');
    svcOk();
    const ALLOWED=['iziGold5','iziGold15','iziBit5','iziBit15','izi5','izi15'];
    const rows=Object.entries(s.by_strategy||{}).filter(([k])=>ALLOWED.includes(k.split(' @')[0]));
    const ids=['iziGold5','iziGold15','iziBit5','iziBit15'];
    if(!ids.includes(_activeTab) && !['izi5','izi15'].includes(_activeTab)) _activeTab='iziGold15';
    if(_activeTab==='izi5') _activeTab='iziGold5';
    if(_activeTab==='izi15') _activeTab='iziGold15';
    
    $('stratTabs').innerHTML = `
      <span style="font-size:11px;font-weight:800;color:var(--gold-primary);padding:0 4px;">🥇</span>
      <button type="button" class="tab-btn${_activeTab==='iziGold5'?' active':''}" data-tab="iziGold5">M5</button>
      <button type="button" class="tab-btn${_activeTab==='iziGold15'?' active':''}" data-tab="iziGold15">M15</button>
      <span style="display:inline-block;width:1px;height:14px;background:var(--border-subtle);margin:0 6px;"></span>
      <span style="font-size:11px;font-weight:800;color:#38bdf8;padding:0 4px;">₿</span>
      <button type="button" class="tab-btn${_activeTab==='iziBit5'?' active':''}" data-tab="iziBit5">M5</button>
      <button type="button" class="tab-btn${_activeTab==='iziBit15'?' active':''}" data-tab="iziBit15">M15</button>
    `;
      
    $('cmp').innerHTML='<thead><tr><th>Strategy</th><th>TF</th><th>Signals</th><th>Hit%</th><th>Avg Move</th><th>Action</th></tr></thead><tbody>'+
      rows.map(([key,v])=>{
        const [sid, tf] = key.split(' @');
        const btn=key==='pre-framework'?''
          :`<button class="btn-sm" data-sid="${esc(sid)}">Switch</button>`;
        const isBtc = sid.startsWith('iziBit');
        const badge = isBtc ? '<span style="color:#38bdf8;font-size:11px;margin-right:4px;">₿</span>' : '<span style="color:var(--gold-primary);font-size:11px;margin-right:4px;">🥇</span>';
        return `<tr><td>${badge}<b style="color:#ffffff">${esc(sid)}</b></td><td><span class="chip chip-muted">${esc(tf||'—')}</span></td><td class="mono">${v.signals}</td>`
          +`<td><span class="chip ${v.hit_pct>=50?'chip-pos':'chip-neg'} mono">${fmt(v.hit_pct,1)}%</span></td>`
          +`<td class="mono">${fmt(v.avg_move)}</td><td>${btn}</td></tr>`;
      }).join('')+'</tbody>';
  } catch(e) {
    svcFailed(e);
  }
}

$('stratTabs').addEventListener('click', e=>{
  const b=e.target.closest('[data-tab]');
  if(b) setTab(b.dataset.tab);
});

$('cmp').addEventListener('click', e=>{
  const btn=e.target.closest('[data-sid]');
  if(btn) sw(btn.dataset.sid);
});

async function signals(){
  try {
    const {signals}=await j('/api/signals');
    svcOk();
    const flt=$('sigFilter').value;
    const list=signals.filter(s=>flt==='all'||(flt==='active'?s.is_active:!s.is_active));
    $('sig').innerHTML='<thead><tr><th>Time</th><th>Strategy</th><th>Signal</th><th>AI Forecast</th><th>Verdict</th><th>Outcome</th></tr></thead><tbody>'+
      list.map(s=>{
        const isRefused = !!s.refusal_reason || s.verdict === 'REFUSED';
        const vBadge = isRefused ? 'chip-neg' : (s.verdict === 'CONFIRMED' ? 'chip-pos' : (s.verdict === 'VETO' ? 'chip-neg' : 'chip-gold'));
        const sigBadge = s.signal === 'BUY' ? 'chip-pos' : (s.signal === 'SELL' ? 'chip-neg' : 'chip-muted');
        const verdictContent = isRefused
          ? `<span class="chip chip-neg" style="font-weight:700;">⛔ REFUSED</span><div style="font-size:10px;color:var(--text-muted);margin-top:2px;max-width:240px;line-height:1.2;">${esc(s.refusal_reason || 'Gate blocked')}</div>`
          : `<span class="chip ${vBadge}">${esc(s.verdict)}</span>`;
        return `<tr><td class="mono" style="font-size:11px;color:var(--text-muted);">${new Date(s.created_ts*1000).toLocaleTimeString()}</td><td><b>${esc(s.strategy_id||'—')}</b>${s.timeframe?' <span class="chip chip-muted">'+esc(s.timeframe)+'</span>':''}</td>`+
        `<td><span class="chip ${sigBadge}">${s.signal}</span></td>`+
        `<td>${s.direction} <span class="chip chip-gold mono">${fmt(100*s.confidence,0)}%</span></td>`+
        `<td>${verdictContent}</td>`+
        `<td class="mono">${s.outcome_move==null?'<span style="color:var(--text-dim)">—</span>':fmt(s.outcome_move)}</td></tr>`;
      }).join('')+'</tbody>';
  } catch(e) {
    svcFailed(e);
  }
}

const agree=v=>v===1?'<span class="chip chip-pos">✓</span>'
  :(v===0?'<span class="chip chip-neg">✗</span>':'—');

function renderTradesTable(trades){
    $('tr').innerHTML='<thead><tr><th>📍</th><th>Time</th><th>Event</th><th>Strategy</th><th>Side</th><th>Size @ Price</th><th>Realized P/L</th><th>Fill</th><th>Reason</th><th>MTF</th><th>E200</th><th>Screen</th></tr></thead><tbody>'+
      trades.map(t=>{
        const pl=t.event==='close'?`<span class="${t.profit>=0?'pos':'neg'} mono" style="font-weight:700;">${t.profit>=0?'+':''}$${fmt(t.profit)}</span>`:'';
        const evBadge = t.event==='open'?'chip-pos':(t.event==='close'?'chip-gold':'chip-muted');
        const dirBadge = t.direction==='BUY'?'chip-pos':(t.direction==='SELL'?'chip-neg':'chip-muted');
        const fillTxt = (t.slippage_pts||t.exec_ms)?((t.slippage_pts!=null&&t.slippage_pts!==0?(t.slippage_pts>0?'+':'')+fmt(t.slippage_pts,1)+'pt ':'')+(t.exec_ms?t.exec_ms+'ms':'')):'—';
        return `<tr><td><button class="btn-sm" onclick="zoomToTrade(${Number(t.ts)})" title="Locate">📍</button></td>`+
          `<td class="mono" style="font-size:11px;color:var(--text-muted);">${new Date(t.ts*1000).toLocaleTimeString()}</td><td><span class="chip ${evBadge}">${esc(t.event)}</span></td><td><b>${esc(t.strategy_id||'—')}</b></td><td><span class="chip ${dirBadge}">${esc(t.direction||'—')}</span></td>`+
          `<td class="mono">${fmt(t.lots,2)} @ ${fmt(t.price,2)}</td><td>${pl}</td><td class="mono" style="font-size:11px;color:var(--text-muted);">${fillTxt}</td><td><span style="font-size:11.5px;color:var(--text-muted);">${esc(t.reason||'—')}</span></td>`+
          `<td>${agree(t.htf_agree)}</td><td>${agree(t.ema200_agree)}</td>`+
          `<td><img src="/api/screenshot/${t.id}" class="thumb-img" height="28" onerror="this.style.display='none'" onclick="openLightbox(this.src)"></td></tr>`;
      }).join('')+'</tbody>';
}

function renderProposal(p){
  const el=$('propcard');
  if(!p){el.innerHTML='';return;}
  const badge=p.status==='pending'?'chip-gold':(p.status==='executed'?'chip-pos':(p.status==='blocked'?'chip-neg':'chip-muted'));
  const btns=p.status==='pending'
    ?`<div class="proposal-actions">
        <button class="btn-success" onclick="decide(${Number(p.id)},'take')">Take Trade</button>
        <button class="btn-cancel" onclick="decide(${Number(p.id)},'skip')">Skip</button>
      </div>`
    :'';
  el.innerHTML=`
    <div class="proposal-box">
      <div class="proposal-info">
        <span style="font-size:18px;">${p.kind==='entry'?'📥':'📤'}</span>
        <div>
          <b style="color:var(--gold-primary);">${esc(p.direction)}</b> @ <span class="mono">$${fmt(p.price)}</span>
          <span style="color:var(--text-dim);margin:0 4px;">·</span>
          <span>${esc(p.strategy_id||'')}</span>
          <span style="color:var(--text-dim);margin:0 4px;">·</span>
          <span class="chip ${badge}">${esc(p.status)}</span>
        </div>
      </div>
      ${btns}
    </div>`;
}

async function setMode(m){
  try {
    const r=await postJson('/api/mode',{mode:m});
    if(!r.ok) alert('Mode change error ('+r.status+')');
  } catch(e) { alert('Network error: ' + e); }
  state();
}

async function setRule(key,value){
  try {
    const r=await postJson('/api/rules',{key,value});
    if(!r.ok) alert('Rule change error ('+r.status+')');
  } catch(e) { alert('Network error: ' + e); }
  state();
}

function toggleE200(){
  setRule('ema200_enforce', _rules&&_rules.ema200_enforce==='on'?'off':'on');
}

function toggleDynamicRouting(){
  const isAuto = _rules && _rules.dynamic_strategy === 'auto';
  setRule('dynamic_strategy', isAuto ? 'off' : 'auto');
}

function toggleRule(key){
  setRule(key, _rules&&_rules[key]==='on'?'off':'on');
}

async function decide(pid,action){
  try {
    const r=await postJson('/api/proposal/'+pid,{action});
    if(r.ok){
      const d=await r.json(); 
      if(!d.ok) alert('Decided: '+d.status);
    } else alert('Decision failed ('+r.status+')');
  } catch(e) { alert('Network error: ' + e); }
  state();
}

async function closeAllPos(){
  if(!confirm('Close all open positions?')) return;
  try {
    const r=await postJson('/api/close-all');
    if(!r.ok){
      const d=await r.json().catch(()=>({})); 
      alert('Close error: '+(d.detail||r.status));
    }
  } catch(e) { alert('Network error: ' + e); }
  state();
}

async function sw(id){
  try{
    const r=await postJson('/api/switch',{strategy_id:id});
    if(!r.ok){
      const detail=await r.text().catch(()=>'');
      alert(`Switch failed (${r.status}): ${detail||'server error'}`);
    }
  }catch(e){
    alert('Switch error: '+e);
  }
  state();
}

function toggleGatesMenu(e){
  if(e) e.stopPropagation();
  const m = $('gatesMenu');
  if(!m) return;
  const isShown = m.style.display !== 'none';
  m.style.display = isShown ? 'none' : 'flex';
  if($('navMenu')) $('navMenu').style.display = 'none';
}

function toggleNavMenu(e){
  if(e) e.stopPropagation();
  const m = $('navMenu');
  if(!m) return;
  const isShown = m.style.display !== 'none';
  m.style.display = isShown ? 'none' : 'flex';
  if($('gatesMenu')) $('gatesMenu').style.display = 'none';
}

document.addEventListener('click', e => {
  if(!e.target.closest('.menu-dropdown-wrap')){
    if($('gatesMenu')) $('gatesMenu').style.display = 'none';
    if($('navMenu')) $('navMenu').style.display = 'none';
  }
});

async function closePosition(ticket, symbol){
  if(!confirm(`Close position #${ticket} on ${symbol || 'market'}?`)) return;
  try {
    const r = await postJson('/api/close-position', { ticket: Number(ticket), symbol: symbol || '' });
    if(!r.ok){
      const d = await r.json().catch(()=>({}));
      alert('Close error: ' + (d.detail || r.status));
    }
  } catch(e) {
    alert('Network error: ' + e);
  }
  state();
}

if(_previewWanted===null) _previewWanted=storedPreviewWanted();
updatePreviewButton();
state();loadProfile();refreshChartAndTrades();stats();signals();
setInterval(state,1000);
setInterval(refreshChartAndTrades,30000);
setInterval(stats,30000);
setInterval(signals,30000);
