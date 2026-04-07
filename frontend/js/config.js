const Config = {
    TABLE_SCHEMAS: {
        events: { cols: ['Author', 'Content', 'Timestamp'], keys: ['author', 'content', 'timestamp'] },
        user_states: { cols: ['Agent', 'User ID', 'Memory State', 'Last Sync'], keys: ['app_name', 'user_id', 'state', 'update_time'] },
        identities: { cols: ['Session', 'Hashed Alias', 'Real ID', 'Indexed'], keys: ['session_id', 'hashed_id', 'real_id', 'created_at'] },
        profiles: { cols: ['Name', 'Title', 'Company', 'Email', 'Phone', 'E-ID'], keys: ['chinese_name', 'job_title', 'company_name', 'personal_email', 'mobile_phone', 'employee_id'] },
        contacts: { cols: ['Name', 'Phone', 'Company'], keys: ['name', 'phone', 'company'] },
        reminders: { cols: ['Schedule', 'Channel', 'Status', 'Message'], keys: ['run_at', 'channel', 'status', 'prompt'] },
        tasks: { cols: ['Filename', 'Status', 'Created'], keys: ['filename', 'status', 'created_at'] }
    }
};
