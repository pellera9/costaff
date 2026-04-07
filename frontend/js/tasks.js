const Tasks = {
    currentTasks: new Map(),

    init() {
        this.loadTasks();
        this.setupEventListeners();
        // Auto refresh every 10 seconds to catch 'doing' -> 'done' transitions
        this.refreshInterval = setInterval(() => {
            const el = document.getElementById('view-tasks');
            if (el && !el.classList.contains('hidden')) {
                this.loadTasks();
            }
        }, 10000);
    },

    setupEventListeners() {
        const form = document.getElementById('task-form');
        if (form) {
            form.onsubmit = async (e) => {
                e.preventDefault();
                const formData = new FormData(form);
                const data = Object.fromEntries(formData.entries());
                if (!data.title?.trim()) { alert('Task title is required.'); return; }
                if (!data.spec?.trim()) { alert('Spec / Requirement is required.'); return; }
                const taskId = data.task_id;
                
                try {
                    let res;
                    if (taskId) {
                        // Update existing task
                        res = await API.fetch(`/api/tasks/${taskId}`, {
                            method: 'PUT',
                            body: JSON.stringify(data)
                        });
                    } else {
                        // Create new task
                        res = await API.fetch('/api/tasks', {
                            method: 'POST',
                            body: JSON.stringify(data)
                        });
                    }
                    
                    if (res.status === 'success') {
                        this.closeModal();
                        this.loadTasks();
                    }
                } catch (err) {
                    alert('Failed to save task: ' + err.message);
                }
            };
        }
    },

    async loadTasks() {
        try {
            const tasks = await API.fetch('/api/tasks');
            this.currentTasks.clear();
            if (Array.isArray(tasks)) {
                tasks.forEach(t => this.currentTasks.set(t.id, t));
            }
            this.renderKanban(tasks);
        } catch (err) {
            console.error('Failed to load tasks:', err);
        }
    },

    renderKanban(tasks) {
        if (!Array.isArray(tasks)) return;

        const lists = {
            backlog: document.getElementById('list-backlog'),
            doing: document.getElementById('list-doing'),
            done: document.getElementById('list-done'),
            failed: document.getElementById('list-failed')
        };

        const counts = {
            backlog: document.getElementById('count-backlog'),
            doing: document.getElementById('count-doing'),
            done: document.getElementById('count-done'),
            failed: document.getElementById('count-failed')
        };

        // Clear lists (deduplicate since failed no longer points to done)
        const seen = new Set();
        Object.values(lists).forEach(l => { if (l && !seen.has(l)) { l.innerHTML = ''; seen.add(l); } });

        const stats = { backlog: 0, doing: 0, done: 0, failed: 0 };

        tasks.forEach(task => {
            const status = task.status in stats ? task.status : 'backlog';
            stats[status]++;
            const list = lists[status];
            if (list) list.appendChild(this.createTaskCard(task));
        });

        // Update counts
        Object.keys(stats).forEach(k => {
            if (counts[k]) counts[k].innerText = stats[k];
        });
    },

    createTaskCard(task) {
        const div = document.createElement('div');
        div.className = 'bg-white p-5 rounded-2xl border border-slate-100 shadow-sm hover:shadow-md transition-all group relative animate-in fade-in slide-in-from-bottom-2 duration-300 cursor-pointer';
        div.onclick = (e) => {
            if (e.target.closest('button')) return;
            this.openDetails(task);
        };

        const cronTag = task.cron ? `
            <div class="mt-3 flex items-center gap-1.5 text-[10px] font-bold text-blue-500 bg-blue-50 px-2 py-0.5 rounded-full w-fit">
                <i class="fas fa-clock"></i> ${task.cron}
            </div>
        ` : '';

        const callbackTag = task.channel ? `
            <div class="mt-1 flex items-center gap-1.5 text-[10px] font-bold text-purple-500 bg-purple-50 px-2 py-0.5 rounded-full w-fit">
                <i class="fas fa-reply"></i> ${task.channel.toUpperCase()}: ${task.recipient || 'N/A'}
            </div>
        ` : '';

        div.innerHTML = `
            <div class="flex justify-between items-start mb-2">
                <h4 class="font-bold text-slate-800 text-sm leading-tight">${task.title}</h4>
                <div class="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    ${task.status === 'backlog' ? `
                        <button onclick="Tasks.editTask('${task.id}')" class="text-slate-300 hover:text-blue-500 p-1">
                            <i class="fas fa-edit text-xs"></i>
                        </button>
                    ` : ''}
                    <button onclick="Tasks.deleteTask('${task.id}')" class="text-slate-300 hover:text-red-500 p-1">
                        <i class="fas fa-trash-alt text-xs"></i>
                    </button>
                </div>
            </div>
            <p class="text-slate-500 text-xs line-clamp-3 mb-3">${task.spec}</p>
            <div class="flex flex-wrap gap-2">
                ${cronTag}
                ${callbackTag}
            </div>
            
            <div class="mt-4 flex items-center justify-between pt-4 border-t border-slate-50">
                <span class="text-[10px] text-slate-400 font-medium">
                    ${task.last_run ? 'Last run: ' + new Date(task.last_run).toLocaleTimeString() : 'Never run'}
                </span>
                <div class="flex items-center gap-2">
                    ${task.status === 'doing' ? `
                        <div title="Doing" class="w-8 h-8 rounded-full bg-blue-100 text-blue-600 flex items-center justify-center animate-spin">
                            <i class="fas fa-spinner text-[10px]"></i>
                        </div>
                    ` : ''}
                    ${task.status === 'done' ? `
                        <div title="Done" class="w-8 h-8 rounded-full bg-green-100 text-green-600 flex items-center justify-center">
                            <i class="fas fa-check text-[10px]"></i>
                        </div>
                    ` : ''}
                    ${task.status === 'failed' ? `
                        <div title="${task.result || 'Error'}" class="w-8 h-8 rounded-full bg-red-100 text-red-600 flex items-center justify-center cursor-help">
                            <i class="fas fa-exclamation-triangle text-[10px]"></i>
                        </div>
                    ` : ''}
                    
                    ${(task.status === 'done' || task.status === 'failed') ? `
                        <button onclick="Tasks.moveToBacklog('${task.id}')" title="Move to Backlog" class="w-8 h-8 rounded-full bg-slate-100 text-slate-600 flex items-center justify-center hover:bg-slate-200 transition-all active:scale-90">
                            <i class="fas fa-undo text-[10px]"></i>
                        </button>
                    ` : ''}
                    ${(task.status !== 'doing') ? `
                        <button onclick="Tasks.playTask('${task.id}')" title="${task.status === 'backlog' ? 'Play' : 'Rerun'}" class="w-8 h-8 rounded-full bg-blue-600 text-white flex items-center justify-center hover:bg-blue-700 transition-all active:scale-90 shadow-lg shadow-blue-100">
                            <i class="fas ${task.status === 'backlog' ? 'fa-play' : 'fa-redo'} text-[10px] ${task.status === 'backlog' ? 'ml-0.5' : ''}"></i>
                        </button>
                    ` : ''}
                </div>
            </div>
        `;
        return div;
    },

    openModal() {
        const modal = document.getElementById('task-modal');
        if (modal) modal.classList.remove('hidden');
    },

    editTask(taskId) {
        const task = this.currentTasks.get(taskId);
        if (!task) return;

        const form = document.getElementById('task-form');
        if (!form) return;
        
        form.elements['task_id'].value = task.id;
        form.elements['title'].value = task.title;
        form.elements['spec'].value = task.spec;
        form.elements['cron'].value = task.cron || '';
        form.elements['channel'].value = task.channel || '';
        form.elements['recipient'].value = task.recipient || '';
        
        this.openModal();
    },

    closeModal() {
        const modal = document.getElementById('task-modal');
        if (modal) modal.classList.add('hidden');
        const form = document.getElementById('task-form');
        if (form) {
            form.reset();
            form.elements['task_id'].value = '';
        }
    },

    async openDetails(task) {
        document.getElementById('detail-title').innerText = task.title;
        document.getElementById('detail-id').innerText = `TASK_ID: ${task.id}`;
        document.getElementById('detail-spec').innerText = task.spec;
        document.getElementById('detail-cron').innerText = task.cron || 'Manual Execution';
        document.getElementById('detail-channel').innerText = task.channel ? `${task.channel.toUpperCase()} (${task.recipient})` : 'No Callback';
        
        const logsContainer = document.getElementById('detail-logs');
        logsContainer.innerHTML = '<div class="text-center p-10 opacity-30"><i class="fas fa-spinner animate-spin"></i></div>';
        
        document.getElementById('task-details-modal').classList.remove('hidden');
        
        try {
            const logs = await API.fetch(`/api/tasks/${task.id}/logs`);
            this.renderLogs(logs);
        } catch (err) {
            logsContainer.innerHTML = '<p class="text-center text-red-400 text-xs py-10">Failed to load logs</p>';
        }
    },

    renderLogs(logs) {
        const container = document.getElementById('detail-logs');
        if (!logs || logs.length === 0) {
            container.innerHTML = '<div class="text-center py-10 bg-slate-50 rounded-xl text-slate-400 text-xs font-bold">NO HISTORY RECORDED</div>';
            return;
        }

        container.innerHTML = logs.map(log => `
            <div class="bg-white border border-slate-100 rounded-xl p-4 shadow-sm">
                <div class="flex justify-between items-center mb-2">
                    <span class="text-[10px] font-black ${log.status === 'done' ? 'text-green-500' : 'text-red-500'} uppercase tracking-widest">
                        ${log.status}
                    </span>
                    <span class="text-[10px] text-slate-400 font-bold">
                        ${new Date(log.created_at).toLocaleString()}
                    </span>
                </div>
                <div class="bg-slate-50 p-3 rounded-lg text-[11px] text-slate-600 font-mono whitespace-pre-wrap leading-relaxed max-h-40 overflow-y-auto">
                    ${log.output || '(No Output)'}
                </div>
            </div>
        `).join('');
    },

    closeDetails() {
        document.getElementById('task-details-modal').classList.add('hidden');
    },

    async deleteTask(id) {
        if (confirm('Are you sure you want to delete this task?')) {
            try {
                await API.fetch(`/api/tasks/${id}`, { method: 'DELETE' });
                this.loadTasks();
            } catch (err) {
                console.error('Delete failed', err);
            }
        }
    },

    async playTask(id) {
        try {
            await API.fetch(`/api/tasks/${id}/play`, { method: 'POST' });
            this.loadTasks();
        } catch (err) {
            console.error('Execution failed', err);
        }
    },

    async moveToBacklog(id) {
        try {
            await API.fetch(`/api/tasks/${id}`, {
                method: 'PUT',
                body: JSON.stringify({ status: 'backlog' })
            });
            this.loadTasks();
        } catch (err) {
            console.error('Failed to move to backlog', err);
        }
    }
};
