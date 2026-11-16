-- yanderebait shop: database setup.
-- Run this once in the Supabase SQL editor, AFTER creating your one login
-- under Authentication > Users. It is safe to run again.
--
-- What it sets up:
--   1. admins      the list of logins allowed to edit the shop
--   2. pieces      one row per thing in the shop
--   3. media       the storage bucket for photos and videos
-- The rules: anyone can read published pieces. Only an admin can change anything.

-- ---------------------------------------------------------------- 1. admins

create table if not exists public.admins (
  user_id  uuid primary key references auth.users (id) on delete cascade,
  added_at timestamptz not null default now()
);
alter table public.admins enable row level security;

-- True when the person making the request is on the admin list.
create or replace function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.admins where user_id = (select auth.uid())
  );
$$;

revoke all on function public.is_admin() from public;
grant execute on function public.is_admin() to anon, authenticated;

-- An admin may see their own entry. Nobody can add, change or remove entries
-- through the website: that is only possible from this SQL editor.
revoke all on public.admins from anon, authenticated;
grant select on public.admins to authenticated;

drop policy if exists "Admins can see their own entry" on public.admins;
create policy "Admins can see their own entry"
  on public.admins for select to authenticated
  using (user_id = (select auth.uid()));

-- ---------------------------------------------------------------- 2. pieces

create table if not exists public.pieces (
  id          uuid primary key default gen_random_uuid(),
  title       text not null check (char_length(title) between 1 and 120),
  description text not null default '' check (char_length(description) <= 5000),
  price_pence integer check (price_pence is null or price_pence between 0 and 100000000),
  status      text not null default 'draft'
              check (status in ('draft', 'coming_soon', 'for_sale', 'reserved', 'sold', 'not_for_sale')),
  media       jsonb not null default '[]'::jsonb check (jsonb_typeof(media) = 'array'),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
alter table public.pieces enable row level security;

create or replace function public.touch_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists pieces_touch_updated_at on public.pieces;
create trigger pieces_touch_updated_at
  before update on public.pieces
  for each row execute function public.touch_updated_at();

revoke all on public.pieces from anon, authenticated;
grant select on public.pieces to anon;
grant select, insert, update, delete on public.pieces to authenticated;

drop policy if exists "Anyone can see published pieces" on public.pieces;
create policy "Anyone can see published pieces"
  on public.pieces for select to anon, authenticated
  using (status <> 'draft');

drop policy if exists "Admins can see every piece" on public.pieces;
create policy "Admins can see every piece"
  on public.pieces for select to authenticated
  using ((select public.is_admin()));

drop policy if exists "Admins can add pieces" on public.pieces;
create policy "Admins can add pieces"
  on public.pieces for insert to authenticated
  with check ((select public.is_admin()));

drop policy if exists "Admins can change pieces" on public.pieces;
create policy "Admins can change pieces"
  on public.pieces for update to authenticated
  using ((select public.is_admin()))
  with check ((select public.is_admin()));

drop policy if exists "Admins can delete pieces" on public.pieces;
create policy "Admins can delete pieces"
  on public.pieces for delete to authenticated
  using ((select public.is_admin()));

-- ---------------------------------------------------------------- 3. media

-- A public bucket: anyone with a file's address can view it, which is what a
-- shop needs. 50 MB per file, photos and videos only.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'media', 'media', true, 52428800,
  array['image/jpeg', 'image/png', 'image/webp', 'video/mp4', 'video/webm', 'video/quicktime']
)
on conflict (id) do update
  set public = excluded.public,
      file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists "Shop admins can list media" on storage.objects;
create policy "Shop admins can list media"
  on storage.objects for select to authenticated
  using (bucket_id = 'media' and (select public.is_admin()));

drop policy if exists "Shop admins can upload media" on storage.objects;
create policy "Shop admins can upload media"
  on storage.objects for insert to authenticated
  with check (bucket_id = 'media' and (select public.is_admin()));

drop policy if exists "Shop admins can replace media" on storage.objects;
create policy "Shop admins can replace media"
  on storage.objects for update to authenticated
  using (bucket_id = 'media' and (select public.is_admin()))
  with check (bucket_id = 'media' and (select public.is_admin()));

drop policy if exists "Shop admins can delete media" on storage.objects;
create policy "Shop admins can delete media"
  on storage.objects for delete to authenticated
  using (bucket_id = 'media' and (select public.is_admin()));

-- ---------------------------------------------------------------- 4. you

-- If the project has exactly one login, put it on the admin list.
-- With zero logins, or more than one, this does nothing and says so.
do $$
declare
  logins integer;
begin
  select count(*) into logins from auth.users;
  if logins = 1 then
    insert into public.admins (user_id)
    select id from auth.users
    on conflict (user_id) do nothing;
    raise notice 'Done: your login is on the admin list.';
  else
    raise notice 'Found % logins, expected exactly 1. Nobody was added to the admin list.', logins;
  end if;
end;
$$;
