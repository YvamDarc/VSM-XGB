import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from copy import deepcopy
import numpy as np
import pandas as pd
import pytest
from data import aggregate,account_match,prepare_external,merge_external,records,frame,Pennylane,demo
from modeling import DEFAULT_FEATURES,DEFAULT_PARAMS,fit_model,forecast,folds,score_tables,train_pipeline,evaluate_fold
from storage import new_project,dump_project,load_project,CloudStore


def dataset(n=150):
    idx=pd.date_range('2024-01-01',periods=n)
    return pd.DataFrame({'date':idx,'y':np.where(idx.dayofweek==6,0,100+idx.dayofweek*10).astype(float)})


def test_ledger_zero_negative_and_cents():
    lines=pd.DataFrame({'date':['2024-01-01','2024-01-01','2024-01-03'],'credit':['0.10','0.20','0'],'debit':['0','0','12.34'],'account':['707']*3,'account_label':['Ventes']*3})
    d,c=aggregate(lines,'2024-01-01','2024-01-04')
    assert d.y.tolist()==[.30,0.,-12.34,0.]
    assert c.CAHT.sum()==pytest.approx(-12.04)
    assert account_match('707ABC','70',exclude='706')
    assert not account_match('709000','70',exclude='709')
    assert account_match('707200',ranges='707000:707999')


def test_external_duplicates_and_missing():
    df=pd.DataFrame({'jour':['01/01/2024','01/01/2024'],'effectif':['1,5','2,5']})
    f=prepare_external(df,'jour',['effectif'],'sum','planning',{'effectif':'Nombre'})
    assert f['planning__effectif'].tolist()==[4.]
    daily=dataset(30)
    merged=merge_external(daily,[{'data':records(f)}])
    assert len(merged)==30
    with pytest.raises(ValueError,match='incomplète'):
        fit_model(merged,DEFAULT_FEATURES,DEFAULT_PARAMS,['planning__effectif'])


def test_calendar_holidays_future_and_schema():
    from modeling import base_features
    cfg=deepcopy(DEFAULT_FEATURES); cfg['calendar']=['weekday','holiday','before_holiday','after_holiday']
    df=pd.DataFrame({'date':pd.to_datetime(['2027-07-13','2027-07-14','2027-07-15'])})
    x=base_features(df,cfg,[])
    assert x.holiday.tolist()==[0,1,0]
    assert x.before_holiday.tolist()==[1,0,0]
    assert x.after_holiday.tolist()==[0,0,1]


def test_cumulative_metrics_really_use_cumulative_values():
    df=pd.DataFrame({'date':pd.date_range('2024-01-01',periods=2),'real':[100.,100.],'pred':[110.,90.],'baseline':[100.,100.],'fold':['1','1']})
    scores,totals,traj=score_tables(df,'Mois',7)
    score=scores[scores.Modèle=='XGBoost'].set_index('Lecture')
    assert score.loc['Journalier','MAE']==10.
    assert score.loc['Total de période','MAE']==0.
    assert score.loc['Cumul progressif','MAE']==5.
    assert not totals['complète'].iloc[0]
    assert traj.pred_cum.tolist()==[110.,200.]
    # Remise à zéro au mois suivant et jamais mélange entre origines.
    extra=df.copy(); extra.date+=pd.Timedelta(days=31); extra.fold='2'
    _,_,traj=score_tables(pd.concat([df,extra]),'Mois',7)
    assert traj.pred_cum.tolist()==[110.,200.,110.,200.]


def test_protocol_fields_and_holdout():
    assert folds(100,40,20,10,5)==[(0,40,45,55),(0,60,65,75),(0,80,85,95)]
    assert folds(100,40,20,10,5,'Glissante')[1]==(20,60,65,75)
    params={**DEFAULT_PARAMS,'n_estimators':10}
    protocol={'initial':40,'step':30,'horizon':10,'gap':3,'window':'Croissante','holdout':20,'period':'Mois','block':7,'objective':'Cumul progressif','metric':'MAE'}
    model,cv,test,trials=train_pipeline(dataset(),DEFAULT_FEATURES,[],protocol,params,1)
    assert cv.date.max()<test.date.min()
    assert len(test)==20
    assert model['train_end']=='2024-05-29'
    assert test.origin.max()<test.date.min()-pd.Timedelta(days=3)


def test_recursive_forecast_cannot_read_actual_future():
    df=dataset(); cfg=deepcopy(DEFAULT_FEATURES); cfg['lags']=[1,7];cfg['rolling']=[7]
    params={**DEFAULT_PARAMS,'n_estimators':8}
    split=(0,90,95,120)
    first=evaluate_fold(df,split,cfg,params,[],1)
    changed=df.copy(); changed.loc[90:,'y']=999999.
    second=evaluate_fold(changed,split,cfg,params,[],1)
    np.testing.assert_array_equal(first.pred,second.pred)
    assert not np.array_equal(first.real,second.real)


def test_missing_prediction_exog_blocks():
    df=dataset(80);df['planning__hours']=8.
    b=fit_model(df,DEFAULT_FEATURES,{**DEFAULT_PARAMS,'n_estimators':8},['planning__hours'])
    with pytest.raises(ValueError,match='absente'):
        forecast(b,pd.DataFrame({'date':pd.date_range(df.date.max()+pd.Timedelta(days=1),periods=3)}))


def test_project_roundtrip_predictions_and_notes():
    df=dataset(80);cfg=deepcopy(DEFAULT_FEATURES);cfg['lags']=[7]
    b=fit_model(df,cfg,{**DEFAULT_PARAMS,'n_estimators':8},[])
    future=pd.DataFrame({'date':pd.date_range(df.date.max()+pd.Timedelta(days=1),periods=15)})
    p=new_project();p['daily']=records(df);p['models'][b['id']]=b;p['active_model']=b['id'];p['notes']=[{'comment':'Événement local'}]
    restored=load_project(dump_project(p))
    np.testing.assert_array_equal(forecast(b,future),forecast(restored['models'][b['id']],future))
    assert restored['notes']==p['notes']


class Response:
    status_code=200
    headers={}
    def __init__(self,body): self.body=body
    def json(self): return self.body

class Session:
    def __init__(self):self.headers={};self.calls=[]
    def get(self,url,params,timeout):
        self.calls.append((url,params.copy()))
        if url.endswith('ledger_accounts'):return Response({'items':[{'id':3,'number':'707','label':'Ventes'}],'has_more':False})
        if url.endswith('journals'):return Response({'items':[{'id':5,'code':'VE'}],'has_more':False})
        i=2 if params.get('cursor') else 1
        return Response({'items':[{'id':i,'date':'2024-01-01','ledger_account':{'id':3,'number':'707'},'journal':{'id':5},'credit':'100','debit':'0'}],'has_more':i==1,'next_cursor':'next' if i==1 else None})


def test_api_schema_pagination_filters_and_replacement():
    s=Session();c=Pennylane('not-a-token',session=s)
    df=c.snapshot('2024-01-01','2024-01-02')
    assert len(df)==2 and df.account.tolist()==['707','707']
    assert df.journal.tolist()==['VE','VE']
    assert s.calls[-1][1]['cursor']=='next'
    assert '2024-01-01' in s.calls[-1][1]['filter']


def test_cloud_conflict_does_not_overwrite(monkeypatch):
    monkeypatch.setattr('storage.requests.patch',lambda *a,**k:Response([]))
    with pytest.raises(ValueError,match='autre session'):
        CloudStore('https://test.supabase.co','dummy','dossier').save(new_project(),2)
