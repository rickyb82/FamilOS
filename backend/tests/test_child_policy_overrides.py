import pytest
from app.models import User, Role, AgeGroup
from app.policy import education
from app.policy.permissions import effective_policy, tool_allowed

@pytest.mark.parametrize('tools', [['mcp', 'artifacts', 'memory'], []])
@pytest.mark.parametrize('windows', [[], [{'days': [0, 2], 'start': '15:00', 'end': '23:00'}]])
def test_individual_choices_override_global(monkeypatch, tools, windows):
    monkeypatch.setattr(education, 'rules', lambda: {'profiles': education.DEFAULTS})
    child = User(role=Role.child, age_group=AgeGroup.kid, apps=None,
                 policy={'allowed_tools': tools, 'time_windows': windows, 'daily_image_limit': 12})
    policy = effective_policy(child)
    assert policy['allowed_tools'] == tools
    assert policy['time_windows'] == windows
    assert policy['daily_image_limit'] == 12
    assert tool_allowed(policy, 'mcp__example__lookup') == ('mcp' in tools)
    other = effective_policy(User(role=Role.child, age_group=AgeGroup.kid, apps=None, policy={}))
    assert 'mcp' not in other['allowed_tools']
    assert other['time_windows'][0]['end'] == '20:30'

async def test_admin_changes_survive_reload_and_reach_child(admin, kid):
    members = (await admin.get('/api/admin/members')).json()
    child = next(m for m in members if m['username'] == 'kid')
    original = child.get('policy') or {}
    url = '/api/admin/members/' + child['id']
    custom = {**original, 'allowed_tools': ['mcp', 'artifacts'], 'time_windows': []}
    try:
        response = await admin.patch(url, json={'policy': custom})
        assert response.status_code == 200
        actual = (await kid.get('/api/auth/me')).json()['user']['effective_policy']
        assert actual['allowed_tools'] == custom['allowed_tools']
        assert actual['time_windows'] == []
        reloaded = next(m for m in (await admin.get('/api/admin/members')).json() if m['id'] == child['id'])
        assert reloaded['policy'] == custom
        assert (await kid.patch(url, json={'policy': {}})).status_code == 403
    finally:
        await admin.patch(url, json={'policy': original})
