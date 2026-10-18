from io import BytesIO
import base64
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from data import read_table,prepare_external,records,frame


def import_external(key,spec=None):
    """Même interface à l'apprentissage et à la prévision, schéma verrouillé en prévision."""
    f=st.file_uploader('Fichier Excel ou CSV',type=['csv','txt','xlsx','xlsm'],key=key+'_file')
    if f is None: return None
    raw=f.getvalue()
    sheet=0
    if f.name.lower().endswith(('.xlsx','.xlsm')):
        names=pd.ExcelFile(BytesIO(raw)).sheet_names
        sheet=st.selectbox('Onglet',names,key=key+'_sheet')
    df=read_table(raw,f.name,sheet)
    st.dataframe(df.head(5),hide_index=True)
    if df.empty: raise ValueError('Fichier vide.')
    if spec:
        st.caption('Variables attendues : '+', '.join(spec['columns']))
        cols=spec['columns']; date_col=spec['date_col']; name=spec['name']; agg=spec['aggregation']; kinds=spec['kinds']
        # Autoriser un intitulé de date différent dans le fichier de prévision.
        date_col=st.selectbox('Colonne date',list(df.columns),index=list(df.columns).index(date_col) if date_col in df else 0,key=key+'_date')
        missing=set(cols)-set(df.columns)
        if missing: raise ValueError('Colonnes absentes : '+', '.join(sorted(missing)))
    else:
        name=st.text_input('Nom court de cette source',value='source',key=key+'_name').strip()
        if not name or not name.replace('_','').isalnum(): raise ValueError('Nom de source : lettres, chiffres et _ uniquement.')
        date_col=st.selectbox('Colonne date',list(df.columns),key=key+'_date')
        available=[c for c in df.columns if c!=date_col]
        cols=st.multiselect('Variables à utiliser',available,default=available,key=key+'_cols')
        agg=st.selectbox('Plusieurs lignes par date : calcul pour les nombres',['sum','mean','last','max','min'],format_func=lambda s:{'sum':'Somme','mean':'Moyenne','last':'Dernière valeur','max':'Maximum','min':'Minimum'}[s],key=key+'_agg')
        kinds={}
        for c in cols:
            kinds[c]=st.selectbox(f'Type de {c}',['Nombre','Catégorie'],key=key+'_type_'+str(c))
        st.caption('Les catégories utilisent la dernière valeur en cas de doublon de date. Toutes les variables importées doivent être connues à l’origine des prévisions, y compris dans les tests historiques.')
    if not cols: return None
    prepared=prepare_external(df,date_col,cols,agg,name,kinds)
    st.caption(f'{len(df)} lignes → {len(prepared)} dates uniques.')
    return {'name':name,'date_col':date_col,'columns':cols,'aggregation':agg,'kinds':kinds,
        'data':records(prepared),'filename':f.name,'raw_b64':base64.b64encode(raw).decode(),'sheet':sheet}


def excel_bytes(sheets):
    buf=BytesIO()
    with pd.ExcelWriter(buf,engine='xlsxwriter',engine_kwargs={'options':{'strings_to_formulas':False,'strings_to_urls':False}}) as w:
        for name,df in sheets.items():
            df.to_excel(w,sheet_name=name[:31],index=False)
            ws=w.sheets[name[:31]]; ws.freeze_panes(1,0)
            if len(df) and len(df.columns): ws.autofilter(0,0,len(df),len(df.columns)-1)
            ws.set_column(0,max(0,len(df.columns)-1),20)
    return buf.getvalue()


def chart(df,real='real',pred='pred',title='CA HT quotidien',lower=None,upper=None):
    fig=go.Figure()
    if lower and upper and lower in df and upper in df:
        fig.add_trace(go.Scatter(x=df.date,y=df[upper],mode='lines',line={'width':0},showlegend=False))
        fig.add_trace(go.Scatter(x=df.date,y=df[lower],mode='lines',line={'width':0},fill='tonexty',fillcolor='rgba(8,127,140,.13)',name='Bande indicative'))
    if real in df:
        fig.add_trace(go.Scatter(x=df.date,y=df[real],name='Réel',line={'color':'#16324F'},connectgaps=False))
    fig.add_trace(go.Scatter(x=df.date,y=df[pred],name='Prévision',line={'color':'#087F8C'},connectgaps=False))
    fig.update_layout(title=title,template='plotly_white',height=400,hovermode='x unified',yaxis_title='CA HT (€)',legend={'orientation':'h'},margin={'l':20,'r':20,'t':55,'b':20})
    return fig
