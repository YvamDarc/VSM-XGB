"""Archives portables sans pickle ; persistance Supabase avec verrou optimiste."""
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
from copy import deepcopy
import json
import base64
import re
import requests
from xgboost import XGBRegressor
from modeling import DEFAULT_FEATURES, DEFAULT_PARAMS, stamp


def new_project(name='Mon dossier'):
    return {'format_version':1, 'name':name, 'created_at':stamp(), 'lines':[], 'daily':[], 'controls':[],
        'account_config':{'prefixes':'70','exact':'','ranges':'','exclude':'','excluded_journals':[]},
        'source':{}, 'sources':[], 'features':deepcopy(DEFAULT_FEATURES), 'params':deepcopy(DEFAULT_PARAMS),
        'models':{}, 'active_model':None, 'runs':[], 'notes':[], 'evaluations':{}, 'training_settings':{}}


def dump_project(project):
    meta={k:v for k,v in project.items() if k!='models'}
    meta['models']={}
    buf=BytesIO()
    with ZipFile(buf,'w',ZIP_DEFLATED) as z:
        for key,bundle in project['models'].items():
            if not re.fullmatch('[a-zA-Z0-9_-]+',key): raise ValueError('Identifiant modèle invalide.')
            meta['models'][key]={k:v for k,v in bundle.items() if k!='model'}
            z.writestr(f'models/{key}.ubj',bytes(bundle['model'].get_booster().save_raw(raw_format='ubj')))
        z.writestr('project.json',json.dumps(meta,ensure_ascii=False,allow_nan=False))
    return buf.getvalue()


def load_project(raw):
    with ZipFile(BytesIO(raw)) as z:
        if sum(i.file_size for i in z.infolist())>200*1024**2: raise ValueError('Archive décompressée supérieure à 200 Mo.')
        p=json.loads(z.read('project.json'))
        if p.get('format_version')!=1: raise ValueError('Version de projet non prise en charge.')
        required=set(new_project())
        if not required.issubset(p): raise ValueError('Projet incomplet.')
        p={k:p[k] for k in required}
        for key,bundle in p['models'].items():
            if not re.fullmatch('[a-zA-Z0-9_-]+',key): raise ValueError('Identifiant modèle invalide.')
            model=XGBRegressor()
            model.load_model(bytearray(z.read(f'models/{key}.ubj')))
            bundle['model']=model
    return p


class CloudStore:
    def __init__(self,url,key,project_id):
        if not url.startswith('https://'): raise ValueError('SUPABASE_URL doit utiliser HTTPS.')
        self.url=url.rstrip('/')+'/rest/v1/forecast_projects'
        self.headers={'apikey':key,'Content-Type':'application/json','Prefer':'return=representation'}
        if not key.startswith('sb_secret_'):
            self.headers['Authorization']=f'Bearer {key}'
        self.project_id=project_id

    def load(self):
        try: r=requests.get(self.url,headers=self.headers,params={'project_id':'eq.'+self.project_id,'select':'version,payload'},timeout=60)
        except requests.RequestException: raise ValueError('Stockage distant inaccessible.') from None
        if r.status_code!=200: raise ValueError(f'Lecture du stockage impossible (HTTP {r.status_code}).')
        rows=r.json()
        if not rows: return None,None
        return load_project(base64.b64decode(rows[0]['payload'],validate=True)),rows[0]['version']

    def save(self,project,version):
        raw=dump_project(project)
        payload=base64.b64encode(raw).decode()
        row={'project_id':self.project_id,'payload':payload,'version':1 if version is None else version+1,'updated_at':stamp()}
        try:
            if version is None:
                r=requests.post(self.url,headers=self.headers,json=row,timeout=90)
            else:
                r=requests.patch(self.url,headers=self.headers,params={'project_id':'eq.'+self.project_id,'version':'eq.'+str(version)},json=row,timeout=90)
        except requests.RequestException:
            raise ValueError('Sauvegarde distante non confirmée. Exporter le ZIP puis recharger le projet distant avant de réessayer.') from None
        if r.status_code==409 or (r.status_code==200 and not r.json()):
            raise ValueError('Le projet a été modifié dans une autre session. Exporter votre travail puis recharger la version distante.')
        if r.status_code not in (200,201): raise ValueError(f'Échec sauvegarde distante (HTTP {r.status_code}). Exporter votre projet ZIP.')
        return row['version']
