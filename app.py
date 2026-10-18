from datetime import date,timedelta
from copy import deepcopy
import hashlib
import hmac
import json
import uuid
import pandas as pd
import numpy as np
import streamlit as st
from data import Pennylane,select_lines,aggregate,records,frame,demo,merge_external,read_table,dates,number
from modeling import CALENDAR,DEFAULT_PARAMS,history_depth,train_pipeline,forecast,score_tables,summaries,stamp
from storage import new_project,dump_project,load_project,CloudStore
from ui_helpers import import_external,excel_bytes,chart

st.set_page_config(page_title='Prévisions CA · Pennylane',page_icon='📈',layout='wide')


def secret(key,default=''):
    try: return st.secrets.get(key,default)
    except FileNotFoundError: return default


def target_signature(p):
    return hashlib.sha256(json.dumps(p['account_config'],sort_keys=True).encode()).hexdigest()


def authenticate():
    password=secret('APP_PASSWORD')
    live=bool(secret('PENNYLANE_TOKEN') or secret('SUPABASE_SECRET_KEY') or secret('SUPABASE_SERVICE_ROLE_KEY'))
    if live and not password:
        st.error('Ajouter APP_PASSWORD dans les secrets pour protéger les données du dossier.'); st.stop()
    if password and not st.session_state.get('authenticated'):
        st.title('Prévisions de chiffre d’affaires')
        with st.form('login'):
            entered=st.text_input('Mot de passe',type='password')
            go=st.form_submit_button('Ouvrir le dossier')
        if go:
            if hmac.compare_digest(entered.encode(),str(password).encode()):
                st.session_state.authenticated=True; st.rerun()
            st.error('Mot de passe incorrect.')
        st.stop()


def cloud():
    url,key=secret('SUPABASE_URL'),secret('SUPABASE_SECRET_KEY') or secret('SUPABASE_SERVICE_ROLE_KEY')
    if url and key: return CloudStore(url,key,str(secret('PROJECT_ID','mon-dossier')))
    return None


def changed(message='Modifications conservées dans la session.'):
    st.session_state.dirty=True
    backend=cloud()
    if backend and not st.session_state.get('cloud_blocked'):
        try:
            st.session_state.cloud_version=backend.save(P,st.session_state.get('cloud_version'))
            st.session_state.dirty=False
            st.success(message+' Sauvegarde distante effectuée.')
        except ValueError as e:
            st.session_state.cloud_blocked=True
            st.error(str(e))
    else: st.success(message)


def need_daily():
    if not P['daily']:
        st.info('Commencer par la page Données & comptes.'); st.stop()
    return frame(P['daily'])


