import pytest
from app.policy import education
from app.policy.permissions import effective_policy
from app.models import User, Role, AgeGroup

@pytest.mark.parametrize('limit', [20, 150, 0])
def test_explicit_child_message_limit_overrides_age_profile(monkeypatch, limit):
    monkeypatch.setattr(education, 'rules', lambda: {'profiles': education.DEFAULTS})
    user = User(role=Role.child, age_group=AgeGroup.kid, policy={'daily_message_limit': limit}, apps=[])
    assert effective_policy(user)['daily_message_limit'] == limit

@pytest.mark.parametrize('limit', [60, 180])
def test_child_without_override_inherits_configured_profile(monkeypatch, limit):
    profiles = {**education.DEFAULTS, 'kid': {**education.DEFAULTS['kid'], 'daily_message_limit': limit}}
    monkeypatch.setattr(education, 'rules', lambda: {'profiles': profiles})
    user = User(role=Role.child, age_group=AgeGroup.kid, policy={}, apps=[])
    assert effective_policy(user)['daily_message_limit'] == limit

@pytest.mark.asyncio
async def test_admin_limit_survives_save_reload_and_child_session(admin, kid):
    members = (await admin.get('/api/admin/members')).json()
    child = next(m for m in members if m['username'] == 'kid')
    original = child.get('policy') or {}
    uid = child['id']
    try:
        for limit in (150, 20, 0):
            response = await admin.patch('/api/admin/members/' + uid, json={'policy': {**original, 'daily_message_limit': limit}})
            assert response.status_code == 200, response.text
            assert response.json()['effective_policy']['daily_message_limit'] == limit
            reloaded = next(m for m in (await admin.get('/api/admin/members')).json() if m['id'] == uid)
            assert reloaded['policy']['daily_message_limit'] == limit
            assert reloaded['effective_policy']['daily_message_limit'] == limit
            assert (await kid.get('/api/auth/me')).json()['user']['effective_policy']['daily_message_limit'] == limit
        denied = await kid.patch('/api/admin/members/' + uid, json={'policy': {'daily_message_limit': 0}})
        assert denied.status_code == 403
    finally:
        await admin.patch('/api/admin/members/' + uid, json={'policy': original})
