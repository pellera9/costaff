// Multi-CoStaff core switcher (read-only). Hidden on single-core installs.
const Cores = {
    async init() {
        const wrap = document.getElementById('core-switcher');
        if (!wrap) return;
        let cores;
        try { cores = await API.fetch('/api/cores'); } catch (e) { wrap.innerHTML = ''; return; }
        // Remember the active core's container prefix so other tabs (e.g. Agents)
        // can scope host-wide /api/status to THIS core's manager only.
        const active = (cores || []).find(c => c.active);
        if (active) App.state.activeCorePrefix = active.prefix;
        if (!cores || cores.length <= 1) { wrap.innerHTML = ''; return; }  // single core → no switcher
        wrap.innerHTML = `
            <div class="flex items-center gap-2 px-3 py-2 rounded-xl bg-slate-50 border border-slate-200" title="Switch CoStaff core">
                <i class="fas fa-server text-blue-600 text-sm"></i>
                <span class="text-[10px] font-black uppercase tracking-widest text-slate-400">CoStaff</span>
                <select id="core-select" class="bg-transparent text-sm font-bold text-slate-900 outline-none cursor-pointer">
                    ${cores.map(c => `<option value="${c.name}" ${c.active ? 'selected' : ''}>${c.label}</option>`).join('')}
                </select>
            </div>`;
        document.getElementById('core-select').addEventListener('change', async (e) => {
            const sel = e.target;
            sel.disabled = true;
            try {
                await API.post('/api/cores/active', { name: sel.value });
                window.location.reload();  // re-fetch every view from the newly active core
            } catch (err) {
                sel.disabled = false;
                alert('Switch failed: ' + err.message);
            }
        });
    }
};
