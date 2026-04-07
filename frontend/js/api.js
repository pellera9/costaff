const API = {
    token: () => localStorage.getItem('mate_token'),
    async fetch(url, options = {}) {
        const headers = { 
            'Authorization': `Bearer ${this.token()}`, 
            'Content-Type': 'application/json', 
            ...options.headers 
        };
        const res = await fetch(url, { ...options, headers });
        if (res.status === 401) { 
            if (typeof App !== 'undefined' && App.logout) App.logout(); 
            throw new Error('Unauthorized'); 
        }
        return res.json();
    },
    async post(url, body) {
        return this.fetch(url, {
            method: 'POST',
            body: JSON.stringify(body)
        });
    }
};
