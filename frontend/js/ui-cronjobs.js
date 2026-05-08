Object.assign(UI, {
    async renderCronjobs() {
        const data = await API.fetch('/api/db/reminders');
        this.drawTable('cron-thead', 'cron-tbody', 'reminders', data);
        const nextEl = document.getElementById('next-cron-time'); const nextMsgEl = document.getElementById('next-cron-msg');
        if (nextEl && nextMsgEl) {
            if (data.length > 0) {
                const upcoming = data.find(r => ['pending', 'scheduled', 'active'].includes(r.status)) || data[0];
                const timeStr = upcoming.cron ? upcoming.cron : upcoming.run_at ? new Date(upcoming.run_at).toLocaleString() : 'STANDBY';
                nextEl.innerText = timeStr; nextMsgEl.innerText = upcoming.prompt || 'No message content';
                nextMsgEl.classList.remove('italic');
            } else { nextEl.innerText = 'NO TASKS'; nextMsgEl.innerText = 'No active schedules detected.'; nextMsgEl.classList.add('italic'); }
        }
    },

    openCronModal() {
        this.editingCronId = null; document.getElementById('cron-modal').classList.remove('hidden');
        document.getElementById('cron-full-input').value = ''; document.getElementById('cron-recipient').value = '';
    },

    openEditCronModal(id, channel, recipient, fullCommand) {
        this.editingCronId = id; document.getElementById('cron-modal').classList.remove('hidden');
        document.getElementById('cron-channel').value = channel; document.getElementById('cron-recipient').value = recipient;
        document.getElementById('cron-full-input').value = fullCommand;
    },

    closeCronModal() { document.getElementById('cron-modal').classList.add('hidden'); this.editingCronId = null; },

    async saveCronjobLine() {
        const input = document.getElementById('cron-full-input').value.trim(); const chan = document.getElementById('cron-channel').value;
        const target = document.getElementById('cron-recipient').value.trim(); if (!input || !target) return alert('FIELDS_REQUIRED');
        const parts = input.split(/\s+/); if (parts.length < 6) return alert('INVALID_FORMAT');
        const cronExpr = parts.slice(0, 5).join(' '); const messageContent = parts.slice(5).join(' ');
        const data = { channel: chan, recipient: target, prompt: messageContent, cron: cronExpr, run_at: null };
        try {
            const url = this.editingCronId ? `/api/reminders/${this.editingCronId}` : '/api/reminders';
            const method = this.editingCronId ? 'PUT' : 'POST';
            await API.fetch(url, { method, body: JSON.stringify(data) });
            this.closeCronModal(); this.renderCronjobs();
        } catch (e) { alert('SYNC_ERROR'); }
    },

    async deleteReminder(id) { if (confirm('PURGE_TASK?')) { try { await API.fetch(`/api/reminders/${id}`, { method: 'DELETE' }); this.renderCronjobs(); } catch (e) { alert('PURGE_FAILED'); } } },
});
