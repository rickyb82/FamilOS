from app.models import User, Role
from app.services import model_catalog as catalog

def test_admin_default_only_applies_without_explicit_selection(monkeypatch):
    providers=[{'id':'openai','default_model':'first','models':[{'id':'first'},{'id':'second'}]}]
    monkeypatch.setattr(catalog,'available_providers',lambda:providers)
    user=User(role=Role.admin,prefs={'default_chat_model':'openai/second'},policy={})
    policy={'allowed_providers':['openai']}
    assert catalog.catalog(user,policy)[0]['default_model']=='second'
    assert catalog.coerce(user,policy,None,None)==('openai','second')
    assert catalog.coerce(user,policy,'openai','first')==('openai','first')
    assert providers[0]['default_model']=='first'
    other=User(role=Role.admin,prefs={},policy={})
    assert catalog.coerce(other,policy,None,None)==('openai','first')
    user.prefs={'default_chat_model':'openai/removed'}
    assert catalog.coerce(user,policy,None,None)==('openai','first')
    user.policy={'locked_model':'openai/first'}
    user.prefs={'default_chat_model':'openai/second'}
    assert catalog.coerce(user,policy,None,None)==('openai','first')
