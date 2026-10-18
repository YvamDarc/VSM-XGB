"""Acquisition et préparation : aucun accès à Streamlit ni état global."""
from io import BytesIO
from decimal import Decimal, InvalidOperation
import json
import time
import re
import pandas as pd
import numpy as np
import requests


def dates(values):
    s = pd.Series(values).astype(str).str.strip()
    if s.str.fullmatch(r'\d{8}').all():
        out = pd.to_datetime(s, format='%Y%m%d', errors='raise')
    elif s.str.match(r'^\d{4}-\d{2}-\d{2}').all():
        out = pd.to_datetime(s, errors='raise', format='mixed')
    else:
        out = pd.to_datetime(s, errors='raise', dayfirst=True, format='mixed')
    if out.isna().any():
        raise ValueError('Des dates sont vides.')
    return out.dt.normalize()


def number(values):
    s = pd.Series(values).astype(str).str.replace('\u202f', '', regex=False).str.replace('\xa0', '', regex=False).str.replace(' ', '', regex=False).str.replace('€', '', regex=False).str.replace(',', '.', regex=False)
    return pd.to_numeric(s, errors='raise')


def read_table(raw, name, sheet=0):
    if name.lower().endswith(('.xlsx', '.xlsm')):
        return pd.read_excel(BytesIO(raw), sheet_name=sheet)
    for encoding in ('utf-8-sig', 'cp1252'):
        try:
            return pd.read_csv(BytesIO(raw), sep=None, engine='python', encoding=encoding, dtype=str)
        except UnicodeDecodeError:
            continue
    raise ValueError('Encodage du CSV non reconnu.')


def records(df):
    return json.loads(df.to_json(orient='records', date_format='iso'))


def frame(rows):
    df = pd.DataFrame(rows)
    if 'date' in df:
        df['date'] = pd.to_datetime(df['date']).dt.normalize()
    return df


def tokens(text):
    return [s.strip().upper() for s in re.split('[;,\n]', text) if s.strip()]


def account_match(account, prefixes='', exact='', ranges='', exclude=''):
    a = str(account).strip().upper()
    matches = a in tokens(exact) or any(a.startswith(p) for p in tokens(prefixes))
    for interval in tokens(ranges):
        parts = interval.split(':')
        if len(parts) != 2 or not all(parts):
            raise ValueError('Plage invalide : utiliser 706000:707999, bornes incluses.')
        lo, hi = parts
        # Comparaison textuelle explicite : numéros de comptes conservés, même longueur recommandée.
        matches |= lo <= a <= hi
    return matches and not any(a.startswith(p) for p in tokens(exclude))


def select_lines(lines, cfg):
    df = lines.copy()
    mask = df['account'].map(lambda a: account_match(a, cfg.get('prefixes','70'), cfg.get('exact',''), cfg.get('ranges',''), cfg.get('exclude','')))
    mask &= ~df['journal'].astype(str).isin(cfg.get('excluded_journals', []))
    return df.loc[mask].copy()


def aggregate(lines, start, end):
    if pd.Timestamp(start) > pd.Timestamp(end):
        raise ValueError('La date de début dépasse la date de fin.')
    idx = pd.date_range(start, end, name='date')
    df = lines.copy()
    df['date'] = pd.to_datetime(df['date']).dt.normalize()
    df = df[df['date'].between(pd.Timestamp(start), pd.Timestamp(end))]
    # Cents entiers : pas de dérive des sommes de mouvements comptables.
    df['cents'] = [int((Decimal(str(c)) - Decimal(str(d))) * 100) for c,d in zip(df.credit,df.debit)]
    daily = df.groupby('date')['cents'].sum().reindex(idx, fill_value=0).div(100).rename('y').reset_index()
    df['month'] = df.date.dt.to_period('M').astype(str)
    control = df.groupby(['account','account_label','month'], dropna=False)['cents'].sum().div(100).rename('CAHT').reset_index()
    return daily, control


def prepare_external(df, date_col, columns, aggregation, name, kinds):
    if not columns:
        raise ValueError('Sélectionner au moins une variable.')
    out = pd.DataFrame({'date': dates(df[date_col]).to_numpy()})
    for col in columns:
        if kinds[col] == 'Nombre':
            vals = number(df[col])
            if not np.isfinite(vals).all():
                raise ValueError(f'Valeurs manquantes ou infinies : {col}.')
        else:
            if df[col].isna().any():
                raise ValueError(f'Valeurs manquantes : {col}.')
            vals = df[col].astype(str)
        out[f'{name}__{col}'] = vals.to_numpy()
    rules = {f'{name}__{col}': (aggregation if kinds[col] == 'Nombre' else 'last') for col in columns}
    return out.groupby('date', as_index=False).agg(rules).sort_values('date')