def source_page():
    st.title('Données & comptes')
    st.write('Crédits − débits, cumulés par jour. Chaque journée absente vaut zéro sur toute la période récupérée.')
    source_mode=st.radio('Source',['Pennylane','Import comptable CSV / Excel','Démonstration'],horizontal=True)
    src=P['source']
    c1,c2=st.columns(2)
    begin=c1.date_input('Début de récupération',value=pd.Timestamp(src.get('start','2023-01-01')).date())
    end=c2.date_input('Comptabilité complète jusqu’au',value=pd.Timestamp(src.get('end',date.today()-timedelta(days=1))).date())
    if source_mode=='Pennylane':
        st.caption('Token lu exclusivement dans les secrets. Droits : ledger_entries:readonly, ledger_accounts:readonly, journals:readonly.')
        if st.button('Synchroniser Pennylane',type='primary'):
            if not secret('PENNYLANE_TOKEN'): raise ValueError('PENNYLANE_TOKEN absent des secrets.')
            if begin>end: raise ValueError('Dates inversées.')
            status=st.empty()
            with st.spinner('Récupération complète de la période…'):
                lines=Pennylane(secret('PENNYLANE_TOKEN'),secret('PENNYLANE_COMPANY_ID')).snapshot(begin,end,lambda n:status.caption(f'{n:,} lignes reçues'))
            install_lines(lines,'Pennylane',begin,end)
    elif source_mode=='Démonstration':
        st.info('Données fictives du 01/01/2023 au 30/09/2026. Aucun appel Pennylane.')
        if st.button('Charger la démonstration',type='primary'):
            install_lines(demo(),'Démonstration',date(2023,1,1),date(2026,9,30)); st.rerun()
    else:
        f=st.file_uploader('Grand livre / FEC / export de mouvements',type=['csv','txt','xlsx','xlsm'])
        if f:
            sheet=0
            if f.name.lower().endswith(('.xlsx','.xlsm')):
                from io import BytesIO
                sheet=st.selectbox('Onglet comptable',pd.ExcelFile(BytesIO(f.getvalue())).sheet_names)
            df=read_table(f.getvalue(),f.name,sheet)
            st.dataframe(df.head(),hide_index=True)
            mapping={}
            aliases={'date':['EcritureDate','Date','date'],'account':['CompteNum','Compte','account'],'account_label':['CompteLib','Libellé compte','account_label'],'journal':['JournalCode','Journal','journal'],'debit':['Debit','Débit','debit'],'credit':['Credit','Crédit','credit']}
            for field,possible in aliases.items():
                options=list(df.columns)
                default=next((x for x in possible if x in options),options[0])
                mapping[field]=st.selectbox(field,options,index=options.index(default),key='map_'+field)
            if st.button('Charger cet export',type='primary'):
                lines=pd.DataFrame({k:df[v].to_numpy() for k,v in mapping.items()})
                lines['date']=dates(lines.date).to_numpy()
                for c in ['debit','credit']: lines[c]=number(lines[c].fillna(0))
                for c in ['account','journal','account_label']: lines[c]=lines[c].fillna('').astype(str).str.strip()
                lines['account']=lines['account'].str.replace(r'^(\d+)\.0$',r'\1',regex=True)
                lines['id']=[str(i) for i in range(len(lines))]; lines['label']='Import'
                install_lines(lines,'Import '+f.name,begin,end)
    if not P['lines']:
        st.info('Aucun mouvement chargé.'); return
    st.divider()
    st.caption(f"Source : {P['source'].get('mode')} · période {P['source'].get('start')} → {P['source'].get('end')} · synchronisation {P['source'].get('synced_at','')}")
    lines=frame(P['lines']); cfg=P['account_config']
    with st.form('accounts'):
        left,right=st.columns(2)
        prefixes=left.text_input('Préfixes inclus (ex. 706;707)',value=cfg['prefixes'])
        exact=right.text_input('Comptes exacts supplémentaires',value=cfg['exact'])
        ranges=left.text_input('Plages inclusives (ex. 706000:707999)',value=cfg['ranges'],help='Comparaison textuelle. Utiliser des bornes de même longueur que vos comptes ou préférer les préfixes.')
        exclude=right.text_input('Préfixes exclus',value=cfg['exclude'])
        journals=sorted(lines.journal.astype(str).unique())
        excluded=st.multiselect('Journaux exclus',journals,default=[j for j in cfg['excluded_journals'] if j in journals])
        if st.form_submit_button('Appliquer les comptes et reconstruire le CA'):
            new_cfg=dict(prefixes=prefixes,exact=exact,ranges=ranges,exclude=exclude,excluded_journals=excluded)
            selected=select_lines(lines,new_cfg)
            if selected.empty: raise ValueError('Aucune ligne dans cette sélection. Les paramètres précédents sont conservés.')
            daily,control=aggregate(selected,P['source']['start'],P['source']['end'])
            P['account_config']=new_cfg; P['daily']=records(daily); P['controls']=records(control)
            changed('Sélection appliquée. Les anciens modèles restent conservés.'); st.rerun()
    if P['daily']:
        daily=frame(P['daily']); selected=select_lines(lines,P['account_config'])
        a,b,c=st.columns(3)
        a.metric('CA HT total',f'{daily.y.sum():,.2f} €'); b.metric('Jours',len(daily)); c.metric('Jours à zéro',int((daily.y==0).sum()))
        st.dataframe(selected[['account','account_label']].drop_duplicates().sort_values('account'),hide_index=True)
        st.plotly_chart(chart(daily.rename(columns={'y':'pred'}),title='CA HT récupéré'),use_container_width=True)
        st.dataframe(pd.DataFrame(P['controls']),hide_index=True)
        st.download_button('Exporter les contrôles',excel_bytes({'Journalier':daily,'Comptes et mois':pd.DataFrame(P['controls'])}),'controle_ca.xlsx')


