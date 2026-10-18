import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from streamlit.testing.v1 import AppTest


def test_complete_demo_journey():
    at=AppTest.from_file(str(ROOT/'app.py'),default_timeout=120).run()
    assert not at.exception
    next(r for r in at.radio if r.label=='Source').set_value('Démonstration').run()
    next(b for b in at.button if b.label=='Charger la démonstration').click().run()
    assert not at.exception
    assert not at.error, [x.value for x in at.error]
    assert len(at.session_state.project['daily'])>1300
    at.sidebar.radio[0].set_value('Variables').run()
    assert not at.exception
    next(b for b in at.button if b.label=='Enregistrer les variables').click().run()
    at.sidebar.radio[0].set_value('Entraînement').run()
    next(n for n in at.number_input if n.label=='Nombre de configurations à tester').set_value(1)
    next(n for n in at.number_input if n.label=='n_estimators').set_value(10)
    next(b for b in at.button if b.label=='Rechercher, évaluer et entraîner').click().run(timeout=120)
    assert not at.exception
    assert not at.error, [x.value for x in at.error]
    assert len(at.session_state.project['models'])==1
    at.sidebar.radio[0].set_value('Prévisions').run()
    next(b for b in at.button if b.label=='Créer et conserver cette prévision').click().run()
    assert not at.exception
    assert not at.error,[x.value for x in at.error]
    assert len(at.session_state.project['runs'])==1
    original=at.session_state.project['runs'][0]['predictions']
    at.sidebar.radio[0].set_value('Écarts & questionnaire').run()
    next(b for b in at.button if b.label=='Analyser cette date').click().run()
    next(t for t in at.text_area if t.label=='Explication / constat').set_value('Test de suivi')
    next(b for b in at.button if b.label=='Enregistrer la fiche').click().run()
    assert not at.exception
    assert not at.error,[x.value for x in at.error]
    assert len(at.session_state.project['notes'])==1
    assert at.session_state.project['runs'][0]['predictions']==original
    at.sidebar.radio[0].set_value('Projet & sauvegardes').run()
    assert not at.exception
    assert not at.error,[x.value for x in at.error]


def test_password_gate():
    at=AppTest.from_file(str(ROOT/'app.py'))
    at.secrets['APP_PASSWORD']='correct-password'
    at.run()
    assert len(at.sidebar.radio)==0
    at.text_input[0].set_value('wrong')
    at.button[0].click().run()
    assert at.error
    at.text_input[0].set_value('correct-password')
    at.button[0].click().run()
    assert not at.exception
    assert len(at.sidebar.radio)==1
