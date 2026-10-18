"""Prévisions récursives et validation à origine glissante sans accès au futur du CA."""
from copy import deepcopy
import math
import hashlib
import json
import uuid
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import holidays
from xgboost import XGBRegressor
from data import records, frame

CALENDAR = {'weekday':'Jour de semaine', 'day':'Jour du mois', 'month':'Mois', 'year':'Année',
 'dayofyear':'Jour dans l’année', 'week':'Semaine ISO', 'quarter':'Trimestre', 'weekend':'Week-end',
 'holiday':'Férié', 'before_holiday':'Veille de férié', 'after_holiday':'Lendemain de férié'}
DEFAULT_FEATURES = {'calendar':['weekday','month','day','week','holiday'], 'lags':[], 'rolling':[], 'country':'FR', 'subdiv':None}
DEFAULT_PARAMS = {'n_estimators':180, 'max_depth':3, 'learning_rate':0.05, 'subsample':0.9, 'colsample_bytree':0.9, 'min_child_weight':3, 'reg_lambda':2.0}


def stamp():
    return datetime.now(timezone.utc).isoformat()


def base_features(df, cfg, exog):
    d = pd.DatetimeIndex(df['date'])
    out = pd.DataFrame(index=range(len(df)))
    attributes = {'weekday':d.dayofweek,'day':d.day,'month':d.month,'year':d.year,'dayofyear':d.dayofyear,
        'week':d.isocalendar().week.to_numpy(dtype=int),'quarter':d.quarter,'weekend':(d.dayofweek>=5).astype(int)}
    selected = cfg['calendar']
    if any(c in selected for c in ('holiday','before_holiday','after_holiday')):
        hs = holidays.country_holidays(cfg.get('country','FR'), subdiv=cfg.get('subdiv') or None,
            years=range(d.min().year-1,d.max().year+2))
        for c,offset in [('holiday',0),('before_holiday',1),('after_holiday',-1)]:
            attributes[c] = [int((t+pd.Timedelta(days=offset)).date() in hs) for t in d]
    for c in selected:
        out[c] = attributes[c]
    for c in exog:
        if c not in df or df[c].isna().any():
            raise ValueError(f'Variable complémentaire absente ou incomplète : {c}. Importer les valeurs de toutes les dates nécessaires.')
        out[c] = df[c].to_numpy()
    return out


def encode_fit(X):
    schema = {'numeric':[], 'categories':{}, 'columns':[]}
    for c in X:
        if pd.api.types.is_numeric_dtype(X[c]): schema['numeric'].append(c)
        else: schema['categories'][c] = sorted(X[c].astype(str).unique().tolist())
    encoded = encode(X,schema,fit=True)
    schema['columns'] = encoded.columns.tolist()
    return encoded, schema


def encode(X,schema,fit=False):
    out = pd.DataFrame(index=X.index)
    for c in schema['numeric']:
        out[c] = pd.to_numeric(X[c], errors='raise').astype(float)
    for c,cats in schema['categories'].items():
        vals = X[c].astype(str)
        unknown = set(vals)-set(cats)
        if unknown: raise ValueError(f'Catégories inédites dans {c} : {sorted(unknown)}. Adapter les données ou réentraîner.')
        for i,v in enumerate(cats): out[f'{c}__cat_{i}'] = (vals==v).astype(float)
    if not fit: out = out.reindex(columns=schema['columns'])
    if out.shape[1]==0: raise ValueError('Choisir au moins une variable de calendrier, historique ou importée.')
    if not np.isfinite(out.to_numpy()).all(): raise ValueError('Variables non finies après préparation.')
    # XGBoost refuse certains caractères dans les noms : positions stables + schéma conservé.
    return out


def history_depth(cfg):
    return max([0]+cfg['lags']+cfg['rolling'])


def fit_model(df, cfg, params, exog):
    df = df.sort_values('date').reset_index(drop=True)
    if len(df)<max(20,history_depth(cfg)+10): raise ValueError('Historique trop court pour les variables choisies.')
    expected = pd.date_range(df.date.min(),df.date.max())
    if len(expected)!=len(df) or not np.array_equal(expected.values,df.date.values):
        raise ValueError('Le calendrier d’apprentissage doit être quotidien et sans doublon.')
    X = base_features(df,cfg,exog)
    for lag in cfg['lags']: X[f'lag_{lag}'] = df.y.shift(lag)
    for window in cfg['rolling']: X[f'mean_{window}'] = df.y.shift(1).rolling(window).mean()
    warmup = history_depth(cfg)
    X = X.iloc[warmup:].reset_index(drop=True)
    target = df.y.iloc[warmup:]
    X, schema = encode_fit(X)
    model = XGBRegressor(**params, objective='reg:squarederror', tree_method='hist', random_state=42, n_jobs=2)
    model.fit(X.to_numpy(),target.to_numpy())
    return {'model':model,'schema':schema,'cfg':deepcopy(cfg),'params':deepcopy(params),'exog':exog[:],
        'history':records(df[['date','y']]), 'train_start':str(df.date.min().date()), 'train_end':str(df.date.max().date()),
        'id':uuid.uuid4().hex[:12], 'created_at':stamp(),
        'fingerprint':hashlib.sha256(df.to_json(date_format='iso').encode()).hexdigest()}