def install_lines(lines,mode,begin,end):
    if begin>end: raise ValueError('Dates inversées.')
    old=set(str(r['id']) for r in P['lines'])
    selected=select_lines(lines,P['account_config'])
    daily,control=aggregate(selected,begin,end)
    P['lines']=records(lines)
    P['source']={'mode':mode,'start':str(begin),'end':str(end),'synced_at':stamp()}
    P['daily']=records(daily); P['controls']=records(control)
    new=set(lines.id.astype(str))
    changed(f'{len(lines)} mouvements chargés ; {len(new-old)} nouveaux IDs, {len(old-new)} anciens IDs absents du nouveau périmètre.')


def variables_page():
    st.title('Variables du modèle')
    daily=need_daily()
    st.subheader('Calendrier automatique')
    cfg=P['features']
    with st.form('features'):
        preset=st.selectbox('Sélection rapide',['Personnalisée','Essentiel','Calendrier complet','Aucun calendrier'])
        chosen=st.multiselect('Variables calendaires',list(CALENDAR),default=cfg['calendar'],format_func=lambda c:CALENDAR[c])
        country=st.selectbox('Pays des jours fériés',['FR','BE','CH','LU'],index=['FR','BE','CH','LU'].index(cfg.get('country','FR')))
        subdiv=st.text_input('Subdivision éventuelle (vide = calendrier national)',value=cfg.get('subdiv') or '',help='Code reconnu par python-holidays, par exemple Alsace-Moselle selon le pays. Laisser vide pour le cas courant.')
        lags=st.multiselect('CA des jours précédents — facultatif',[1,7,14,28,364],default=cfg['lags'])
        rolling=st.multiselect('Moyennes des jours précédents — facultatif',[7,14,28],default=cfg['rolling'])
        st.caption('En prévision, les retards futurs utilisent les prévisions précédentes. Les fériés sont des indicateurs 0/1, sans fermeture imposée.')
        if st.form_submit_button('Enregistrer les variables'):
            if preset=='Essentiel': chosen=['weekday','month','day','week','holiday']
            elif preset=='Calendrier complet': chosen=list(CALENDAR)
            elif preset=='Aucun calendrier': chosen=[]
            new_cfg={'calendar':chosen,'lags':lags,'rolling':rolling,'country':country,'subdiv':subdiv or None}
            from modeling import base_features
            base_features(daily,new_cfg,[])  # Valider le calendrier avant modification.
            P['features']=new_cfg; changed('Variables enregistrées.'); st.rerun()
    st.subheader('Fichiers complémentaires')
    st.write('Une source peut contenir plusieurs variables. Ajouter les sources une par une ; le même nom remplace la source existante.')
    for s in P['sources']:
        with st.expander(s['name']+' · '+', '.join(s['columns'])):
            st.dataframe(frame(s['data']).head(),hide_index=True)
            if st.button('Retirer cette source',key='remove_'+s['name']):
                P['sources']=[x for x in P['sources'] if x['name']!=s['name']]; changed(); st.rerun()
    with st.expander('Ajouter / remplacer un Excel ou CSV',expanded=not P['sources']):
        new=import_external('historical')
        known=st.checkbox('Ces valeurs étaient connues à l’avance aux dates de simulation (planning, calendrier, scénario archivé…).')
        if st.button('Associer cette source',disabled=new is None):
            if not known: raise ValueError('Confirmer la disponibilité à l’avance des variables pour une validation prospective.')
            P['sources']=[s for s in P['sources'] if s['name']!=new['name']]+[new]
            changed('Source associée.'); st.rerun()
    merged=merge_external(daily,P['sources'])
    st.subheader('Jeu de données fusionné')
    st.dataframe(merged.head(100),hide_index=True)
    missing=merged.drop(columns=['date','y']).isna().sum()
    if (missing>0).any(): st.warning('Dates non couvertes dans les imports : '+str(missing[missing>0].to_dict())+'. La période d’entraînement choisie devra être entièrement couverte.')
    st.download_button('Exporter le jeu fusionné',excel_bytes({'Donnees':merged}),'donnees_fusionnees.xlsx')


