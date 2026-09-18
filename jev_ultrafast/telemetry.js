(() => {
  if (window.top !== window) return;
  const documentId = crypto.randomUUID(), timeOrigin = performance.timeOrigin;
  let sequence = 0, previous = '';
  const emit = (kind, data) => {
    window.jevEvidence(JSON.stringify({documentId, sequence:++sequence, timeOrigin,
      at:performance.now(), kind, data}));
    return {documentId, sequence};
  };
  const visible = e => {
    const r = e.getBoundingClientRect();
    return e.checkVisibility({checkOpacity:true, checkVisibilityCSS:true}) &&
      r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight;
  };
  const regions = () => {
    const nodes = [...document.querySelectorAll('[role="status"],[role="alert"],[aria-live],[aria-busy],progress')];
    return {truncated:nodes.length > 20, regions:nodes.slice(0,20).map(e=>({
      text:(e.textContent || '').trim().slice(0,800), visible:visible(e),
      role:e.getAttribute('role'), busy:e.getAttribute('aria-busy'),
      textTruncated:(e.textContent || '').length > 800,
    }))};
  };
  const sample = () => {
    const value = regions(), signature = JSON.stringify(value);
    if (signature !== previous) {
      previous = signature;
      emit('feedback', value);
    }
  };
  const observer = new MutationObserver(sample);
  observer.observe(document, {subtree:true, childList:true, attributes:true, characterData:true});
  for (const type of ['click','input','change']) {
    addEventListener(type, event => {
      const e = event.target;
      if (['password','file','hidden'].includes(e?.type)) return;
      emit('input', {type, trusted:event.isTrusted, tag:e?.tagName || null,
        label:(e?.getAttribute?.('aria-label') || e?.labels?.[0]?.textContent ||
          e?.textContent || '').trim().slice(0,120)});
      sample();
    }, true);
  }
  addEventListener('DOMContentLoaded', sample);
  const controls = () => {
    const nodes = [...document.querySelectorAll('button,input,select,textarea,[role="button"]')]
      .filter(e=>!['password','file','hidden'].includes(e.type));
    return {truncated:nodes.length > 100, controls:nodes.slice(0,100).map(e=>{
      const r = e.getBoundingClientRect();
      return {tag:e.tagName, label:(e.getAttribute('aria-label') || e.labels?.[0]?.textContent ||
        e.textContent || '').trim().slice(0,120), disabled:e.matches(':disabled') ||
        e.getAttribute('aria-disabled') === 'true', readOnly:!!e.readOnly, visible:visible(e),
        value:String(e.value ?? '').slice(0,120), rect:{x:r.x,y:r.y,w:r.width,h:r.height},
        viewportClipped:r.x < 0 || r.y < 0 || r.right > innerWidth || r.bottom > innerHeight};
    })};
  };
  window.__jevTelemetry = {projection:()=>({documentId, timeOrigin, at:performance.now(),
    feedback:regions(), ...controls()}), checkpoint:label=>{
    observer.takeRecords();
    sample();
    const watermark = emit('watermark', {label});
    return {...watermark, timeOrigin, at:performance.now(), url:location.href,
      title:document.title, feedback:regions(), evaluation:controls()};
  }};
  emit('begin', {url:location.href});
  sample();
})();
