import { api } from '../api.js';

export async function renderSettings(view, setMsg) {
  const j = await api('/api/settings');
  const gate = j.gate || {};
  const chip = gate.chip || '运行闸';
  const aligned = !!gate.aligned;
  view.innerHTML = `
    <section class="card">
      <h2>台参数</h2>
      <p class="muted">压实阈值与对账模式；保存后单次落盘：现行阈值、闸快照与闸凭证同事务提交。</p>
      <p>现行阈值读数：<strong data-current-threshold>${gate.threshold_kb ?? ''}</strong> KB
        <span class="tag" data-gate-chip>${chip}</span>
        <span class="muted" data-gate-reason>${gate.reason || '口径已对齐'}</span>
      </p>
      <div class="wall">
        ${(j.items || []).map(s => `<article class="card">
          <h3>${s.label}</h3>
          <p class="muted">${s.key}</p>
          <input data-key="${s.key}" value="${s.value}" />
          ${s.key === 'compact_threshold_kb'
            ? `<p class="muted">执行闸 ${s.runtime_gate} KB · 快照 ${s.snapshot_threshold_kb ?? '-'} · 凭证 ${s.credential || '-'}${s.aligned ? '' : ' · ' + (s.gate_reason || '漂移')}</p>
               <span class="tag" data-item-chip>${s.gate_chip || ''}</span>`
            : ''}
          <button data-save="${s.key}" class="primary" type="button">保存</button>
        </article>`).join('')}
      </div>
    </section>`;
  view.querySelectorAll('[data-save]').forEach(btn => {
    btn.onclick = async () => {
      try {
        const key = btn.dataset.save;
        const input = view.querySelector('input[data-key="' + key + '"]');
        const r = await api('/api/settings', { method:'POST', body: JSON.stringify({ key, value: input.value }) });
        const g = r.gate || {};
        setMsg('已保存 ' + key + '，闸 ' + (g.chip || ''));
        await renderSettings(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
  });

  try {
    const dash = await api('/api/thr_stale/dash');
    view.innerHTML += `<section class="card" style="margin-top:1rem">
      <h3>阈值旧闸旁路（审计）</h3>
      <span class="tag">${dash.chip}</span>
      <p class="muted">真相 ${dash.truth} · 假绿 ${dash.fake_green} · 快照阈值 ${dash.snapshot_threshold_kb ?? '-'} · 凭证 ${dash.credential || '-'}</p>
      <div class="row">
        <button id="note_thr_stale" type="button">记一笔</button>
        <button id="half_thr_stale" type="button">半修自检</button>
      </div>
    </section>`;
    document.getElementById('note_thr_stale').onclick = async () => {
      try {
        await api('/api/thr_stale/note', { method:'POST', body: JSON.stringify({plan_id:1, note:'阈值旧闸'}) });
        setMsg('已记旁路');
        await renderSettings(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
    document.getElementById('half_thr_stale').onclick = async () => {
      try {
        const r = await api('/api/thr_stale/half', { method:'POST', body:'{}' });
        setMsg(r.half_fixed ? '闸已对齐' : ('半修无效：' + (r.reason || '口径漂移')));
        await renderSettings(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
  } catch (e) { /* surface optional */ }

}