def train_page():
    st.title('Entraînement & validation')
    daily=need_daily(); saved=P['training_settings']
    with st.form('training'):
        a,b=st.columns(2)
        start=a.date_input('Apprendre depuis',pd.Timestamp(saved.get('start',daily.date.min())).date())
        end=b.date_input('Apprendre jusqu’au',pd.Timestamp(saved.get('end',daily.date.max()-pd.Timedelta(days=60))).date())
        a,b,c=st.columns(3)
        initial=a.number_input('Historique initial (jours)',min_value=30,value=int(saved.get('initial',365)))
        step=b.number_input('Pas entre simulations (jours)',min_value=1,value=int(saved.get('step',90)))
        horizon=c.number_input('Horizon de chaque simulation (jours)',min_value=1,value=int(saved.get('horizon',30)))
        gap=a.number_input('Délai avant le début évalué (jours)',min_value=0,value=int(saved.get('gap',0)))
        holdout=b.number_input('Réserve finale indépendante (jours)',min_value=1,value=int(saved.get('holdout',90)))
        window=c.selectbox('Fenêtre d’apprentissage',['Croissante','Glissante'],index=['Croissante','Glissante'].index(saved.get('window','Croissante')))
        period=a.selectbox('Période des cumuls',['Mois','Semaine','Trimestre','Blocs de N jours'],index=['Mois','Semaine','Trimestre','Blocs de N jours'].index(saved.get('period','Mois')))
        block=b.number_input('N jours par bloc',min_value=1,value=int(saved.get('block',7)))
        objective=c.selectbox('Optimiser quelle lecture ?',['Journalier','Total de période','Cumul progressif'],index=['Journalier','Total de période','Cumul progressif'].index(saved.get('objective','Journalier')))
        metric=a.selectbox('Critère à minimiser',['MAE','RMSE'],index=['MAE','RMSE'].index(saved.get('metric','MAE')))
        budget=b.number_input('Nombre de configurations à tester',min_value=1,max_value=50,value=int(saved.get('budget',5)),help='1 = paramètres manuels ci-dessous. Sinon, ils sont comparés à une recherche aléatoire reproductible.')
        with st.expander('Paramètres XGBoost manuels'):
            params={}
            for k,v in P['params'].items():
                if isinstance(v,int): params[k]=st.number_input(k,min_value=1,value=v,key='param_'+k)
                else: params[k]=st.number_input(k,min_value=0.001,max_value=1.0 if k in ('learning_rate','subsample','colsample_bytree') else 100.,value=float(v),key='param_'+k)
        go=st.form_submit_button('Rechercher, évaluer et entraîner',type='primary')
    if go:
        if start>end or start<daily.date.min().date() or end>daily.date.max().date(): raise ValueError('Choisir une période incluse dans les données récupérées.')
        merged=merge_external(daily,P['sources'])
        df=merged[merged.date.between(pd.Timestamp(start),pd.Timestamp(end))].reset_index(drop=True)
        exog=[c for c in df if c not in ['date','y']]
        protocol=dict(initial=int(initial),step=int(step),horizon=int(horizon),gap=int(gap),holdout=int(holdout),window=window,period=period,block=int(block),objective=objective,metric=metric)
        # Les erreurs de données doivent être signalées avant la recherche coûteuse.
        if df[exog].isna().any().any(): raise ValueError('Les imports complémentaires ne couvrent pas toute la période d’entraînement.')
        pbar=st.progress(0.,text='Recherche et validation chronologique…')
        with st.spinner('Calcul en cours…'):
            bundle,cv,test,trials=train_pipeline(df,P['features'],exog,protocol,params,int(budget),lambda x:pbar.progress(x))
        bundle['target_signature']=target_signature(P)
        bundle['account_config']=deepcopy(P['account_config'])
        bundle['source_specs']=[{k:v for k,v in s.items() if k not in ['data','raw_b64']} for s in P['sources']]
        bundle['training_data']=records(df)
        model_id=bundle['id']; P['models'][model_id]=bundle; P['active_model']=model_id
        P['evaluations'][model_id]={'cv':records(cv),'test':records(test),'trials':records(trials)}
        P['training_settings']={**protocol,'start':str(start),'end':str(end),'budget':int(budget)}
        P['params']=params
        changed('Modèle entraîné et versionné.'); st.rerun()
    if P['active_model']:
        bundle=P['models'][P['active_model']]; evaluation=P['evaluations'][bundle['id']]
        st.subheader('Résultats du modèle '+bundle['id'])
        st.caption(f"Modèle de production réentraîné du {bundle['train_start']} au {bundle['train_end']}. Le test final ci-dessous provient d’un modèle entraîné avant la réserve finale.")
        st.json(bundle['params'],expanded=False)
        choice=st.radio('Résultats à afficher',['Test final indépendant','Validation de sélection'],horizontal=True)
        pred=frame(evaluation['test' if choice=='Test final indépendant' else 'cv'])
        proto=bundle['protocol']
        tables,totals,trajectory=score_tables(pred,proto['period'],proto['block'])
        st.dataframe(tables,hide_index=True)
        st.caption('Biais = réel − prévu. SMAPE : contribution 0 lorsque réel et prévu valent 0. WAPE non défini si tous les réels valent 0. Les totaux partiels restent inclus et sont marqués dans le détail.')
        selected=st.selectbox('Simulation à tracer',pred.fold.unique())
        f=pred[pred.fold==selected]
        st.plotly_chart(chart(f),use_container_width=True)
        t=trajectory[trajectory.fold==selected]
        st.plotly_chart(chart(t,real='real_cum',pred='pred_cum',title='Cumuls remis à zéro à chaque période'),use_container_width=True)
        st.dataframe(totals,hide_index=True)
        st.caption('Bande indicative calibrée sur les résidus de validation de sélection ; couverture mesurée séparément sur le test final : '+', '.join(f'{float(k):.0%} nominal → {v:.1%} observé' for k,v in bundle['coverage_holdout'].items()))
        st.dataframe(pd.DataFrame(evaluation['trials']).sort_values('score'),hide_index=True)
        importance=pd.DataFrame({'Variable':bundle['schema']['columns'],'Importance':bundle['model'].feature_importances_}).sort_values('Importance',ascending=False)
        st.dataframe(importance,hide_index=True)
        st.caption('L’importance décrit le modèle ; elle ne prouve pas une cause des variations de CA.')
        st.download_button('Exporter les résultats de validation',excel_bytes({'Scores':tables,'Prévisions':pred,'Totaux':totals,'Cumuls':trajectory,'Recherche':pd.DataFrame(evaluation['trials']),'Variables':importance}),'validation.xlsx')


