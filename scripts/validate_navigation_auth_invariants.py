#!/usr/bin/env python3
"""Fail deployment if TalentX Home/login routing invariants regress."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


home = text("assets/account-home-routing.js")
client = text("assets/client-stability.js")
auth = text("assets/supabase-auth-sync.js")

# Home must be recognized in every current desktop/mobile representation.
assert 'button[data-route="dashboard"],button[data-mobile-route="dashboard"]' in home
assert 'if(next===\'dashboard\') markDashboardIntent();' in home
assert 'window.__talentxDashboardIntent=true;' in home
assert '.mobile-brand' in home, "Mobile TalentX logo must remain a Welcome/logo target"

# Independent safety net in the late-loaded client layer.
assert 'button[data-route="dashboard"],button[data-mobile-route="dashboard"]' in client
assert "window.__talentxDashboardIntent=true;" in client
assert "window.talentxGoDashboard" in client

# Login may wait for Supabase authentication itself, but must not wait for the
# subsequent account-state hydration before returning to the UI.
login_start = auth.index("async login({email,password})")
login_end = auth.index("async signup({email,password,name})")
login_block = auth[login_start:login_end]
assert "await client.auth.signInWithPassword" in login_block
assert "await loadCloudState" not in login_block, "Login must not block on portfolio hydration"
assert "loadCloudState(data.user.id).catch" in login_block

submit_start = auth.index("window.submitTalentxAuth=async function(mode)")
submit_end = auth.index("window.talentxAuthPlaceholder=async function(feature)")
submit_block = auth[submit_start:submit_end]
assert "talentxGoDashboard" in submit_block or "go('dashboard')" in submit_block
assert "go('market')" not in submit_block, "Successful login must route to Home/dashboard, not Market"

# Critical-path bootstrap must expose the auth adapter before optional account UI.
assert "supabase-auth-sync.js?v=20260914-fast-login-1" in client
assert client.index("supabase-auth-sync.js?v=20260914-fast-login-1") < client.index("password-requirements.js")
assert client.index("settleAuthReady('ready')") < client.index("password-requirements.js")

print("TalentX navigation/auth invariants passed: Home=>Dashboard, logo=>Welcome, login=>Dashboard without blocking cloud hydration.")
