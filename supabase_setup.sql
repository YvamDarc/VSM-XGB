-- À exécuter une fois dans SQL Editor de votre projet Supabase.
create table if not exists public.forecast_projects (
  project_id text primary key,
  payload text not null,
  version bigint not null default 1,
  updated_at timestamptz not null default now()
);
alter table public.forecast_projects enable row level security;
revoke all on public.forecast_projects from anon, authenticated;
grant all on public.forecast_projects to service_role;
-- Accès depuis le serveur Streamlit uniquement, avec la clé service_role secrète.
-- Les mises à jour de l'application filtrent sur version pour empêcher l'écrasement concurrent.