def select_model():
    if not P['models']: st.info('Entraîner un modèle au préalable.'); st.stop()
    ids=list(P['models'])
    key=st.selectbox('Version du modèle',ids,index=ids.index(P['active_model']) if P['active_model'] in ids else 0,
        format_func=lambda k:f"{k} · entraîné jusqu’au {P['models'][k]['train_end']}")
    return P['models'][key]


def prediction_page():
    st.title('Prévisions')
    bundle=select_model()
    st.caption('Le calendrier et les fériés sont recréés automatiquement avec les paramètres sauvegardés du modèle.')
    origin=pd.Timestamp(bundle['train_end']); a,b=st.columns(2)
    start=a.date_input('Première date prédite',value=(origin+pd.Timedelta(days=1)).date())
    end=b.date_input('Dernière date prédite',value=(origin+pd.Timedelta(days=30)).date())
    if start<=origin.date() or end<start:
        st.info('Choisir une période après la fin d’apprentissage du modèle.'); return
    depth=history_depth(bundle['cfg'])
    needed_start=origin+pd.Timedelta(days=1) if depth else pd.Timestamp(start)
    if (pd.Timestamp(end)-needed_start).days>3660: raise ValueError('Horizon limité à dix ans.')
    future=pd.DataFrame({'date':pd.date_range(needed_start,end)})
    st.caption(f'Calendrier nécessaire : {needed_start.date()} → {end}. '+('Les jours intermédiaires sont nécessaires aux retards.' if depth else ''))
    complete=True
    if bundle['exog']:
        st.subheader('Variables complémentaires pour les dates prédites')
        for spec in bundle['source_specs']:
            with st.expander('Importer '+spec['name'],expanded=True):
                template=future.copy()
                existing=next((s for s in P['sources'] if s['name']==spec['name']),None)
                if existing: template=template.merge(frame(existing['data']),on='date',how='left')
                for c in spec['columns']:
                    full=spec['name']+'__'+c
                    template[c]=template[full] if full in template else np.nan
                template=template[['date']+spec['columns']].rename(columns={'date':spec['date_col']})
                st.download_button('Télécharger la trame à compléter',excel_bytes({'Donnees':template}),f"prevision_{spec['name']}.xlsx",key='tpl_'+spec['name'])
                imported=import_external('future_'+bundle['id']+'_'+spec['name'],spec)
                if imported is None: complete=False
                else: future=future.merge(frame(imported['data']),on='date',how='left',validate='one_to_one')
        if not complete: st.info('Importer chaque source attendue pour activer la prédiction.')
        elif future[bundle['exog']].isna().any().any():
            complete=False
            st.warning('Dates incomplètes dans les fichiers : aucune prédiction ne sera lancée.')
    level=st.selectbox('Bande indicative',['Sans bande','80 %','95 %'])
    if st.button('Créer et conserver cette prévision',disabled=not complete,type='primary'):
        values=forecast(bundle,future)
        result=future[['date']].copy(); result['pred']=values
        if level!='Sans bande':
            q=bundle['bands']['0.8' if level=='80 %' else '0.95']
            result['lower']=result.pred-q; result['upper']=result.pred+q
        result=result[result.date>=pd.Timestamp(start)]
        run={'id':uuid.uuid4().hex[:12],'model_id':bundle['id'],'created_at':stamp(),'origin':bundle['train_end'],
            'start':str(start),'end':str(end),'band':level,'inputs':records(future),'predictions':records(result),'target_signature':bundle['target_signature']}
        P['runs'].append(run); st.session_state.selected_run=run['id']
        changed('Prévision enregistrée.'); st.rerun()
    if P['runs']:
        run=P['runs'][-1]
        st.subheader('Dernière prévision conservée')
        display_run(run)


