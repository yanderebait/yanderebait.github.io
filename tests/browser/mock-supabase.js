// Stand-in for the Supabase library, used only by the tests.
// Every call is forwarded to the test's own back end (window.__backend).
window.supabase = {
  createClient() {
    const call = async (op, args) =>
      JSON.parse(await window.__backend(JSON.stringify({ op, args, token: localStorage.getItem('mock-token') })));
    const toB64 = (blob) => new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
      reader.readAsDataURL(blob);
    });
    class Query {
      constructor(table) { this.q = { table, filters: [], action: 'select' }; }
      select(cols) { if (this.q.action === 'select') this.q.cols = cols; else this.q.returning = true; return this; }
      insert(row) { this.q.action = 'insert'; this.q.row = row; return this; }
      update(row) { this.q.action = 'update'; this.q.row = row; return this; }
      delete() { this.q.action = 'delete'; return this; }
      eq(c, v) { this.q.filters.push(['eq', c, v]); return this; }
      neq(c, v) { this.q.filters.push(['neq', c, v]); return this; }
      order(c, o) { this.q.order = [c, !!(o && o.ascending)]; return this; }
      single() { this.q.single = true; return this; }
      then(resolve, reject) { return call('query', this.q).then(resolve, reject); }
    }
    return {
      from: (table) => new Query(table),
      rpc: (name) => call('rpc', { name }),
      auth: {
        getSession: async () => {
          const t = localStorage.getItem('mock-token');
          return { data: { session: t ? { access_token: t } : null }, error: null };
        },
        signInWithPassword: async (creds) => {
          const r = await call('signin', creds);
          if (r.token) localStorage.setItem('mock-token', r.token);
          return { data: {}, error: r.error || null };
        },
        signOut: async () => { localStorage.removeItem('mock-token'); return { error: null }; }
      },
      storage: {
        from: (bucket) => ({
          upload: async (path, blob, opts) => call('upload', { bucket, path, b64: await toB64(blob), type: opts && opts.contentType, size: blob.size }),
          remove: async (paths) => call('remove', { bucket, paths })
        })
      }
    };
  }
};
