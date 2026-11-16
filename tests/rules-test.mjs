// Run with: npm install @electric-sql/pglite && node tests/rules-test.mjs
// Runs supabase/setup.sql in a local Postgres with stand-ins for the parts
// Supabase provides (roles, auth.users, auth.uid(), storage tables), then
// checks who can do what.
import { PGlite } from '@electric-sql/pglite';
import { readFileSync } from 'node:fs';

const db = new PGlite();
const setup = readFileSync(new URL('../supabase/setup.sql', import.meta.url), 'utf8');
const OWNER = '11111111-1111-1111-1111-111111111111';
const STRANGER = '22222222-2222-2222-2222-222222222222';

await db.exec(`
  create role anon nologin; create role authenticated nologin;
  create schema auth; create schema storage;
  create table auth.users (id uuid primary key, email text, created_at timestamptz default now());
  create function auth.uid() returns uuid language sql stable as
    $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
  grant usage on schema auth, storage, public to anon, authenticated;
  grant execute on function auth.uid() to anon, authenticated;
  create table storage.buckets (id text primary key, name text, public boolean, file_size_limit bigint, allowed_mime_types text[]);
  create table storage.objects (id uuid primary key default gen_random_uuid(), bucket_id text, name text);
  alter table storage.objects enable row level security;
  grant all on storage.objects to anon, authenticated;
  insert into auth.users (id, email) values ('${OWNER}', 'owner@example.test');
`);

await db.exec(setup);
await db.exec(setup); // second run must not fail
// a stranger signs up after setup (as if sign-ups were left switched on)
await db.exec(`insert into auth.users (id, email) values ('${STRANGER}', 'stranger@example.test')`);
await db.exec(`insert into public.pieces (id, title, status) values
  ('aaaaaaaa-0000-0000-0000-000000000001', 'Draft piece', 'draft'),
  ('aaaaaaaa-0000-0000-0000-000000000002', 'For sale piece', 'for_sale'),
  ('aaaaaaaa-0000-0000-0000-000000000003', 'Sold piece', 'sold')`);

let pass = 0, fail = 0;
async function as(role, uid, sql) {
  await db.exec(`reset role; select set_config('request.jwt.claim.sub', '${uid ?? ''}', false); set role ${role};`);
  try { const r = await db.query(sql); return { ok: true, rows: r.rows, n: r.affectedRows }; }
  catch (e) { return { ok: false, err: e.message }; }
  finally { await db.exec('reset role'); }
}
function check(name, cond, detail = '') { cond ? pass++ : fail++; console.log((cond ? 'PASS  ' : 'FAIL  ') + name + (cond ? '' : '  -> ' + detail)); }

let r;
r = await db.query('select count(*)::int as n from public.admins');
check('setup put the single login on the admin list', r.rows[0].n === 1, JSON.stringify(r.rows));

// visitor (not logged in)
r = await as('anon', null, 'select title from public.pieces order by title');
check('visitor sees the 2 published pieces and not the draft', r.ok && r.rows.length === 2 && !r.rows.some(x => x.title === 'Draft piece'), JSON.stringify(r));
r = await as('anon', null, `insert into public.pieces (title) values ('hack')`);
check('visitor cannot add a piece', !r.ok, JSON.stringify(r));
r = await as('anon', null, `update public.pieces set price_pence = 1`);
check('visitor cannot change a piece', !r.ok || r.n === 0, JSON.stringify(r));
r = await as('anon', null, `delete from public.pieces`);
check('visitor cannot delete a piece', !r.ok || r.n === 0, JSON.stringify(r));
r = await as('anon', null, `select * from public.admins`);
check('visitor cannot read the admin list', !r.ok || r.rows.length === 0, JSON.stringify(r));
r = await as('anon', null, `insert into storage.objects (bucket_id, name) values ('media', 'x.jpg')`);
check('visitor cannot upload a file', !r.ok, JSON.stringify(r));

// a logged-in stranger who is not on the admin list
r = await as('authenticated', STRANGER, 'select public.is_admin() as a');
check('stranger login is not an admin', r.ok && r.rows[0].a === false, JSON.stringify(r));
r = await as('authenticated', STRANGER, 'select title from public.pieces');
check('stranger login sees only published pieces', r.ok && r.rows.length === 2, JSON.stringify(r));
r = await as('authenticated', STRANGER, `insert into public.pieces (title) values ('hack')`);
check('stranger login cannot add a piece', !r.ok, JSON.stringify(r));
r = await as('authenticated', STRANGER, `update public.pieces set price_pence = 1`);
check('stranger login cannot change a piece', r.ok && r.n === 0, JSON.stringify(r));
r = await as('authenticated', STRANGER, `delete from public.pieces`);
check('stranger login cannot delete a piece', r.ok && r.n === 0, JSON.stringify(r));
r = await as('authenticated', STRANGER, `insert into public.admins (user_id) values ('${STRANGER}')`);
check('stranger login cannot add themselves to the admin list', !r.ok, JSON.stringify(r));
r = await as('authenticated', STRANGER, `insert into storage.objects (bucket_id, name) values ('media', 'x.jpg')`);
check('stranger login cannot upload a file', !r.ok, JSON.stringify(r));

// the owner
r = await as('authenticated', OWNER, 'select public.is_admin() as a');
check('owner login is an admin', r.ok && r.rows[0].a === true, JSON.stringify(r));
r = await as('authenticated', OWNER, 'select title from public.pieces');
check('owner sees all 3 pieces including the draft', r.ok && r.rows.length === 3, JSON.stringify(r));
r = await as('authenticated', OWNER, `insert into public.pieces (title, price_pence, status) values ('New dagger', 4500, 'for_sale') returning id`);
check('owner can add a piece', r.ok && r.rows.length === 1, JSON.stringify(r));
r = await as('authenticated', OWNER, `update public.pieces set status = 'reserved' where title = 'New dagger' returning updated_at > created_at as touched`);
check('owner can change a status', r.ok && r.rows.length === 1, JSON.stringify(r));
r = await as('authenticated', OWNER, `update public.pieces set status = 'on fire' where title = 'New dagger'`);
check('a status outside the list is refused', !r.ok, JSON.stringify(r));
r = await as('authenticated', OWNER, `insert into public.pieces (title, price_pence) values ('Bad price', -5)`);
check('a negative price is refused', !r.ok, JSON.stringify(r));
r = await as('authenticated', OWNER, `insert into storage.objects (bucket_id, name) values ('media', 'a/b.jpg') returning id`);
check('owner can upload a file to the media bucket', r.ok && r.rows.length === 1, JSON.stringify(r));
r = await as('authenticated', OWNER, `insert into storage.objects (bucket_id, name) values ('other', 'a/b.jpg')`);
check('owner cannot upload outside the media bucket', !r.ok, JSON.stringify(r));
r = await as('authenticated', OWNER, `delete from storage.objects where bucket_id = 'media'`);
check('owner can delete a file', r.ok && r.n === 1, JSON.stringify(r));
r = await as('authenticated', OWNER, `delete from public.pieces where title = 'New dagger'`);
check('owner can delete a piece', r.ok && r.n === 1, JSON.stringify(r));
r = await db.query(`select public, file_size_limit from storage.buckets where id = 'media'`);
check('media bucket is public with a 50 MB file limit', r.rows.length === 1 && r.rows[0].public === true && Number(r.rows[0].file_size_limit) === 52428800, JSON.stringify(r.rows));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