def comparison(run):
    df=frame(run['predictions'])
    if P['daily'] and run['target_signature']==target_signature(P):
        df=df.merge(frame(P['daily']).rename(columns={'y':'real'}),on='date',how='left',validate='one_to_one')
    else: df['real']=np.nan
    df['ecart']=df.real-df.pred
    df['ecart_pct']=np.where(np.abs(df.pred)>1e-9,100*df.ecart/np.abs(df.pred),np.nan)
    return df


def display_run(run):
    df=comparison(run)
    st.caption(f"Prévision {run['id']} · origine des données {run['origin']} · créée {run['created_at']} · modèle {run['model_id']}")
    if run['target_signature']!=target_signature(P): st.warning('Les comptes / journaux ont changé depuis cette prévision : comparaison au réel désactivée pour éviter un périmètre incohérent.')
    a,b,c=st.columns(3)
    a.metric('Total prévu',f'{df.pred.sum():,.2f} €')
    observed=df[df.real.notna()]
    b.metric('Réel disponible',f'{observed.real.sum():,.2f} €' if len(observed) else '—')
    c.metric('Écart sur jours comparables',f'{observed.ecart.sum():+,.2f} €' if len(observed) else '—')
    st.plotly_chart(chart(df,lower='lower',upper='upper'),use_container_width=True)
    if run['band']!='Sans bande': st.caption('Bande empirique indicative quotidienne, sans garantie de couverture future ni de couverture du cumul.')
    period=st.selectbox('Cumuls affichés',['Mois','Semaine','Trimestre','Blocs de N jours'],key='period_'+run['id'])
    block=st.number_input('Nombre de jours par bloc',min_value=1,value=7,key='block_'+run['id'])
    from modeling import period_keys
    cum=df.copy(); cum['period']=period_keys(cum.date,period,int(block))
    cum['pred_cum']=cum.groupby('period').pred.cumsum()
    # Après une date inconnue, ne pas afficher un cumul réel artificiellement partiel.
    cum['real_cum']=cum.groupby('period').real.transform(lambda s:s.cumsum(skipna=False))
    st.plotly_chart(chart(cum,real='real_cum',pred='pred_cum',title='CA HT cumulé'),use_container_width=True)
    st.dataframe(df,hide_index=True)
    st.download_button('Exporter prévision, réel et cumuls',excel_bytes({'Quotidien':df,'Cumuls':cum,'Hypotheses':frame(run['inputs'])}),f"prevision_{run['id']}.xlsx",key='export_'+run['id'])
    return df