def merge_external(daily, sources):
    out = daily.copy()
    for source in sources:
        f = frame(source['data'])
        out = out.merge(f, on='date', how='left', validate='one_to_one')
    return out


class Pennylane:
    BASE = 'https://app.pennylane.com/api/external/v2'
    def __init__(self, token, company_id=None, session=None):
        self.session = session or requests.Session()
        self.session.headers.update({'Authorization': f'Bearer {token}', 'Accept': 'application/json'})
        if company_id:
            self.session.headers['X-Company-Id'] = str(company_id)

    def paginate(self, endpoint, filters=None, progress=None):
        params = {'limit':100, 'sort':'id'}
        if filters:
            params['filter'] = json.dumps(filters)
        items, seen = [], set()
        while True:
            for attempt in range(5):
                try:
                    r = self.session.get(f'{self.BASE}/{endpoint}', params=params, timeout=(10,60))
                except requests.RequestException:
                    if attempt == 4:
                        raise ValueError('Connexion Pennylane interrompue. Les anciennes données sont conservées.') from None
                    time.sleep(min(2**attempt, 8)); continue
                if r.status_code == 429 or r.status_code >= 500:
                    if attempt == 4:
                        raise ValueError('Pennylane temporairement indisponible ou limite API atteinte.')
                    try: delay = float(r.headers.get('Retry-After', 2**attempt))
                    except ValueError: delay = 2**attempt
                    time.sleep(min(max(delay,1),30)); continue
                if r.status_code != 200:
                    raise ValueError(f'Pennylane HTTP {r.status_code} sur {endpoint} : vérifier token, dossier et droits de lecture.')
                break
            body = r.json()
            if not isinstance(body.get('items'), list):
                raise ValueError('Réponse Pennylane inattendue (items absent).')
            items.extend(body['items'])
            if progress: progress(len(items))
            if not body.get('has_more'):
                break
            cursor = body.get('next_cursor')
            if not cursor or cursor in seen:
                raise ValueError('Pagination Pennylane incohérente ; synchronisation annulée.')
            seen.add(cursor); params['cursor'] = cursor
        return items

    def snapshot(self, start, end, progress=None):
        accounts = self.paginate('ledger_accounts')
        journals = self.paginate('journals')
        am = {a['id']:a for a in accounts}
        jm = {j['id']:j for j in journals}
        entries = self.paginate('ledger_entry_lines', [
            {'field':'date','operator':'gteq','value':str(start)},
            {'field':'date','operator':'lteq','value':str(end)}], progress)
        rows = []
        for e in entries:
            a = e['ledger_account']; j = e['journal']
            rows.append({'id':str(e['id']), 'date':e['date'], 'account':str(a.get('number') or am[a['id']]['number']),
                'account_label':am.get(a['id'],{}).get('label',''), 'journal':jm.get(j['id'],{}).get('code',str(j['id'])),
                'debit':e['debit'], 'credit':e['credit'], 'label':e.get('label','')})
        df = pd.DataFrame(rows, columns=['id','date','account','account_label','journal','debit','credit','label'])
        if df.id.duplicated().any():
            raise ValueError('IDs dupliqués reçus de Pennylane : relancer la synchronisation.')
        if len(df): df['date'] = pd.to_datetime(df['date'])
        return df


def demo():
    idx = pd.date_range('2023-01-01','2026-09-30')
    rng = np.random.default_rng(42)
    y = np.maximum(0, 1600 + idx.dayofweek.to_numpy()*130 + 350*np.sin(idx.dayofyear.to_numpy()*2*np.pi/365.25) + rng.normal(0,90,len(idx)))
    y[idx.dayofweek==6] = 0
    df = pd.DataFrame({'date':idx,'id':[str(i) for i in range(len(idx))], 'account':'707100', 'account_label':'Ventes démonstration', 'journal':'VE', 'debit':'0', 'credit':np.round(y,2).astype(str), 'label':'Données fictives'})
    return df[df.credit!='0.0'].copy()
