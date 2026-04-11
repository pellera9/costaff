// ============================================================
// Diary Module
// ============================================================
const Diary = {
    async init() {
        await this.load();
    },

    async load() {
        const filter = document.getElementById('diary-days-filter');
        const days = filter ? parseInt(filter.value) : 7;
        const feed = document.getElementById('diary-feed');
        if (!feed) return;
        try {
            const entries = await API.fetch(`/api/diary?days=${days}`);
            this.render(Array.isArray(entries) ? entries : []);
        } catch (err) {
            feed.innerHTML = `<div class="flex items-center justify-center h-40 text-red-400 text-xs font-bold">載入失敗</div>`;
        }
    },

    render(entries) {
        const feed = document.getElementById('diary-feed');
        if (!feed) return;

        if (entries.length === 0) {
            feed.innerHTML = `<div class="flex flex-col items-center justify-center h-48 text-slate-300 gap-3">
                <i class="fas fa-book text-4xl"></i>
                <span class="text-xs font-bold uppercase tracking-widest">尚無日記記錄</span>
            </div>`;
            return;
        }

        // Group by date
        const byDate = {};
        entries.forEach(e => {
            const d = e.date || (e.created_at ? e.created_at.split('T')[0] : '—');
            if (!byDate[d]) byDate[d] = [];
            byDate[d].push(e);
        });

        const typeLabel = { daily: '每日', weekly: '每週', monthly: '每月' };
        const typeColor = {
            daily:   'bg-blue-50 text-blue-600',
            weekly:  'bg-purple-50 text-purple-600',
            monthly: 'bg-green-50 text-green-600',
        };

        feed.innerHTML = Object.entries(byDate).map(([date, items]) => {
            const cards = items.map(e => {
                const tc = typeColor[e.type] || 'bg-slate-100 text-slate-500';
                const tl = typeLabel[e.type] || e.type || 'note';
                return `<div class="bg-white border border-slate-100 rounded-2xl p-5 shadow-sm hover:shadow-md transition-all">
                    <div class="flex items-center gap-2 mb-3">
                        <span class="text-[9px] font-black uppercase tracking-widest px-2 py-0.5 rounded-full ${tc}">${tl}</span>
                        <span class="text-xs font-bold text-purple-600">${e.agent_name || '—'}</span>
                    </div>
                    ${e.done ? `
                    <div class="mb-3">
                        <div class="flex items-center gap-1 mb-1">
                            <i class="fas fa-check-circle text-green-500 text-[10px]"></i>
                            <span class="text-[9px] font-black text-slate-400 uppercase tracking-widest">完成事項</span>
                        </div>
                        <p class="text-sm text-slate-700 leading-relaxed whitespace-pre-wrap">${e.done}</p>
                    </div>` : ''}
                    ${e.next ? `
                    <div class="mb-3">
                        <div class="flex items-center gap-1 mb-1">
                            <i class="fas fa-arrow-right text-blue-500 text-[10px]"></i>
                            <span class="text-[9px] font-black text-slate-400 uppercase tracking-widest">下一步</span>
                        </div>
                        <p class="text-sm text-slate-500 leading-relaxed whitespace-pre-wrap">${e.next}</p>
                    </div>` : ''}
                    ${e.blocker ? `
                    <div>
                        <div class="flex items-center gap-1 mb-1">
                            <i class="fas fa-exclamation-triangle text-red-400 text-[10px]"></i>
                            <span class="text-[9px] font-black text-red-400 uppercase tracking-widest">阻礙</span>
                        </div>
                        <p class="text-sm text-red-600 leading-relaxed whitespace-pre-wrap">${e.blocker}</p>
                    </div>` : ''}
                </div>`;
            }).join('');

            return `<div class="mb-8">
                <div class="flex items-center gap-3 mb-4">
                    <div class="w-2 h-2 rounded-full bg-blue-500 shrink-0"></div>
                    <span class="text-sm font-black text-slate-700 tracking-tight">${date}</span>
                    <div class="flex-1 h-px bg-slate-100"></div>
                    <span class="text-[10px] font-bold text-slate-400">${items.length} 筆</span>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">${cards}</div>
            </div>`;
        }).join('');
    },
};