def followup_page():
    st.title('Écarts & questionnaire')
    if not P['runs']: st.info('Créer une prévision au préalable.'); return
    ids=[r['id'] for r in P['runs']]
    index=ids.index(st.session_state.get('selected_run')) if st.session_state.get('selected_run') in ids else len(ids)-1
    selected=st.selectbox('Prévision à suivre',ids,index=index)
    run=next(r for r in P['runs'] if r['id']==selected)
    df=display_run(run)
    threshold=st.number_input('Écart minimum en euros',min_value=0.,value=100.)
    top=st.number_input('Nombre de dates par classement',min_value=1,max_value=100,value=10)
    a,b=st.columns(2)
    a.subheader('Écarts positifs'); a.dataframe(df[df.ecart>threshold].nlargest(int(top),'ecart')[['date','real','pred','ecart','ecart_pct']],hide_index=True)
    b.subheader('Écarts négatifs'); b.dataframe(df[df.ecart<-threshold].nsmallest(int(top),'ecart')[['date','real','pred','ecart','ecart_pct']],hide_index=True)
    st.subheader('Analyser une date ou une période')
    dates_options=df.date.dt.strftime('%Y-%m-%d').tolist()
    chosen=st.selectbox('Date à analyser',dates_options)
    if st.button('Analyser cette date'):
        st.session_state['note_start']=pd.Timestamp(chosen).date(); st.session_state['note_end']=pd.Timestamp(chosen).date()
    notes=[n for n in P['notes'] if n['run_id']==selected]
    options=['Nouvelle fiche']+[n['id'] for n in notes]
    edit=st.selectbox('Fiche existante à modifier',options,format_func=lambda v: v if v=='Nouvelle fiche' else next(n['start']+' → '+n['end']+' · '+n['status'] for n in notes if n['id']==v))
    existing=next((n for n in notes if n['id']==edit),None)
    default=existing or {'start':chosen,'end':chosen,'causes':[],'comment':'','action':'','status':'À analyser','complete':True,'closed':False}
    with st.form('questionnaire_'+edit):
        a,b=st.columns(2)
        begin=a.date_input('Début de la fiche',value=pd.Timestamp(default['start']).date(),key='note_start' if not existing else 'edit_start_'+edit)
        end=b.date_input('Fin de la fiche',value=pd.Timestamp(default['end']).date(),key='note_end' if not existing else 'edit_end_'+edit)
        complete=st.checkbox('Données comptables vérifiées',value=default['complete'])
        closed=st.checkbox('Fermeture / horaires réduits',value=default['closed'])
        causes=st.multiselect('Causes supposées',['Promotion','Événement local','Météo','Personnel','Rupture / approvisionnement','Saisie comptable','Autre'],default=default['causes'])
        comment=st.text_area('Explication / constat',value=default['comment'])
        action=st.text_area('Action décidée',value=default['action'])
        status=st.selectbox('Statut',['À analyser','En cours','Expliqué','Action terminée'],index=['À analyser','En cours','Expliqué','Action terminée'].index(default['status']))
        if st.form_submit_button('Enregistrer la fiche',type='primary'):
            if begin>end: raise ValueError('Dates de fiche inversées.')
            part=df[df.date.between(pd.Timestamp(begin),pd.Timestamp(end))]
            if part.empty: raise ValueError('Choisir une période incluse dans la prévision.')
            if begin<df.date.min().date() or end>df.date.max().date(): raise ValueError('La fiche doit rester dans la période prévue.')
            note={'id':existing['id'] if existing else uuid.uuid4().hex[:12],'run_id':selected,'model_id':run['model_id'],'start':str(begin),'end':str(end),
                'complete':complete,'closed':closed,'causes':causes,'comment':comment,'action':action,'status':status,'updated_at':stamp(),
                'created_at':existing['created_at'] if existing else stamp(),'gap_at_save':float(part.ecart.sum()) if part.real.notna().any() else None,
                'observed_days_at_save':int(part.real.notna().sum())}
            P['notes']=[n for n in P['notes'] if n['id']!=note['id']]+[note]
            changed('Fiche enregistrée.'); st.rerun()
    st.caption('Les réponses expliquent les écarts ; elles ne modifient ni le modèle ni les prévisions déjà émises.')
    if notes:
        export=pd.DataFrame(notes); export['causes']=export.causes.map(lambda x:', '.join(x))
        st.dataframe(export,hide_index=True)
        st.download_button('Exporter les fiches',excel_bytes({'Questionnaires':export}),'questionnaires.xlsx')


