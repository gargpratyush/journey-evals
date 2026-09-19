(() => {
  if (window.top !== window) return;
  const documentId = crypto.randomUUID(), timeOrigin = performance.timeOrigin;
  // Hard caps. Instrumentation that can grow without bound would distort the very timings it
  // reports, so every stream stops and says so rather than quietly flooding the journal.
  const CAP = {events: 1500, timing: 400, entries: 40, controls: 100, regions: 20, text: 800,
    messages: 20, headings: 20, hitTests: 40};
  let sequence = 0, previous = '', previousLayout = '', emitted = 0, timingEmitted = 0;
  const dropped = {events: 0, timing: 0};
  const capped = new Set();
  const emit = (kind, data) => {
    if (emitted >= CAP.events) {
      dropped.events++;
      if (!capped.has('events')) {
        capped.add('events');
        try { window.jevEvidence(JSON.stringify({documentId, sequence:++sequence, timeOrigin,
          at:performance.now(), kind:'capped', data:{stream:'events', cap:CAP.events}})); } catch {}
      }
      return null;
    }
    emitted++;
    window.jevEvidence(JSON.stringify({documentId, sequence:++sequence, timeOrigin,
      at:performance.now(), kind, data}));
    return {documentId, sequence};
  };
  const boxOf = e => { const r = e.getBoundingClientRect();
    return {x:Math.round(r.x), y:Math.round(r.y), w:Math.round(r.width), h:Math.round(r.height)}; };
  const visible = e => {
    const r = e.getBoundingClientRect();
    return e.checkVisibility({checkOpacity:true, checkVisibilityCSS:true}) &&
      r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight;
  };
  const nameOf = e => (e?.getAttribute?.('aria-label') || e?.labels?.[0]?.textContent ||
    e?.getAttribute?.('title') || e?.textContent || '').trim().slice(0, 120);
  const regions = () => {
    const nodes = [...document.querySelectorAll('[role="status"],[role="alert"],[aria-live],[aria-busy],progress')];
    return {truncated:nodes.length > CAP.regions, regions:nodes.slice(0,CAP.regions).map(e=>({
      text:(e.textContent || '').trim().slice(0,CAP.text), visible:visible(e),
      role:e.getAttribute('role'), busy:e.getAttribute('aria-busy'),
      textTruncated:(e.textContent || '').length > CAP.text,
    }))};
  };
  const sample = () => {
    const value = regions(), signature = JSON.stringify(value);
    if (signature !== previous) {
      previous = signature;
      emit('feedback', value);
    }
  };

  // --- Evaluation projection -------------------------------------------------------------
  // A separate, wider view than the action table. It deliberately includes elements the actor
  // may not operate: disabled controls, clipped controls, and controls covered by something
  // else are exactly the ones a layout defect hides, so omitting them would hide the defect.
  const clipping = e => {
    const r = e.getBoundingClientRect();
    for (let parent = e.parentElement; parent && parent !== document.documentElement;
         parent = parent.parentElement) {
      const style = getComputedStyle(parent);
      if (!/hidden|scroll|auto|clip/.test(style.overflowX + ' ' + style.overflowY)) continue;
      const box = parent.getBoundingClientRect();
      const name = parent.tagName.toLowerCase() + (parent.id ? '#' + parent.id : '');
      if (r.right <= box.left + 0.5 || r.left >= box.right - 0.5 ||
          r.bottom <= box.top + 0.5 || r.top >= box.bottom - 0.5) {
        return {clipped:true, by:name, reason:'outside_clipping_ancestor'};
      }
      if (r.right > box.right + 0.5 || r.bottom > box.bottom + 0.5 ||
          r.left < box.left - 0.5 || r.top < box.top - 0.5) {
        return {clipped:true, by:name, reason:'partially_outside_clipping_ancestor'};
      }
    }
    return {clipped:false, by:null, reason:null};
  };
  let hitTests = 0;
  const occlusion = e => {
    if (hitTests >= CAP.hitTests) return {tested:false, occluded:null, by:null};
    const r = e.getBoundingClientRect(), x = r.x + r.width / 2, y = r.y + r.height / 2;
    if (!r.width || !r.height || x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) {
      return {tested:false, occluded:null, by:null};
    }
    hitTests++;
    const top = document.elementFromPoint(x, y);
    if (!top || e.contains(top) || top.contains(e)) return {tested:true, occluded:false, by:null};
    return {tested:true, occluded:true, by:top.tagName.toLowerCase() + (top.id ? '#' + top.id : '')};
  };
  const controls = () => {
    hitTests = 0;
    const nodes = [...document.querySelectorAll('button,input,select,textarea,[role="button"],a[href]')]
      .filter(e=>!['password','file','hidden'].includes(e.type));
    return {truncated:nodes.length > CAP.controls, controls:nodes.slice(0,CAP.controls).map(e=>{
      const r = e.getBoundingClientRect(), clip = clipping(e), hit = visible(e) ? occlusion(e)
        : {tested:false, occluded:null, by:null};
      return {tag:e.tagName, label:nameOf(e), disabled:e.matches(':disabled') ||
        e.getAttribute('aria-disabled') === 'true', readOnly:!!e.readOnly, visible:visible(e),
        value:String(e.value ?? '').slice(0,120), rect:{x:r.x,y:r.y,w:r.width,h:r.height},
        viewportClipped:r.x < 0 || r.y < 0 || r.right > innerWidth || r.bottom > innerHeight,
        offscreen:r.bottom <= 0 || r.top >= innerHeight || r.right <= 0 || r.left >= innerWidth,
        ancestorClipped:clip.clipped, clippedBy:clip.by, clipReason:clip.reason,
        occluded:hit.occluded, occludedBy:hit.by, hitTested:hit.tested,
        focused:document.activeElement === e, invalid:e.getAttribute('aria-invalid') === 'true' ||
          (typeof e.checkValidity === 'function' && e.willValidate && !e.checkValidity())};
    })};
  };
  const messages = () => {
    const nodes = [...document.querySelectorAll(
      '[role="alert"],[aria-errormessage],[aria-invalid="true"],.error,.invalid,[data-error]')];
    return {messagesTruncated:nodes.length > CAP.messages, messages:nodes.slice(0,CAP.messages).map(e=>({
      text:(e.textContent || '').trim().slice(0,300), visible:visible(e),
      role:e.getAttribute('role') || e.tagName.toLowerCase()}))};
  };
  const headings = () => {
    const nodes = [...document.querySelectorAll('h1,h2,h3,[role="heading"]')];
    return {headingsTruncated:nodes.length > CAP.headings, headings:nodes.slice(0,CAP.headings).map(e=>({
      level:e.getAttribute('aria-level') || e.tagName.replace('H',''),
      text:(e.textContent || '').trim().slice(0,200), visible:visible(e)}))};
  };
  const capabilities = () => ({observers:{...observers}, supportedEntryTypes:[...supported]});
  const projection = () => ({documentId, timeOrigin, at:performance.now(), viewport:{
    width:innerWidth, height:innerHeight, scrollX:Math.round(scrollX), scrollY:Math.round(scrollY),
    documentHeight:document.documentElement.scrollHeight},
    feedback:regions(), ...controls(), ...messages(), ...headings()});

  // --- Transient state -------------------------------------------------------------------
  // A control that vanished and came back looks fine in both endpoint snapshots. Tracking
  // visibility between them is the only way that state is observable at all.
  const layoutSignature = () => {
    const tracked = [...document.querySelectorAll('button,[role="button"],input,select,progress')]
      .filter(e=>!['password','file','hidden'].includes(e.type)).slice(0, CAP.controls);
    return tracked.map(e=>{
      const shown = visible(e), r = boxOf(e);
      return [(nameOf(e) || e.tagName).replace(/\|/g, ' '), shown ? 1 : 0, shown ? r.x : -1,
        shown ? r.y : -1, shown ? r.w : -1, shown ? r.h : -1,
        e.matches(':disabled') ? 1 : 0].join('|');
    });
  };
  const sampleLayout = () => {
    const current = layoutSignature(), signature = JSON.stringify(current);
    if (signature === previousLayout) return;
    const before = previousLayout ? JSON.parse(previousLayout) : [];
    const first = !previousLayout;
    previousLayout = signature;
    if (first) return;
    const seen = new Map(before.map(row=>[row.split('|')[0], row]));
    const changes = [];
    for (const row of current) {
      const parts = row.split('|'), key = parts[0], old = seen.get(key);
      seen.delete(key);
      if (old === row) continue;
      const was = old ? old.split('|') : null;
      changes.push({label:key, visible:parts[1] === '1', disabled:parts[6] === '1',
        rect:{x:+parts[2], y:+parts[3], w:+parts[4], h:+parts[5]},
        wasVisible:was ? was[1] === '1' : null, appeared:!was});
    }
    for (const [key, row] of seen) {
      changes.push({label:key, visible:false, disabled:row.split('|')[6] === '1',
        rect:null, wasVisible:row.split('|')[1] === '1', removed:true});
    }
    if (changes.length) emit('visibility', {changes:changes.slice(0, CAP.entries),
      truncated:changes.length > CAP.entries});
  };

  // --- Native performance observers ------------------------------------------------------
  // These are browser measurements, not estimates. Interpretation happens in code later: a
  // long frame is a frame, not proof of jank, and a layout shift is not a CLS score.
  const pending = {events:[], longFrames:[], shifts:[]};
  let flushScheduled = false;
  const flushTiming = () => {
    flushScheduled = false;
    if (!pending.events.length && !pending.longFrames.length && !pending.shifts.length) return;
    if (timingEmitted >= CAP.timing) {
      dropped.timing += pending.events.length + pending.longFrames.length + pending.shifts.length;
      pending.events.length = 0; pending.longFrames.length = 0; pending.shifts.length = 0;
      return;
    }
    timingEmitted++;
    const batch = {events:pending.events.slice(0, CAP.entries),
      longFrames:pending.longFrames.slice(0, CAP.entries),
      shifts:pending.shifts.slice(0, CAP.entries), dropped:{...dropped}};
    pending.events.length = 0; pending.longFrames.length = 0; pending.shifts.length = 0;
    emit('timing', batch);
  };
  const scheduleFlush = () => {
    if (flushScheduled) return;
    flushScheduled = true;
    setTimeout(flushTiming, 250);
  };
  const supported = new Set(PerformanceObserver.supportedEntryTypes || []);
  const observe = (type, handler, options) => {
    if (!supported.has(type)) return false;
    try {
      new PerformanceObserver(list => { handler(list.getEntries()); scheduleFlush(); })
        .observe({type, ...options});
      return true;
    } catch { return false; }
  };
  const observers = {
    event: observe('event', entries => {
      for (const entry of entries) {
        if (pending.events.length >= CAP.entries) { dropped.timing++; break; }
        pending.events.push({name:entry.name, startTime:Math.round(entry.startTime),
          duration:Math.round(entry.duration),
          processingDelay:Math.round(entry.processingStart - entry.startTime),
          processingDuration:Math.round(entry.processingEnd - entry.processingStart),
          interactionId:entry.interactionId ?? null,
          target:entry.target ? nameOf(entry.target) : null});
      }
    }, {durationThreshold:16, buffered:true}),
    longAnimationFrame: observe('long-animation-frame', entries => {
      for (const entry of entries) {
        if (pending.longFrames.length >= CAP.entries) { dropped.timing++; break; }
        pending.longFrames.push({startTime:Math.round(entry.startTime),
          duration:Math.round(entry.duration),
          blockingDuration:Math.round(entry.blockingDuration ?? 0),
          renderStart:Math.round(entry.renderStart ?? 0),
          scripts:(entry.scripts || []).slice(0,3).map(s=>({
            invoker:String(s.invoker || '').slice(0,120), invokerType:s.invokerType || null,
            duration:Math.round(s.duration || 0)}))});
      }
    }, {buffered:true}),
    layoutShift: observe('layout-shift', entries => {
      for (const entry of entries) {
        if (pending.shifts.length >= CAP.entries) { dropped.timing++; break; }
        pending.shifts.push({startTime:Math.round(entry.startTime),
          value:Number(entry.value.toFixed(5)), hadRecentInput:!!entry.hadRecentInput,
          sources:(entry.sources || []).slice(0,3).map(s=>({
            node:s.node ? String(s.node.tagName || s.node.nodeName || '').toLowerCase() : null,
            previous:s.previousRect ? {x:Math.round(s.previousRect.x), y:Math.round(s.previousRect.y),
              w:Math.round(s.previousRect.width), h:Math.round(s.previousRect.height)} : null,
            current:s.currentRect ? {x:Math.round(s.currentRect.x), y:Math.round(s.currentRect.y),
              w:Math.round(s.currentRect.width), h:Math.round(s.currentRect.height)} : null}))});
      }
    }, {buffered:true}),
  };

  const mutated = () => { sample(); sampleLayout(); };
  const observer = new MutationObserver(mutated);
  observer.observe(document, {subtree:true, childList:true, attributes:true, characterData:true});
  for (const type of ['click','input','change']) {
    addEventListener(type, event => {
      const e = event.target;
      if (['password','file','hidden'].includes(e?.type)) return;
      emit('input', {type, trusted:event.isTrusted, tag:e?.tagName || null, label:nameOf(e)});
      mutated();
    }, true);
  }
  addEventListener('DOMContentLoaded', mutated);
  // Flush observer records at a document boundary where one is offered. An unload callback is
  // not guaranteed, so a missing tail is reported as incomplete rather than assumed empty.
  addEventListener('pagehide', () => { try { flushTiming(); } catch {} }, {capture:true});

  window.__jevTelemetry = {projection, capabilities, checkpoint:label=>{
    observer.takeRecords();
    sample();
    sampleLayout();
    flushTiming();
    const watermark = emit('watermark', {label});
    return {...(watermark || {documentId, sequence:-1}), timeOrigin, at:performance.now(),
      url:location.href, title:document.title, feedback:regions(), evaluation:projection(),
      capabilities:capabilities(),
      caps:{emitted, timingEmitted, dropped:{...dropped}, capped:[...capped]}};
  }};
  emit('begin', {url:location.href, viewport:{width:innerWidth, height:innerHeight},
    capabilities:capabilities()});
  sample();
  sampleLayout();
})();