def forecast(bundle, future):
    future = future.sort_values('date').reset_index(drop=True)
    if future.empty: raise ValueError('Aucune date à prédire.')
    origin = pd.Timestamp(bundle['train_end'])
    if future.date.min() <= origin:
        raise ValueError('Choisir des dates après la fin d’apprentissage du modèle sélectionné.')
    cfg = bundle['cfg']
    X = base_features(future,cfg,bundle['exog'])
    if not history_depth(cfg):
        predictions = bundle['model'].predict(encode(X,bundle['schema']).to_numpy())
    else:
        expected = pd.date_range(origin+pd.Timedelta(days=1),future.date.max())
        if not np.array_equal(expected.values,future.date.values):
            raise ValueError('Avec les retards, fournir chaque jour depuis la fin d’apprentissage, y compris avant le début affiché.')
        history = frame(bundle['history']).set_index('date').y.to_dict()
        predictions = []
        for i,d in enumerate(future.date):
            row = X.iloc[[i]].copy()
            for lag in cfg['lags']: row[f'lag_{lag}'] = history[d-pd.Timedelta(days=lag)]
            for w in cfg['rolling']: row[f'mean_{w}'] = np.mean([history[d-pd.Timedelta(days=k)] for k in range(1,w+1)])
            p = float(bundle['model'].predict(encode(row,bundle['schema']).to_numpy())[0])
            predictions.append(p); history[d] = p
    return np.asarray(predictions,dtype=float)


def naive(train, dates):
    """Moyenne des 4 derniers mêmes jours de semaine, connue à l'origine."""
    df = train.copy(); df['dow'] = df.date.dt.dayofweek
    levels = df.groupby('dow').tail(4).groupby('dow').y.mean().to_dict()
    return np.array([levels.get(pd.Timestamp(d).dayofweek,float(df.y.mean())) for d in dates])


def metrics(real,pred):
    a,b = np.asarray(real,float),np.asarray(pred,float)
    e = a-b; den=np.abs(a)+np.abs(b)
    return {'MAE':float(np.abs(e).mean()),'RMSE':float(np.sqrt(np.mean(e**2))), 'Biais réel−prévu':float(e.mean()),
        'SMAPE %':float(100*np.divide(2*np.abs(e),den,out=np.zeros_like(e),where=den>0).mean()),
        'WAPE %':float(100*np.abs(e).sum()/np.abs(a).sum()) if np.abs(a).sum()>0 else None}