def project_page():
    st.title('Projet & sauvegardes')
    name=st.text_input('Nom du dossier',value=P['name'])
    if st.button('Enregistrer le nom'):
        P['name']=name; changed()
    st.download_button('Télécharger le projet complet',dump_project(P),'projet_previsions.zip',mime='application/zip',type='primary')
    st.caption('Contient les données, imports originaux, paramètres, modèles, tests, prévisions et questionnaires. Aucun secret. Ce ZIP sert à reprendre le travail ; il est distinct du ZIP du code de l’application.')
    uploaded=st.file_uploader('Restaurer un projet exporté',type=['zip'])
    confirm=st.checkbox('Remplacer le projet de cette session par le fichier importé')
    if st.button('Restaurer le projet',disabled=uploaded is None or not confirm):
        restored=load_project(uploaded.getvalue())
        reset_project(restored)
    backend=cloud()
    if backend:
        st.success('Stockage distant configuré. Les actions d’enregistrement sauvegardent automatiquement le projet.')
        if st.button('Sauvegarder maintenant'):
            if st.session_state.get('cloud_blocked'): raise ValueError('Recharger la version distante après avoir exporté votre ZIP ; la sauvegarde est bloquée pour éviter un conflit.')
            changed('Projet enregistré.')
        if st.button('Recharger la version distante'):
            remote,version=backend.load()
            if remote is None: raise ValueError('Aucun projet distant enregistré.')
            st.session_state.cloud_version=version; st.session_state.cloud_blocked=False
            reset_project(remote,dirty=False)
    else:
        st.info('Mode portable : télécharger le projet ZIP avant de fermer la session. Pour retrouver automatiquement les données, configurer Supabase en suivant le README.')
    if P['models']:
        st.subheader('Versions conservées')
        st.dataframe(pd.DataFrame([{k:b[k] for k in ['id','train_start','train_end','created_at','fingerprint']} for b in P['models'].values()]),hide_index=True)
    st.subheader('Rappel des règles')
    st.write('Les jours sans écritures valent zéro. Les variables complémentaires doivent couvrir toutes les dates requises. Actualiser les données ne réentraîne pas le modèle. Une ancienne prévision reste inchangée ; seul le réel comparé évolue.')


def reset_project(project,dirty=True):
    keep={k:st.session_state[k] for k in ['authenticated','cloud_version','cloud_blocked'] if k in st.session_state}
    st.session_state.clear(); st.session_state.update(keep)
    st.session_state.project=project; st.session_state.initialized=True; st.session_state.dirty=dirty
    st.rerun()


authenticate()
if not st.session_state.get('initialized'):
    st.session_state.project=new_project()
    st.session_state.cloud_version=None
    try:
        store=cloud()
        if store:
            remote,version=store.load()
            if remote is not None: st.session_state.project=remote
            st.session_state.cloud_version=version
    except ValueError as e:
        st.session_state.cloud_blocked=True; st.session_state.cloud_error=str(e)
    st.session_state.initialized=True
P=st.session_state.project
st.sidebar.title('Prévisions CA HT')
st.sidebar.caption(P['name'])
page=st.sidebar.radio('Parcours',['Données & comptes','Variables','Entraînement','Prévisions','Écarts & questionnaire','Projet & sauvegardes'])
st.sidebar.caption(f"{len(P['daily'])} jours · {len(P['models'])} modèles · {len(P['runs'])} prévisions")
if st.session_state.get('cloud_error'): st.sidebar.error(st.session_state.cloud_error)
if st.session_state.get('dirty'): st.sidebar.warning('Modifications à sauvegarder : exporter le projet ou utiliser le stockage distant.')
if not cloud(): st.sidebar.caption('Mode portable · export ZIP nécessaire pour conserver le travail.')
try:
    {'Données & comptes':source_page,'Variables':variables_page,'Entraînement':train_page,'Prévisions':prediction_page,'Écarts & questionnaire':followup_page,'Projet & sauvegardes':project_page}[page]()
except (ValueError,KeyError,TypeError) as e:
    st.error(str(e))
except Exception:
    st.error('Une opération a échoué. Vos données en session sont conservées : exportez le projet depuis Projet & sauvegardes. Vérifiez le format du fichier ou la connexion.')