def period_keys(dates,period,block=7):
    d = pd.Series(pd.to_datetime(dates)).reset_index(drop=True)
    if period=='Mois': return d.dt.to_period('M').astype(str)
    if period=='Semaine': return d.dt.to_period('W-SUN').astype(str)
    if period=='Trimestre': return d.dt.to_period('Q').astype(str)
    return ((d-d.min()).dt.days//block).astype(str)


def summaries(predictions,period='Mois',block=7):
    """Chaque origine indépendante ; ne jamais sommer des prévisions de folds différents."""
    trajectories=[]; totals=[]
    for fold,f in predictions.groupby('fold',sort=False):
        f=f.sort_values('date').reset_index(drop=True).copy()
        f['period']=period_keys(f.date,period,block)
        for key,g in f.groupby('period',sort=False):
            g=g.copy(); g['real_cum']=g.real.cumsum(); g['pred_cum']=g.pred.cumsum(); g['baseline_cum']=g.baseline.cumsum()
            trajectories.append(g)
            if period in ('Mois','Semaine','Trimestre'):
                unit={'Mois':'M','Semaine':'W-SUN','Trimestre':'Q'}[period]
                p=g.date.iloc[0].to_period(unit)
                complete=g.date.min()==p.start_time.normalize() and g.date.max()==p.end_time.normalize() and len(g)==(p.end_time.normalize()-p.start_time.normalize()).days+1
            else: complete=len(g)==block
            totals.append({'fold':fold,'period':key,'date':g.date.max(),'real':g.real.sum(),'pred':g.pred.sum(),'baseline':g.baseline.sum(),'jours':len(g),'complète':bool(complete)})
    return pd.DataFrame(totals),pd.concat(trajectories,ignore_index=True)


def score_tables(predictions,period,block):
    totals,trajectory=summaries(predictions,period,block)
    rows=[]
    for name,df,y,p,b in [('Journalier',predictions,'real','pred','baseline'),('Total de période',totals,'real','pred','baseline'),('Cumul progressif',trajectory,'real_cum','pred_cum','baseline_cum')]:
        for model,col in [('XGBoost',p),('Référence simple',b)]:
            rows.append({'Lecture':name,'Modèle':model,**metrics(df[y],df[col])})
    return pd.DataFrame(rows),totals,trajectory


def folds(n,initial,step,horizon,gap=0,window='Croissante'):
    if min(initial,step,horizon)<1 or gap<0: raise ValueError('Paramètres de validation invalides.')
    result=[]
    for end in range(initial,n-gap-horizon+1,step):
        start=0 if window=='Croissante' else end-initial
        result.append((start,end,end+gap,end+gap+horizon))
    if not result: raise ValueError('Pas assez de jours : réduire historique initial, horizon, délai ou réserve finale.')
    return result


def evaluate_fold(df,split,cfg,params,exog,fold):
    start,end,test_start,test_end=split
    train=df.iloc[start:end].copy()
    bundle=fit_model(train,cfg,params,exog)
    # Tout le gap est prévu récursivement ; aucun CA réel postérieur à l'origine n'est injecté.
    future=df.iloc[end:test_end].drop(columns='y').copy()
    pred=forecast(bundle,future)[test_start-end:]
    test=df.iloc[test_start:test_end]
    return pd.DataFrame({'date':test.date.to_numpy(),'real':test.y.to_numpy(),'pred':pred,'baseline':naive(train,test.date),
        'fold':str(fold),'origin':train.date.max(),'horizon':np.arange(test_start-end+1,test_end-end+1)})


def backtest(df,cfg,params,exog,protocol,progress=None):
    splits=folds(len(df),protocol['initial'],protocol['step'],protocol['horizon'],protocol['gap'],protocol['window'])
    if len(splits)>100: raise ValueError('Plus de 100 simulations : augmenter le pas pour limiter le calcul.')
    results=[]
    for i,split in enumerate(splits):
        results.append(evaluate_fold(df,split,cfg,params,exog,i+1))
        if progress: progress((i+1)/len(splits))
    return pd.concat(results,ignore_index=True)


def objective(predictions,protocol):
    tables,_,_=score_tables(predictions,protocol['period'],protocol['block'])
    return float(tables[(tables.Lecture==protocol['objective']) & (tables.Modèle=='XGBoost')][protocol['metric']].iloc[0])


def candidates(base,budget):
    rng=np.random.default_rng(42); out=[base.copy()]; seen={json.dumps(base,sort_keys=True)}
    while len(out)<budget:
        p={'n_estimators':int(rng.choice([100,200,350])), 'max_depth':int(rng.choice([2,3,4,6])),
           'learning_rate':float(rng.choice([.025,.05,.1])), 'subsample':float(rng.choice([.8,1.])),
           'colsample_bytree':float(rng.choice([.8,1.])), 'min_child_weight':int(rng.choice([1,3,6])), 'reg_lambda':float(rng.choice([1.,3.,8.]))}
        key=json.dumps(p,sort_keys=True)
        if key not in seen: out.append(p); seen.add(key)
    return out


def train_pipeline(df,cfg,exog,protocol,base,budget=1,progress=None):
    df=df.sort_values('date').reset_index(drop=True)
    reserve=protocol['holdout']; gap=protocol['gap']
    if reserve<1 or len(df)<=reserve+gap: raise ValueError('Réserve finale trop longue.')
    # La réserve et son gap ne participent ni au choix ni au calcul de la bande.
    development=df.iloc[:len(df)-reserve-gap]
    best=None; trials=[]
    for i,p in enumerate(candidates(base,budget)):
        pred=backtest(development,cfg,p,exog,protocol)
        value=objective(pred,protocol)
        trials.append({'essai':i+1,'score':value,**p})
        if best is None or value<best[0]: best=(value,p,pred)
        if progress: progress((i+1)/(budget+1))
    _,params,cv=best
    test=evaluate_fold(df,(0,len(development),len(df)-reserve,len(df)),cfg,params,exog,'Test final')
    bands={str(level):float(np.quantile(np.abs(cv.real-cv.pred),level)) for level in [.8,.95]}
    coverage={key:float((np.abs(test.real-test.pred)<=v).mean()) for key,v in bands.items()}
    production=fit_model(df,cfg,params,exog)
    production['protocol']=deepcopy(protocol); production['bands']=bands
    production['coverage_holdout']=coverage
    if progress: progress(1.)
    return production,cv,test,pd.DataFrame(trials)
